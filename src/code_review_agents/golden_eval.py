"""Stage-1 bridge: score the review pipeline against the org-knowledge-graph-rag
golden PR-review dataset, with **no knowledge-graph dependency**.

The golden set (`golden_pr_scenarios.json` in the `org-knowledge-graph-rag` repo,
`develop` branch) describes code-change scenarios and the findings a correct,
knowledge-grounded review must surface, each tagged with a stable `key`. The
upstream harness scores an external agent with
``score_review(agent_findings, scenario)`` → precision/recall/F1 over those keys.

This module is the seam between that harness and our pipeline. Four pure pieces
plus glue:

  * ``load_scenarios()``           — read the scenarios JSON (path via env/arg).
  * ``scenario_to_review_input()`` — scenario → ``(diff, context)`` our graph accepts.
  * ``map_findings_to_keys()``     — our free-form ``Finding``s → ``[{key}]`` for scoring,
                                     deterministically (token overlap), never via the prompt.
  * ``score_review()``             — vendored verbatim from the upstream harness.
  * ``run_golden_eval()``          — input → review_fn → map → score, aggregated.

**What to expect from Stage 1.** Our specialists are bug/security/test reviewers
with no knowledge graph; the golden findings are governance/stability/process
(`breaking change to a STABLE component`, `missing metadata.yaml`, `changelog
required`). So recall will be low. That baseline is the deliverable — it sizes how
much a Stage-2 governance agent + KG retrieval has to close. Key mapping is
deliberately deterministic and post-hoc (same philosophy as ``grounding.py``):
feeding the expected keys into a prompt would be teaching to the test.
"""

from __future__ import annotations

import json
import math
import os
import re

from .state import Finding

# The golden dataset lives in a *separate* repo. Default to the known local clone;
# override with GOLDEN_PR_SCENARIOS (e.g. in CI or after relocating the clone).
_DEFAULT_SCENARIOS = (
    "/Users/rnishtala/src/jarvis-org/rag-app/data/opentelemetry/golden_pr_scenarios.json"
)
# Stage-2 knowledge-graph context cache (produced by scripts/build_kg_context.py).
_DEFAULT_KG_CONTEXT = os.path.join(
    os.path.dirname(os.path.dirname(os.path.dirname(__file__))), "data", "kg_context.json"
)


def load_scenarios(path: str | os.PathLike | None = None) -> list[dict]:
    """Load the ``scenarios`` list from the golden PR dataset."""
    path = str(path or os.environ.get("GOLDEN_PR_SCENARIOS", _DEFAULT_SCENARIOS))
    with open(path, encoding="utf-8") as fh:
        return json.load(fh)["scenarios"]


def load_kg_context(path: str | os.PathLike | None = None) -> dict[str, str]:
    """Load the Stage-2 ``{scenario_id: kg_context}`` cache, or ``{}`` if absent.

    The cache is produced by ``scripts/build_kg_context.py`` in the rag-app
    environment (see that file). Override the path with ``GOLDEN_KG_CONTEXT``.
    """
    path = str(path or os.environ.get("GOLDEN_KG_CONTEXT", _DEFAULT_KG_CONTEXT))
    if not os.path.exists(path):
        return {}
    with open(path, encoding="utf-8") as fh:
        return json.load(fh)


# --------------------------------------------------------------------------- #
# 1. Input adapter: scenario -> (diff, context)
# --------------------------------------------------------------------------- #
def scenario_to_review_input(scenario: dict) -> tuple[str, str]:
    """Turn a golden scenario into inputs our pipeline accepts.

    The scenarios carry a ``pr_description`` + ``files_changed`` but **no patch
    body**, so we synthesize a minimal unified-diff header (one stanza per changed
    file — enough that ``summarize`` does not early-return on an empty diff) and put
    the repo / title / description into ``context``, which every node already
    threads into its prompt. The absence of a real hunk is the inherent Stage-1
    limitation: agents reason from the described intent, not from changed lines.
    """
    files = scenario.get("files_changed", []) or []
    diff_lines: list[str] = []
    for path in files:
        diff_lines += [f"diff --git a/{path} b/{path}", f"--- a/{path}", f"+++ b/{path}"]
    diff = "\n".join(diff_lines) or "diff --git a/UNKNOWN b/UNKNOWN"

    context_parts = []
    if scenario.get("repo"):
        context_parts.append(f"Repository: {scenario['repo']}")
    if scenario.get("title"):
        context_parts.append(f"PR title: {scenario['title']}")
    if scenario.get("pr_description"):
        context_parts.append(f"PR description: {scenario['pr_description']}")
    if files:
        context_parts.append("Files changed: " + ", ".join(files))
    return diff, "\n".join(context_parts)


