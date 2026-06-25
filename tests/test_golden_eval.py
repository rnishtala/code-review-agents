"""Offline tests for the Stage-1 golden-dataset bridge (no LLM, no network, no KG).

Exercises the pure pieces — input adapter, deterministic key mapper, vendored
scorer, and the harness with an injected fake ``review_fn``. The live pipeline is
never invoked here.
"""

import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

from code_review_agents.golden_eval import (  # noqa: E402
    load_scenarios,
    map_findings_to_keys,
    map_findings_to_keys_hybrid,
    map_findings_to_keys_semantic,
    run_golden_eval,
    scenario_to_review_input,
    score_review,
)
from code_review_agents.state import Finding  # noqa: E402

# A scenario shaped exactly like the golden dataset's entries.
_SCENARIO = {
    "id": "pr-otlp-receiver-breaking",
    "title": "Breaking config rename in the stable OTLP receiver",
    "repo": "opentelemetry-collector",
    "files_changed": ["receiver/otlpreceiver/config.go", "receiver/otlpreceiver/factory.go"],
    "pr_description": "Rename the grpc config block to grpc_settings in the OTLP receiver.",
    "expected_findings": [
        {"key": "stability-guarantee", "severity": "high",
         "finding": "otlpreceiver is a STABLE component; renaming a config field is a breaking "
                    "change that violates stability guarantees and must be staged with deprecation."},
        {"key": "downstream-impact", "severity": "high",
         "finding": "opentelemetry-collector-contrib depends on the core collector; a breaking "
                    "config change can ripple into contrib distributions and user configs."},
        {"key": "changelog", "severity": "medium",
         "finding": "A changelog entry (.chloggen) describing the breaking change is required."},
    ],
}


def _f(title, description="", category="general", suggestion=""):
    return Finding(title=title, description=description, category=category, suggestion=suggestion)


# --- input adapter --------------------------------------------------------- #
def test_input_adapter_builds_nonempty_diff_and_context():
    diff, context = scenario_to_review_input(_SCENARIO)
    # One stanza per changed file; non-empty so summarize() won't early-return.
    assert "+++ b/receiver/otlpreceiver/config.go" in diff
    assert "+++ b/receiver/otlpreceiver/factory.go" in diff
    # Context carries the intent the agents reason from.
    assert "opentelemetry-collector" in context
    assert "grpc_settings" in context


def test_input_adapter_handles_no_files():
    diff, context = scenario_to_review_input({"pr_description": "x"})
    assert diff.strip()  # still non-empty (placeholder), so summarize proceeds


# --- key mapper ------------------------------------------------------------ #
def test_mapper_matches_a_clearly_relevant_finding():
    findings = [
        _f("Breaking change to stable OTLP receiver",
           "Renaming the grpc config block is a breaking change to a stable component and "
           "violates stability guarantees."),
    ]
    produced = map_findings_to_keys(findings, _SCENARIO)
    assert produced[0]["matched"]
    assert produced[0]["key"] == "stability-guarantee"


def test_mapper_key_anchor_matches_on_named_key_token():
    # Naming the key's distinctive token ("changelog") is a decisive match even when the
    # rest of the wording differs from the gold prose (the real-3B "version bump" finding).
    findings = [_f("Version bump required for OTLP Receiver",
                   "A version bump is needed to reflect this change in the metadata and changelog.")]
    produced = map_findings_to_keys(findings, _SCENARIO)
    assert produced[0]["matched"]
    assert produced[0]["key"] == "changelog"


def test_mapper_stemming_absorbs_phrasing_variance():
    # "renamed"/"breaking"/"stable component" should still reach stability-guarantee
    # despite differing surface forms from the gold "renaming"/"STABLE".
    findings = [_f("Breaking change to a stable component",
                   "renamed config violates the stability guarantees of a stable component")]
    produced = map_findings_to_keys(findings, _SCENARIO)
    assert produced[0]["key"] == "stability-guarantee"


def test_mapper_leaves_unrelated_finding_unmapped():
    findings = [_f("Possible nil pointer", "factory may dereference a nil pointer on startup")]
    produced = map_findings_to_keys(findings, _SCENARIO)
    assert not produced[0]["matched"]
    assert produced[0]["key"].startswith("unmapped-")


def test_mapper_unmapped_keys_are_unique_so_they_cost_precision():
    findings = [_f("Style nit", "rename variable"), _f("Another nit", "reorder imports")]
    keys = {p["key"] for p in map_findings_to_keys(findings, _SCENARIO)}
    assert len(keys) == 2  # distinct unmapped-* keys, both count against precision


# --- semantic + hybrid mappers (fake embeddings, no Ollama) ---------------- #
# _SCENARIO expected order: stability-guarantee, downstream-impact, changelog.
# One orthogonal unit vector per key, so cosine = how aligned a finding's vector is.
def _fake_embed(vectors):
    """Return an embed_fn that ignores text and yields these vectors in call order."""
    return lambda texts: vectors


