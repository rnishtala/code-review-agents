"""Draft targeted PR review comments from review findings, iterate, and submit as a draft.

This backs the Streamlit UI but is deliberately UI-free and offline-testable. Three concerns:

  1. **Line mapping** (pure, deterministic): parse a unified diff and anchor each
     :class:`~code_review_agents.state.Finding` to a concrete ``(path, line)`` on the diff's
     RIGHT (head) side. Findings carry only free-text locations, so this is best-effort;
     anything we can't anchor degrades to a file-level comment.
  2. **Drafting agent** (conversational): given the current drafts plus a natural-language
     instruction, return an updated set. Mirrors ``agents/base.py`` — structured output with a
     tolerant fallback — and never raises, so the UI stays alive.
  3. **GitHub submit**: build and POST a *pending* (unsubmitted) review. Omitting the
     ``event`` field is what leaves it pending, so nothing is ever auto-published — the user
     finalizes it on github.com.

Nothing here ever submits a review automatically; ``submit_pending_review`` only ever creates
a pending one, and it is only called from the UI's explicit button.
"""

from __future__ import annotations

import json
import re
import uuid
from dataclasses import dataclass, field
from typing import Literal

import requests
from pydantic import BaseModel, Field

from .diff_input import GITHUB_API, _headers
from .llm import make_llm
from .state import Finding, Severity


# --- Models ---------------------------------------------------------------


class DraftComment(BaseModel):
    """One drafted PR comment. The UI renders and edits these; models are the source of truth."""

    id: str = Field(default_factory=lambda: uuid.uuid4().hex)
    source_finding_id: str = Field(
        default="", description="f'{agent}:{index}' of the originating finding; '' if merged/new."
    )
    path: str = Field(default="", description="Repo-relative file path.")
    line: int | None = Field(
        default=None, description="New-file (RIGHT) line number; None => file-level comment."
    )
    side: Literal["RIGHT", "LEFT"] = "RIGHT"
    body: str = Field(default="", description="Markdown comment text shown to the user.")
    severity: Severity = "info"
    included: bool = Field(default=True, description="User include/exclude toggle.")
    anchored: bool = Field(default=True, description="False => mapping failed, file-level fallback.")
    mapping_note: str = Field(default="", description="Human hint about how the line was chosen.")


class DraftResult(BaseModel):
    """Return type of the drafting agent: the full updated draft set plus an action + message."""

    action: Literal["update", "abort", "noop"] = "update"
    comments: list[DraftComment] = Field(default_factory=list)
    message: str = ""


# --- Diff parsing & line mapping (pure, deterministic) --------------------


@dataclass
class DiffHunk:
    header: str  # raw "@@ -a,b +c,d @@" line
    new_start: int
    # (new_line_number_or_None, is_added, text_without_prefix)
    entries: list[tuple[int | None, bool, str]] = field(default_factory=list)
    added_lines: list[int] = field(default_factory=list)
    all_new_lines: list[int] = field(default_factory=list)  # added + context: valid RIGHT anchors


@dataclass
class DiffFile:
    path: str
    hunks: list[DiffHunk] = field(default_factory=list)

    def valid_lines(self) -> set[int]:
        lines: set[int] = set()
        for h in self.hunks:
            lines.update(h.all_new_lines)
        return lines

    def added_lines(self) -> list[int]:
        out: list[int] = []
        for h in self.hunks:
            out.extend(h.added_lines)
        return out


@dataclass
class AnchorCandidate:
    path: str
    line: int | None
    anchored: bool
    note: str


_HUNK_RE = re.compile(r"@@ -\d+(?:,\d+)? \+(?P<start>\d+)(?:,\d+)? @@")
_NEW_PATH_RE = re.compile(r"^\+\+\+ (?:b/)?(.+)$")