# --------------------------------------------------------------------------- #
# 2. Key mapper: Finding[] -> [{key}]  (deterministic, post-hoc)
# --------------------------------------------------------------------------- #
# Words too generic to signal that a finding expresses a given expected finding.
_STOP = {
    "the", "and", "for", "this", "that", "with", "not", "are", "was", "has", "have",
    "must", "should", "would", "could", "when", "which", "from", "into", "use", "used",
    "using", "change", "changes", "changed", "new", "add", "added", "adding", "require",
    "required", "requires", "code", "component", "config", "field", "files", "file",
    "review", "reviewer", "reviewers", "pull", "request", "agent", "finding", "issue",
}


def _stem(token: str) -> str:
    """Crude suffix stripping so 'renamed'/'renaming'/'rename' and 'guarantee'/'guarantees'
    collapse to a common root. Not linguistically correct — just enough to absorb the
    phrasing variance between a model's wording and the gold finding text."""
    for suf in ("ing", "ed", "ies", "es", "s"):
        if token.endswith(suf) and len(token) - len(suf) >= 3:
            return token[: -len(suf)]
    return token


def _tokens(text: str) -> set[str]:
    return {
        _stem(t)
        for t in re.findall(r"[a-z0-9]+", text.lower())
        if len(t) > 2 and t not in _STOP
    }


def _key_tokens(key: str) -> set[str]:
    """Significant (stemmed) tokens from a key like ``stability-guarantee``."""
    return {_stem(t) for t in re.split(r"[-_]", key.lower()) if len(t) > 2 and t not in _STOP}


def _expected_tokens(expected_finding: dict) -> set[str]:
    return _tokens(expected_finding.get("finding", "")) | _key_tokens(
        expected_finding.get("key", "")
    )


def _finding_text(finding: Finding) -> str:
    return " ".join([finding.title, finding.description, finding.category, finding.suggestion])


def _overlap(a: set[str], b: set[str]) -> float:
    """Overlap coefficient — forgiving when the two token sets differ greatly in size."""
    if not a or not b:
        return 0.0
    return len(a & b) / min(len(a), len(b))


def map_findings_to_keys(
    findings: list[Finding],
    scenario: dict,
    *,
    threshold: float = 0.34,
    min_shared: int = 2,
) -> list[dict]:
    """Map each finding to the scenario's best-matching ``expected_findings`` key.

    A finding matches a key when either:

    * **key-anchor** — the finding text contains *all* of the key's own distinctive
      tokens (e.g. "changelog", or "downstream"+"impact"); the key is a strong label,
      so naming it is decisive; or
    * **overlap** — its (stemmed) token overlap-coefficient against the expected
      finding's text clears ``threshold`` with at least ``min_shared`` shared tokens.

    The best-scoring key wins. A finding matching nothing gets a unique ``unmapped-*``
    key so — per the upstream contract — it counts against precision rather than
    silently vanishing. Stemming + the key-anchor rule absorb the phrasing gap between
    a model's wording ("breaking change to OTLP Receiver", "renamed") and the gold text
    ("otlpreceiver", "renaming"); the negative tests guard against false matches.

    Returns dicts shaped for :func:`score_review` (each has a ``key``), plus
    ``matched``/``score``/``finding`` for inspection.
    """
    expected = [
        (ef["key"], _key_tokens(ef["key"]), _expected_tokens(ef))
        for ef in scenario.get("expected_findings", [])
    ]
    produced: list[dict] = []
    for i, finding in enumerate(findings):
        ftoks = _tokens(_finding_text(finding))
        best_key, best_score = None, 0.0
        for key, ktoks, etoks in expected:
            # Key-anchor: naming every distinctive key token is a decisive match.
            if ktoks and ktoks <= ftoks:
                score = max(1.0, _overlap(ftoks, etoks))
            elif len(ftoks & etoks) >= min_shared and _overlap(ftoks, etoks) >= threshold:
                score = _overlap(ftoks, etoks)
            else:
                continue
            if score > best_score:
                best_key, best_score = key, score
        produced.append(
            {
                "key": best_key or f"unmapped-{i}",
                "matched": best_key is not None,
                "score": round(best_score, 3),
                "finding": finding,
            }
        )
    return produced


# --------------------------------------------------------------------------- #
# 2b. Semantic key mapper (embedding cosine) — addresses the lexical ceiling
# --------------------------------------------------------------------------- #
# The lexical mapper misses paraphrases ("deprecate + add an alias" vs "stable
# component / stability guarantees" — ~2 shared tokens). A local embedding model
# (nomic-embed-text, via Ollama) matches on meaning instead of surface tokens.
EMBED_MODEL = os.environ.get("GOLDEN_EMBED_MODEL", "nomic-embed-text")
# nomic-embed-text documents task prefixes; "clustering" is its symmetric-similarity
# task, which is what comparing two short statements calls for.
_EMBED_PREFIX = "clustering: "


