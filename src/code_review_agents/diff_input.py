"""Loading diffs and PR context from GitHub or local files.

Three input shapes are supported by the CLI:
  * ``--pr <github PR url>``    -> :func:`fetch_pr_diff`
  * ``--repo-url <github repo>`` -> :func:`list_open_prs` then :func:`fetch_pr_diff`
  * ``--diff <path>``          -> :func:`load_local_diff`

Only the GitHub modes touch the network. A ``GITHUB_TOKEN`` is optional but raises rate
limits and allows private repos. Model inference is always local and unaffected by this.
"""

from __future__ import annotations

import os
import re
from dataclasses import dataclass

import requests

DEFAULT_GITHUB_API = "https://api.github.com"


def resolve_github_api() -> str:
    """Base URL for the GitHub REST API.

    Defaults to public github.com; set ``GITHUB_API_URL`` to target GitHub Enterprise Server
    (e.g. ``https://github.example.com/api/v3``). Read once at import, so set it before launch.
    """
    return os.environ.get("GITHUB_API_URL", DEFAULT_GITHUB_API).rstrip("/")


GITHUB_API = resolve_github_api()
# Above this size we still review, but warn that the tail was truncated so a small local
# model is not overwhelmed (and to stay under its context window).
MAX_DIFF_CHARS = 60_000

_PR_URL_RE = re.compile(
    r"github\.com/(?P<owner>[^/]+)/(?P<repo>[^/]+)/pull/(?P<number>\d+)"
)
_REPO_URL_RE = re.compile(
    r"github\.com/(?P<owner>[^/]+)/(?P<repo>[^/]+?)(?:\.git)?/?$"
)


@dataclass
class DiffBundle:
    """A diff plus the human context that frames it for the agents."""

    diff: str
    context: str
    truncated: bool = False
    owner: str = ""
    repo: str = ""
    number: int | None = None


@dataclass
class IssueRef:
    owner: str
    repo: str
    number: int


@dataclass
class IssueContext:
    """A fetched GitHub issue, trimmed to what's useful as review context."""

    ref: IssueRef
    title: str
    state: str
    body: str
    labels: list[str]
    comments: list[str]


@dataclass
class PullRequestRef:
    number: int
    title: str
    author: str


def _headers(accept: str) -> dict[str, str]:
    headers = {"Accept": accept, "X-GitHub-Api-Version": "2022-11-28"}
    token = os.environ.get("GITHUB_TOKEN")
    if token:
        headers["Authorization"] = f"Bearer {token}"
    return headers


def parse_pr_url(url: str) -> tuple[str, str, int]:
    """Extract (owner, repo, number) from a GitHub PR URL."""
    match = _PR_URL_RE.search(url)
    if not match:
        raise ValueError(f"Not a recognizable GitHub PR URL: {url!r}")
    return match["owner"], match["repo"], int(match["number"])


def parse_repo_url(url: str) -> tuple[str, str]:
    """Extract (owner, repo) from a GitHub repo URL or 'owner/repo' shorthand."""
    shorthand = re.fullmatch(r"(?P<owner>[^/\s]+)/(?P<repo>[^/\s]+)", url.strip())
    if shorthand:
        return shorthand["owner"], shorthand["repo"].removesuffix(".git")
    match = _REPO_URL_RE.search(url)
    if not match:
        raise ValueError(f"Not a recognizable GitHub repo URL: {url!r}")
    return match["owner"], match["repo"]


def _truncate(diff: str) -> tuple[str, bool]:
    if len(diff) <= MAX_DIFF_CHARS:
        return diff, False
    head = diff[:MAX_DIFF_CHARS]
    note = "\n\n... [diff truncated for review — too large to send to a local model] ...\n"
    return head + note, True


def list_open_prs(owner: str, repo: str) -> list[PullRequestRef]:
    """Return open PRs for a repo (most recently updated first)."""
    resp = requests.get(
        f"{GITHUB_API}/repos/{owner}/{repo}/pulls",
        headers=_headers("application/vnd.github+json"),
        params={"state": "open", "sort": "updated", "direction": "desc", "per_page": 50},
        timeout=30,
    )
    resp.raise_for_status()
    return [
        PullRequestRef(
            number=pr["number"],
            title=pr.get("title", ""),
            author=(pr.get("user") or {}).get("login", "unknown"),
        )
        for pr in resp.json()
    ]


def fetch_pr_diff(owner: str, repo: str, number: int) -> DiffBundle:
    """Fetch a PR's unified diff plus its title/body for context."""
    base = f"{GITHUB_API}/repos/{owner}/{repo}/pulls/{number}"

    diff_resp = requests.get(
        base, headers=_headers("application/vnd.github.v3.diff"), timeout=30
    )
    diff_resp.raise_for_status()
    diff, truncated = _truncate(diff_resp.text)

    meta_resp = requests.get(
        base, headers=_headers("application/vnd.github+json"), timeout=30
    )
    meta_resp.raise_for_status()
    meta = meta_resp.json()

    context = (
        f"Pull request #{number} in {owner}/{repo}\n"
        f"Title: {meta.get('title', '')}\n"
        f"Author: {(meta.get('user') or {}).get('login', 'unknown')}\n"
        f"Description:\n{(meta.get('body') or '(no description provided)').strip()}"
    )
    return DiffBundle(
        diff=diff, context=context, truncated=truncated,
        owner=owner, repo=repo, number=number,
    )