def parse_diff(diff: str) -> list[DiffFile]:
    """Parse a unified diff into files with hunks carrying computed RIGHT-side line numbers."""
    files: list[DiffFile] = []
    current_file: DiffFile | None = None
    current_hunk: DiffHunk | None = None
    new_line = 0

    for raw in diff.splitlines():
        if raw.startswith("diff --git "):
            current_file = None
            current_hunk = None
            continue
        if raw.startswith("+++ "):
            m = _NEW_PATH_RE.match(raw)
            path = m.group(1).strip() if m else ""
            if path in ("", "/dev/null"):
                current_file = None  # deletion / unparseable: nothing to anchor to
            else:
                current_file = DiffFile(path=path)
                files.append(current_file)
            current_hunk = None
            continue
        if raw.startswith("---"):
            continue  # old-path line; ignore
        if raw.startswith("@@"):
            m = _HUNK_RE.search(raw)
            if not m or current_file is None:
                current_hunk = None
                continue
            new_line = int(m.group("start"))
            # Keep just the "@@ ... @@" header, dropping any trailing section heading.
            header = raw[: raw.rfind("@@") + 2]
            current_hunk = DiffHunk(header=header, new_start=new_line)
            current_file.hunks.append(current_hunk)
            continue
        if current_hunk is None:
            continue
        # Hunk body.
        if raw.startswith("+") and not raw.startswith("+++"):
            text = raw[1:]
            current_hunk.entries.append((new_line, True, text))
            current_hunk.added_lines.append(new_line)
            current_hunk.all_new_lines.append(new_line)
            new_line += 1
        elif raw.startswith("-") and not raw.startswith("---"):
            current_hunk.entries.append((None, False, raw[1:]))  # removed: no RIGHT line
        elif raw.startswith(" ") or raw == "":
            # Context line. Real diffs prefix blank context lines with a space, but some
            # hand-authored diffs leave them empty; treat "" inside a hunk as blank context
            # (new file boundaries — "diff --git"/"@@"/"+++" — are caught above).
            text = raw[1:] if raw.startswith(" ") else ""
            current_hunk.entries.append((new_line, False, text))
            current_hunk.all_new_lines.append(new_line)
            new_line += 1
        elif raw.startswith("\\"):
            continue  # "\ No newline at end of file"
        else:
            current_hunk = None  # unknown line ends the hunk

    return files


def _path_tokens(text: str) -> list[str]:
    """Pull plausible file-path tokens out of free text (split on separators)."""
    tokens = re.split(r"[\s,;:()`'\"]+", text)
    return [t for t in tokens if ("/" in t or re.search(r"\.[a-zA-Z0-9]{1,5}$", t))]


def _match_file(finding: Finding, files: list[DiffFile]) -> DiffFile | None:
    """Match a finding to a diff file by path token, then basename/suffix, first-in-diff wins."""
    haystacks = [finding.location, finding.title, finding.description]
    tokens = [t for h in haystacks for t in _path_tokens(h)]
    if not tokens:
        return None
    paths = [f.path for f in files]
    # Exact, then suffix either direction, in token then file order.
    for token in tokens:
        for f in files:
            if f.path == token:
                return f
    for token in tokens:
        for f in files:
            if f.path.endswith(token) or token.endswith(f.path):
                return f
    # Basename match.
    for token in tokens:
        base = token.rsplit("/", 1)[-1]
        for f in files:
            if f.path.rsplit("/", 1)[-1] == base:
                return f
    _ = paths
    return None


def _explicit_line(location: str) -> int | None:
    """A line number the model put in the location, e.g. 'tests/x.py:216' or 'x.py:L216-220'."""
    if ":" not in location:
        return None
    suffix = location.rsplit(":", 1)[1].strip().lstrip("Ll")
    head = suffix.split("-")[0].split(",")[0].strip()
    return int(head) if head.isdigit() else None


def _snap_to_valid(line: int, f: DiffFile) -> tuple[int | None, str]:
    """Snap an arbitrary line to the nearest line that actually appears in the file's diff."""
    valid = f.valid_lines()
    if not valid:
        return None, "no commentable line in diff"
    if line in valid:
        return line, f"explicit line {line}"
    nearest = min(valid, key=lambda v: (abs(v - line), v))
    return nearest, f"snapped to nearest diff line {nearest} (location said {line})"


_BACKTICK_RE = re.compile(r"`([^`]+)`")
_TOKEN_RE = re.compile(r"[A-Za-z_][A-Za-z0-9_]{2,}")
_STOPWORDS = {
    "the", "and", "for", "this", "that", "with", "not", "function", "method",
    "class", "line", "code", "test", "tests", "value", "error", "check", "missing",
}