def test_semantic_mapper_accepts_confident_rejects_ambiguous():
    a = _f("Breaking change", "stable component renamed")       # clearly stability
    b = _f("Generic note", "something broadly applicable")       # ambiguous
    vectors = [
        [1.0, 0.0, 0.0],   # stability-guarantee
        [0.0, 1.0, 0.0],   # downstream-impact
        [0.0, 0.0, 1.0],   # changelog
        [0.95, 0.10, 0.0],  # a -> stability, large top-vs-runner margin
        [0.60, 0.58, 0.55],  # b -> near-tie across keys -> rejected by margin
    ]
    out = map_findings_to_keys_semantic([a, b], _SCENARIO, embed_fn=_fake_embed(vectors))
    assert out[0]["key"] == "stability-guarantee" and out[0]["matched"]
    assert not out[1]["matched"] and out[1]["key"].startswith("unmapped-")


def test_hybrid_uses_lexical_then_semantic():
    # f_lex matches 'changelog' lexically (key-anchor); f_sem is a paraphrase with no
    # token overlap, so only the semantic fallback can place it on stability-guarantee.
    f_lex = _f("Version bump", "add a changelog entry for this")
    f_sem = _f("Compatibility risk for a frozen interface",
               "altering a finalized public contract is risky")
    # embed_fn is called only for the lexically-unmapped finding: expected(3) + 1 pending.
    vectors = [[1.0, 0.0, 0.0], [0.0, 1.0, 0.0], [0.0, 0.0, 1.0], [0.95, 0.1, 0.0]]
    out = map_findings_to_keys_hybrid([f_lex, f_sem], _SCENARIO, embed_fn=_fake_embed(vectors))
    matched = {m["key"] for m in out if m["matched"]}
    assert matched == {"changelog", "stability-guarantee"}


# --- vendored scorer ------------------------------------------------------- #
def test_score_review_perfect():
    produced = [{"key": k["key"]} for k in _SCENARIO["expected_findings"]]
    res = score_review(produced, _SCENARIO)
    assert res["precision"] == 1.0 and res["recall"] == 1.0 and res["f1"] == 1.0


def test_score_review_partial_with_extra():
    produced = [{"key": "stability-guarantee"}, {"key": "unmapped-0"}]
    res = score_review(produced, _SCENARIO)
    assert res["recall"] == 1 / 3            # 1 of 3 expected
    assert res["precision"] == 1 / 2          # 1 of 2 produced
    assert res["missed"] == ["changelog", "downstream-impact"]
    assert res["extra"] == ["unmapped-0"]


# --- harness --------------------------------------------------------------- #
def test_run_golden_eval_with_fake_review_fn():
    # Fake pipeline: surfaces exactly the stability finding for this one scenario.
    def fake_review_fn(diff, context, knowledge=""):
        return [_f("Breaking change to a stable component",
                   "renaming a config field violates stability guarantees on otlpreceiver")]

    result = run_golden_eval([_SCENARIO], fake_review_fn, kg_context={})
    row = result["rows"][0]
    assert row["matched"] == ["stability-guarantee"]
    assert row["recall"] == 1 / 3
    assert result["avg_recall"] == 1 / 3


def test_run_golden_eval_handles_no_findings():
    result = run_golden_eval([_SCENARIO], lambda diff, context, knowledge="": [], kg_context={})
    assert result["avg_recall"] == 0.0
    assert result["avg_precision"] == 0.0


def test_run_golden_eval_threads_kg_context_to_review_fn():
    # The Stage-2 knowledge for a scenario id must reach review_fn as `knowledge`.
    seen = {}

    def fake_review_fn(diff, context, knowledge=""):
        seen["knowledge"] = knowledge
        return []

    kg = {_SCENARIO["id"]: "## Knowledge graph\n- OTLP Receiver stability=stable"}
    result = run_golden_eval([_SCENARIO], fake_review_fn, kg_context=kg)
    assert "stability=stable" in seen["knowledge"]
    assert result["kg_scenarios"] == 1


# --- the real golden file, if the clone is present ------------------------- #
def test_real_golden_file_loads_when_present():
    path = os.environ.get(
        "GOLDEN_PR_SCENARIOS",
        "/Users/rnishtala/src/jarvis-org/rag-app/data/opentelemetry/golden_pr_scenarios.json",
    )
    if not os.path.exists(path):
        return  # clone not present in this environment — skip silently
    scenarios = load_scenarios(path)
    assert len(scenarios) >= 1
    for sc in scenarios:
        assert sc.get("expected_findings")
        diff, context = scenario_to_review_input(sc)
        assert diff.strip() and context.strip()