def load_local_diff(path: str) -> DiffBundle:
    """Load a unified diff from a local file."""
    with open(path, "r", encoding="utf-8") as fh:
        raw = fh.read()
    diff, truncated = _truncate(raw)
    context = f"Local unified diff loaded from {path!r}."
    return DiffBundle(diff=diff, context=context, truncated=truncated)


# --- Issue research -------------------------------------------------------

# How much of an issue body/comment to keep — small models have tight context windows.
MAX_ISSUE_BODY_CHARS = 1500
MAX_ISSUE_COMMENTS = 3
MAX_COMMENT_CHARS = 600

# "fixes #12", "Closes owner/repo#34", "resolves https://github.com/o/r/issues/56"
_CLOSING_RE = re.compile(
    r"\b(?:close[sd]?|fix(?:e[sd])?|resolve[sd]?)\b[:\s]+"
    r"(?:https?://github\.com/(?P<u1>[\w.-]+)/(?P<r1>[\w.-]+)/issues/(?P<n1>\d+)"
    r"|(?:(?P<u2>[\w.-]+)/(?P<r2>[\w.-]+))?#(?P<n2>\d+))",
    re.IGNORECASE,
)
# A cross-repo "owner/repo#123" reference without a closing keyword.
_CROSS_REPO_RE = re.compile(r"\b(?P<owner>[\w.-]+)/(?P<repo>[\w.-]+)#(?P<n>\d+)\b")
# A bare "#123" anywhere (lower priority than an explicit closing keyword).
_BARE_REF_RE = re.compile(r"(?<![\w/])#(?P<n>\d+)\b")
# A full issue URL anywhere.
_ISSUE_URL_RE = re.compile(
    r"github\.com/(?P<owner>[\w.-]+)/(?P<repo>[\w.-]+)/issues/(?P<n>\d+)"
)


def extract_issue_refs(
    text: str,
    default_owner: str,
    default_repo: str,
    *,
    exclude: int | None = None,
    limit: int = 3,
) -> list[IssueRef]:
    """Find issue references in PR text, most-relevant first.

    Closing-keyword references ("fixes #123") come before bare "#123" mentions, since
    they name the issue the PR is actually resolving. Cross-repo ("owner/repo#123") and
    full-URL forms are honored; bare references fall back to the PR's own owner/repo.
    Duplicates and the PR's own number are dropped.
    """
    ordered: list[IssueRef] = []
    seen: set[tuple[str, str, int]] = set()

    def add(owner: str, repo: str, number: int) -> None:
        owner = owner or default_owner
        repo = repo or default_repo
        if not owner or not repo:
            return  # local diff with no repo context and no explicit owner/repo
        if number == exclude and owner == default_owner and repo == default_repo:
            return
        key = (owner, repo, number)
        if key in seen:
            return
        seen.add(key)
        ordered.append(IssueRef(owner=owner, repo=repo, number=number))

    for m in _CLOSING_RE.finditer(text):
        if m.group("n1"):
            add(m.group("u1"), m.group("r1"), int(m.group("n1")))
        else:
            add(m.group("u2") or "", m.group("r2") or "", int(m.group("n2")))
    for m in _ISSUE_URL_RE.finditer(text):
        add(m.group("owner"), m.group("repo"), int(m.group("n")))
    for m in _CROSS_REPO_RE.finditer(text):
        add(m.group("owner"), m.group("repo"), int(m.group("n")))
    for m in _BARE_REF_RE.finditer(text):
        add("", "", int(m.group("n")))

    return ordered[:limit]


def fetch_issue(owner: str, repo: str, number: int) -> IssueContext:
    """Fetch one issue's title/body/labels plus its first few comments."""
    base = f"{GITHUB_API}/repos/{owner}/{repo}/issues/{number}"
    resp = requests.get(base, headers=_headers("application/vnd.github+json"), timeout=30)
    resp.raise_for_status()
    data = resp.json()

    comments: list[str] = []
    if data.get("comments"):
        c_resp = requests.get(
            f"{base}/comments",
            headers=_headers("application/vnd.github+json"),
            params={"per_page": MAX_ISSUE_COMMENTS},
            timeout=30,
        )
        if c_resp.ok:
            comments = [
                (c.get("body") or "").strip()[:MAX_COMMENT_CHARS]
                for c in c_resp.json()[:MAX_ISSUE_COMMENTS]
                if (c.get("body") or "").strip()
            ]

    return IssueContext(
        ref=IssueRef(owner=owner, repo=repo, number=number),
        title=data.get("title", ""),
        state=data.get("state", "unknown"),
        body=(data.get("body") or "").strip()[:MAX_ISSUE_BODY_CHARS],
        labels=[lbl.get("name", "") for lbl in data.get("labels", []) if lbl.get("name")],
        comments=comments,
    )