def _identifiers(symbol: str, *prose: str) -> list[str]:
    """Identifiers to match against diff lines, most-specific first.

    The location ``symbol`` (what the model explicitly named as the spot) contributes ALL its
    tokens — even plain lowercase names like ``average`` — since it's the designated locus.
    Prose (title/description) only contributes *code-shaped* tokens (``_`` / interior capital)
    or backticked snippets, so ordinary words don't mis-anchor. Stopwords are dropped.
    """
    seen: set[str] = set()
    out: list[str] = []

    def add(tok: str) -> None:
        if len(tok) >= 3 and tok.lower() not in _STOPWORDS and tok not in seen:
            seen.add(tok)
            out.append(tok)

    for tok in _TOKEN_RE.findall(symbol):  # designated location: take all tokens
        add(tok)
    for text in prose:  # backticked snippets are explicit code references
        for m in _BACKTICK_RE.finditer(text):
            for tok in _TOKEN_RE.findall(m.group(1)):
                add(tok)
    for text in prose:  # bare prose tokens only if they look like code
        for tok in _TOKEN_RE.findall(text):
            if "_" in tok or re.search(r"[a-z][A-Z]", tok):
                add(tok)
    return out


def _line_by_identifiers(f: DiffFile, idents: list[str]) -> tuple[int | None, str]:
    """Find a line whose code contains one of the identifiers — added lines win over context."""
    for prefer_added in (True, False):
        for ident in idents:
            for h in f.hunks:
                for (ln, is_add, text) in h.entries:
                    if ln is None or (prefer_added and not is_add):
                        continue
                    if ident in text:
                        where = "added" if is_add else "context"
                        return ln, f"matched '{ident}' on {where} line"
    return None, ""


def map_finding(finding: Finding, files: list[DiffFile]) -> AnchorCandidate:
    """Best-effort anchor of a finding to (path, RIGHT line). Degrades to file-level / unmatched.

    Order: (1) an explicit line number in the location, snapped to the nearest real diff line;
    (2) a code identifier from the location/title/description matched against diff lines;
    (3) the first changed line. (1) and (2) keep distinct findings on distinct lines instead
    of collapsing them all onto the first hunk.
    """
    f = _match_file(finding, files)
    if f is None:
        return AnchorCandidate(path="", line=None, anchored=False, note="no matching file in diff")
    if not f.valid_lines():
        return AnchorCandidate(path=f.path, line=None, anchored=False, note="deletion-only; file-level comment")

    # 1. Explicit line number in the location.
    explicit = _explicit_line(finding.location)
    if explicit is not None:
        line, note = _snap_to_valid(explicit, f)
        if line is not None:
            return AnchorCandidate(path=f.path, line=line, anchored=True, note=note)

    # 2. Code identifier match (location symbol, then title, then description).
    symbol = finding.location.rsplit(":", 1)[1] if ":" in finding.location else ""
    idents = _identifiers(symbol, finding.title, finding.description)
    line, note = _line_by_identifiers(f, idents)
    if line is not None:
        return AnchorCandidate(path=f.path, line=line, anchored=True, note=note)

    # 3. First changed line.
    added = f.added_lines()
    if added:
        return AnchorCandidate(path=f.path, line=added[0], anchored=True, note="first changed line")
    first = sorted(f.valid_lines())[0]
    return AnchorCandidate(path=f.path, line=first, anchored=True, note="first diff line")


def _seed_body(finding: Finding) -> str:
    parts = [finding.title.strip()]
    if finding.description.strip():
        parts.append(finding.description.strip())
    if finding.suggestion.strip():
        parts.append(f"**Suggestion:** {finding.suggestion.strip()}")
    tag = f"_{finding.severity}"
    if finding.category:
        tag += f" · {finding.category}"
    if finding.agent:
        tag += f" · {finding.agent} agent_"
    else:
        tag += "_"
    parts.append(tag)
    return "\n\n".join(parts)


def anchor_findings(findings: list[Finding], diff: str) -> list[DraftComment]:
    """Seed the initial draft set from findings — deterministic, no LLM (works if Ollama is down)."""
    files = parse_diff(diff)
    drafts: list[DraftComment] = []
    for i, finding in enumerate(findings):
        cand = map_finding(finding, files)
        drafts.append(
            DraftComment(
                source_finding_id=f"{finding.agent or 'agent'}:{i}",
                path=cand.path,
                line=cand.line,
                body=_seed_body(finding),
                severity=finding.severity,
                included=finding.severity != "info",  # info opt-in
                anchored=cand.anchored,
                mapping_note=cand.note,
            )
        )
    return drafts


# --- Conversational drafting agent ----------------------------------------

_ABORT_PHRASES = {
    "abort", "discard", "cancel", "reset", "discard all", "start over",
    "abort all", "scrap it", "throw it away", "clear all", "nevermind", "never mind",
}