def _ollama_embed(texts: list[str]) -> list[list[float]]:
    """Embed texts with a local Ollama embedding model (one request per text)."""
    import requests

    from .llm import get_base_url

    url = f"{get_base_url()}/api/embeddings"
    out: list[list[float]] = []
    for text in texts:
        resp = requests.post(
            url, json={"model": EMBED_MODEL, "prompt": _EMBED_PREFIX + text}, timeout=60
        )
        resp.raise_for_status()
        out.append(resp.json()["embedding"])
    return out


def _cosine(a: list[float], b: list[float]) -> float:
    dot = sum(x * y for x, y in zip(a, b))
    na = math.sqrt(sum(x * x for x in a))
    nb = math.sqrt(sum(y * y for y in b))
    return dot / (na * nb) if na and nb else 0.0


def map_findings_to_keys_semantic(
    findings: list[Finding],
    scenario: dict,
    *,
    embed_fn=None,
    floor: float = 0.55,
    margin: float = 0.035,
) -> list[dict]:
    """Embedding-based variant of :func:`map_findings_to_keys` (argmax + margin).

    nomic-embed-text packs all of this software/OTel text into a narrow cosine band
    (~0.66–0.81), so an *absolute* threshold can't separate a real match from a generic
    one. What survives that compression is the *ranking within a finding*: a finding
    takes its top-scoring key only when that score clears ``floor`` AND beats the
    runner-up key by ``margin`` (a confident, unambiguous pick). Everything-looks-similar
    findings have a tiny top-vs-next gap and stay unmapped.

    ``embed_fn(texts) -> list[vector]`` is injectable so tests run without Ollama; it
    defaults to a local nomic-embed-text call. Same return shape as the lexical mapper.
    """
    expected = scenario.get("expected_findings", [])
    if not findings or not expected:
        return [
            {"key": f"unmapped-{i}", "matched": False, "score": 0.0, "finding": f}
            for i, f in enumerate(findings)
        ]
    embed_fn = embed_fn or _ollama_embed

    exp_texts = [f"{ef.get('key', '')}: {ef.get('finding', '')}" for ef in expected]
    find_texts = [_finding_text(f) for f in findings]
    # One batched call: expected first, then findings, so we can slice them apart.
    vecs = embed_fn(exp_texts + find_texts)
    exp_vecs = vecs[: len(expected)]
    find_vecs = vecs[len(expected):]

    produced: list[dict] = []
    for i, (finding, fvec) in enumerate(zip(findings, find_vecs)):
        scored = sorted(
            ((_cosine(fvec, evec), ef["key"]) for ef, evec in zip(expected, exp_vecs)),
            reverse=True,
        )
        best_s, best_key = scored[0]
        runner = scored[1][0] if len(scored) > 1 else 0.0
        confident = best_s >= floor and (best_s - runner) >= margin
        produced.append(
            {
                "key": best_key if confident else f"unmapped-{i}",
                "matched": confident,
                "score": round(best_s, 3),
                "finding": finding,
            }
        )
    return produced


def map_findings_to_keys_hybrid(findings: list[Finding], scenario: dict, *, embed_fn=None) -> list[dict]:
    """Lexical first (high-precision literal/key-anchor matches), then a semantic
    argmax+margin pass over whatever stayed unmapped — catching paraphrases the token
    overlap misses. The two are complementary: lexical nails "changelog"; semantic nails
    "deprecate + add an alias" -> stability-guarantee."""
    lex = map_findings_to_keys(findings, scenario)
    pending = [i for i, m in enumerate(lex) if not m["matched"]]
    if not pending:
        return lex
    sem = map_findings_to_keys_semantic(
        [findings[i] for i in pending], scenario, embed_fn=embed_fn
    )
    for slot, i in enumerate(pending):
        if sem[slot]["matched"]:
            lex[i] = sem[slot]
    return lex


# --------------------------------------------------------------------------- #
# 3. Scoring — vendored verbatim from org-knowledge-graph-rag/src/evaluate.py
#    (kept here so this repo needs no dependency on the rag-app to self-score).
# --------------------------------------------------------------------------- #
def score_review(agent_findings: list[dict], scenario: dict) -> dict:
    """Precision/recall/F1 of produced finding keys vs the scenario's expected keys."""
    expected = {f["key"] for f in scenario.get("expected_findings", [])}
    produced = {f.get("key") for f in agent_findings if f.get("key")}
    tp = len(expected & produced)
    precision = tp / len(produced) if produced else 0.0
    recall = tp / len(expected) if expected else 1.0
    f1 = 2 * precision * recall / (precision + recall) if (precision + recall) else 0.0
    return {
        "precision": precision,
        "recall": recall,
        "f1": f1,
        "matched": sorted(expected & produced),
        "missed": sorted(expected - produced),
        "extra": sorted(produced - expected),
    }