def is_abort(instruction: str) -> bool:
    """Deterministic abort detector — instant and LLM-free, so 'abort' always works offline."""
    norm = " ".join(re.findall(r"[a-z]+", instruction.lower()))
    if not norm:
        return False
    if norm in _ABORT_PHRASES:
        return True
    return any(word in {"abort", "discard", "cancel"} for word in norm.split())


_SYSTEM = (
    "You refine a set of draft pull-request review comments. You NEVER submit them — you only "
    "edit the drafts. You are given the review findings, the relevant diff hunks (with line "
    "numbers), the current drafts as JSON (each has a stable `id`), and the user's instruction. "
    "Return the FULL updated list of drafts. Preserve each comment's `id` and `path` unless the "
    "user explicitly asks to change them. You may rewrite `body` text, change its tone, toggle "
    "`included`, merge drafts (keep one id), add fenced code snippets, or adjust `line` — but a "
    "`line` MUST be one of the line numbers shown for that file's hunks. If the user wants to "
    "discard everything, return action='abort' with an empty comments list."
)


def _render_hunks(files: list[DiffFile], limit_lines: int = 400) -> str:
    """Compact diff view with explicit RIGHT-side line numbers, for the agent to anchor against."""
    out: list[str] = []
    shown = 0
    for f in files:
        out.append(f"FILE {f.path}")
        for h in f.hunks:
            out.append(f"  {h.header}")
            for (ln, is_add, text) in h.entries:
                if shown >= limit_lines:
                    out.append("  ... (diff truncated for context) ...")
                    return "\n".join(out)
                marker = "+" if is_add else (" " if ln is not None else "-")
                num = str(ln) if ln is not None else "   "
                out.append(f"  {num:>5} {marker} {text}")
                shown += 1
    return "\n".join(out)


def _build_refine_prompt(
    findings: list[Finding],
    files: list[DiffFile],
    current_drafts: list[DraftComment],
    chat_history: list[tuple[str, str]],
    instruction: str,
) -> str:
    drafts_json = json.dumps([d.model_dump() for d in current_drafts], indent=2)
    history = "\n".join(f"{role}: {text}" for role, text in chat_history[-6:])
    return (
        f"Findings:\n{json.dumps([f.model_dump() for f in findings], indent=2)}\n\n"
        f"Diff hunks (with RIGHT-side line numbers):\n{_render_hunks(files)}\n\n"
        f"Current drafts (JSON):\n{drafts_json}\n\n"
        f"Recent conversation:\n{history or '(none)'}\n\n"
        f"User instruction: {instruction}"
    )


def _coerce_result(payload: object) -> DraftResult | None:
    """Coerce a model payload (DraftResult | dict | list) into a DraftResult."""
    if isinstance(payload, DraftResult):
        return payload
    if isinstance(payload, list):
        payload = {"action": "update", "comments": payload}
    if isinstance(payload, dict):
        try:
            return DraftResult(**payload)
        except Exception:  # noqa: BLE001
            return None
    return None


def _tolerant_parse(text: str) -> DraftResult | None:
    """Best-effort extraction of a DraftResult from a raw model string."""
    if not text:
        return None
    fenced = re.search(r"```(?:json)?\s*(.*?)```", text, re.DOTALL)
    candidate = fenced.group(1) if fenced else text
    match = re.search(r"[\{\[].*[\}\]]", candidate, re.DOTALL)
    if not match:
        return None
    try:
        return _coerce_result(json.loads(match.group(0)))
    except (json.JSONDecodeError, TypeError):
        return None


def _preserve_anchors(
    new_comments: list[DraftComment], prior: list[DraftComment]
) -> list[DraftComment]:
    """Restore each comment's anchor from the prior draft with the same id.

    The chat agent edits content and inclusion; the *anchor* (path/line/side/severity/source)
    is managed via the UI's fields, not chat. Small models routinely drop or mangle these
    fields when asked to tweak wording, which would strip a comment's path and make it
    unsubmittable. Re-attaching anchors by id keeps refined comments anchored and submittable.
    Comments the model invents with a new id keep their own fields (then get line-revalidated).
    """
    by_id = {d.id: d for d in prior}
    for c in new_comments:
        base = by_id.get(c.id)
        if base is None:
            continue
        c.path = base.path
        c.line = base.line
        c.side = base.side
        c.severity = base.severity
        c.source_finding_id = base.source_finding_id
    return new_comments


def revalidate_lines(drafts: list[DraftComment], files: list[DiffFile]) -> list[DraftComment]:
    """Clamp every draft's line to a real diff line (or demote to file-level). A hallucinated
    line can never reach GitHub."""
    by_path = {f.path: f for f in files}
    for d in drafts:
        if not d.path:
            d.line, d.anchored = None, False
            continue
        f = by_path.get(d.path)
        if d.line is None:
            d.anchored = False
            continue
        if f is None or d.line not in f.valid_lines():
            added = f.added_lines() if f else []
            if added:
                d.line = added[0]
                d.anchored = True
                d.mapping_note = (d.mapping_note + " | line revalidated").strip(" |")
            else:
                d.line, d.anchored = None, False
                d.mapping_note = (d.mapping_note + " | no valid line; file-level").strip(" |")
        else:
            d.anchored = True
    return drafts


def draft_comments(
    findings: list[Finding],
    diff: str,
    current_drafts: list[DraftComment],
    chat_history: list[tuple[str, str]],
    instruction: str,
    *,
    llm=None,
) -> DraftResult:
    """Refine the draft set per a natural-language instruction. Never raises."""
    if is_abort(instruction):
        return DraftResult(action="abort", comments=[], message="Discarded all draft comments.")

    files = parse_diff(diff)
    llm = llm or make_llm()
    human = _build_refine_prompt(findings, files, current_drafts, chat_history, instruction)

    result: DraftResult | None = None
    try:
        structured = llm.with_structured_output(DraftResult)
        result = _coerce_result(structured.invoke([("system", _SYSTEM), ("human", human)]))
    except Exception:  # noqa: BLE001 - fall back to tolerant parsing
        try:
            raw = llm.invoke(
                [
                    ("system", _SYSTEM + '\n\nRespond ONLY with JSON: {"action","comments","message"}.'),
                    ("human", human),
                ]
            )
            result = _tolerant_parse(getattr(raw, "content", "") or "")
        except Exception as exc:  # noqa: BLE001 - degrade, never crash the UI
            return DraftResult(
                action="noop", comments=current_drafts, message=f"Could not update drafts: {exc}"
            )

    if result is None:
        return DraftResult(
            action="noop", comments=current_drafts, message="Could not parse model output; drafts unchanged."
        )
    if result.action == "abort":
        return DraftResult(action="abort", comments=[], message=result.message or "Discarded all draft comments.")

    # An update with no comments is almost always a model glitch (abort is how you clear).
    # Keep the user's current drafts rather than silently wiping them.
    if not result.comments:
        return DraftResult(
            action="noop",
            comments=current_drafts,
            message="The model returned no comments; kept your current drafts. (Say 'abort' to clear them.)",
        )

    result.comments = _preserve_anchors(result.comments, current_drafts)
    result.comments = revalidate_lines(result.comments, files)
    result.action = "update"
    result.message = result.message or "Updated drafts."
    return result


# --- GitHub pending-review submission --------------------------------------


def fetch_pr_head_sha(owner: str, repo: str, number: int) -> str:
    """Return the PR head commit SHA — inline comments must pin to a commit."""
    resp = requests.get(
        f"{GITHUB_API}/repos/{owner}/{repo}/pulls/{number}",
        headers=_headers("application/vnd.github+json"),
        timeout=30,
    )
    resp.raise_for_status()
    return resp.json()["head"]["sha"]


def build_pending_review_payload(drafts: list[DraftComment], commit_id: str = "") -> dict:
    """Build the reviews payload. Omits `event` so the review stays PENDING (never submitted)."""
    comments: list[dict] = []
    for d in drafts:
        if not d.included or not d.path:
            continue
        if d.line is not None:
            comments.append({"path": d.path, "line": d.line, "side": d.side, "body": d.body})
        else:
            comments.append({"path": d.path, "subject_type": "file", "body": d.body})
    if not comments:
        raise ValueError("No includable comments to submit")
    payload: dict = {"comments": comments}
    if commit_id:
        payload["commit_id"] = commit_id
    return payload  # NOTE: no "event" key => pending/draft review


def submit_pending_review(
    owner: str, repo: str, number: int, drafts: list[DraftComment], *, head_sha: str | None = None
) -> dict:
    """Create a PENDING review on the PR. Returns the review JSON (has `id`, `html_url`)."""
    if head_sha is None:
        head_sha = fetch_pr_head_sha(owner, repo, number)
    payload = build_pending_review_payload(drafts, head_sha)
    resp = requests.post(
        f"{GITHUB_API}/repos/{owner}/{repo}/pulls/{number}/reviews",
        headers=_headers("application/vnd.github+json"),
        json=payload,
        timeout=30,
    )
    resp.raise_for_status()
    return resp.json()