# --------------------------------------------------------------------------- #
# 4. Harness: glue input -> review -> map -> score, aggregated
# --------------------------------------------------------------------------- #
def _default_review_fn(diff: str, context: str, knowledge: str = "") -> list[Finding]:
    """Run the real pipeline (lazily imported so the pure pieces need no LangGraph)."""
    from .graph import review_diff

    return review_diff(diff, context, knowledge=knowledge).get("findings", [])


def run_golden_eval(
    scenarios: list[dict] | None = None,
    review_fn=None,
    *,
    kg_context: dict[str, str] | None = None,
    mapper: str = "lexical",
    threshold: float | None = None,
    min_shared: int = 2,
) -> dict:
    """Score the pipeline across all scenarios; return per-scenario rows + macro averages.

    ``review_fn(diff, context, knowledge) -> list[Finding]`` is injectable so tests can
    supply a fake (no Ollama); it defaults to the live pipeline. ``kg_context`` maps a
    scenario id to pre-fetched knowledge-graph context (Stage 2); when ``None`` it is
    loaded from the cache on disk, and an empty mapping reproduces the Stage-1 baseline.
    ``mapper`` is ``"lexical"`` (deterministic token overlap) or ``"semantic"``
    (local-embedding cosine, which catches paraphrases the lexical mapper misses).
    """
    if scenarios is None:
        scenarios = load_scenarios()
    if review_fn is None:
        review_fn = _default_review_fn
    if kg_context is None:
        kg_context = load_kg_context()

    if mapper == "semantic":
        map_fn = map_findings_to_keys_semantic
    elif mapper == "hybrid":
        map_fn = map_findings_to_keys_hybrid
    else:
        thr = 0.34 if threshold is None else threshold
        map_fn = lambda f, sc: map_findings_to_keys(f, sc, threshold=thr, min_shared=min_shared)

    rows: list[dict] = []
    for scenario in scenarios:
        diff, context = scenario_to_review_input(scenario)
        knowledge = kg_context.get(scenario["id"], "")
        findings = review_fn(diff, context, knowledge)
        produced = map_fn(findings, scenario)
        score = score_review(produced, scenario)
        rows.append(
            {
                "id": scenario["id"],
                "precision": score["precision"],
                "recall": score["recall"],
                "f1": score["f1"],
                "matched": score["matched"],
                "missed": score["missed"],
                "n_findings": len(findings),
                "kg": bool(knowledge.strip()),
            }
        )

    n = len(rows) or 1
    return {
        "rows": rows,
        "avg_precision": sum(r["precision"] for r in rows) / n,
        "avg_recall": sum(r["recall"] for r in rows) / n,
        "avg_f1": sum(r["f1"] for r in rows) / n,
        "kg_scenarios": sum(1 for r in rows if r["kg"]),
        "mapper": mapper,
    }


def _print_report(result: dict) -> None:
    stage = "Stage 2 (KG-grounded)" if result.get("kg_scenarios") else "Stage 1 (no KG)"
    mapper = result.get("mapper", "lexical")
    print(f"\n=== Golden PR-review eval — {stage}, {mapper} mapper ===")
    print(f"{'scenario':<38}{'prec':>6}{'recall':>8}{'f1':>6}{'finds':>7}  matched")
    for r in result["rows"]:
        print(
            f"{r['id']:<38}{r['precision']:>6.2f}{r['recall']:>8.2f}{r['f1']:>6.2f}"
            f"{r['n_findings']:>7}  {','.join(r['matched']) or '-'}"
        )
        if r["missed"]:
            print(f"    missed: {', '.join(r['missed'])}")
    print(
        f"\n  avg precision: {result['avg_precision']:.2f}"
        f"   avg recall: {result['avg_recall']:.2f}"
        f"   avg f1: {result['avg_f1']:.2f}"
    )


def main(argv: list[str] | None = None) -> None:
    import sys

    argv = sys.argv[1:] if argv is None else argv
    mapper = "lexical"
    if "--hybrid" in argv:
        mapper = "hybrid"
    elif "--semantic" in argv:
        mapper = "semantic"
    result = run_golden_eval(mapper=mapper)
    _print_report(result)


if __name__ == "__main__":
    main()
