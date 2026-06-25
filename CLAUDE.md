# Project context

## Golden evaluation dataset (use going forward)

This project is evaluated against a **golden dataset** maintained in a separate repo:

- **Repo:** `org-knowledge-graph-rag` (GitHub: `rnishtala/org-knowledge-graph-rag`), **`develop` branch**.
- **Local clone:** `/Users/rnishtala/src/jarvis-org/rag-app` (the "jarvis-org" working dir; the
  git repo is `rag-app` inside it — `jarvis-org/` itself is just a parent folder).
- **Files** (under `data/opentelemetry/`):
  - `golden_pr_scenarios.json` — **the relevant one for this repo.** PR-review scenarios for
    evaluating agents that review/raise pull requests on OpenTelemetry code.
  - `golden_qa.json` — knowledge-graph Q&A set (for the RAG app's retrieval, less relevant here).
- **Harness:** `src/evaluate.py` in that repo.

### `golden_pr_scenarios.json` structure

Top-level `meta` + `scenarios[]`. Each scenario:

- `id`, `title`, `repo`, `files_changed[]`, `pr_description`, `retrieval_query`, `tags[]`
- `expected_findings[]` — each has:
  - `key` — stable identifier (e.g. `stability-guarantee`, `missing-metadata`, `wire-compat`)
  - `severity` — `high` / `medium` / `low`
  - `finding` — the prose a correct, knowledge-grounded review must surface
  - `grounded_in[]` — knowledge-graph facts the finding rests on (e.g. `"OTLP Receiver stability=stable"`)
- `expected_reviewers[]` — the SIG/people who should review
- `affected_dependents[]` — downstream repos impacted

The 6 scenarios cover OTel governance/ownership concerns: breaking config rename in a **stable**
component, new contrib exporter missing metadata.yaml/codeowners, additive OTLP proto field,
semantic-convention attribute rename, additive Kafka-exporter config, and a breaking Tracer API
spec change. They test whether a reviewer surfaces **stability/breaking-change, downstream-impact,
process (changelog/codeowners/metadata), and governance/approval** findings — not line-level bugs.

### Scoring contract — `evaluate.score_review(agent_findings, scenario)`

To score this repo's agents against a scenario, each emitted finding must be a dict tagged with a
`key` that matches the scenario's `expected_findings[].key`. The harness computes
**precision / recall / F1 over the set of finding keys** (extra/unknown keys hurt precision;
missed expected keys hurt recall). It returns `matched` / `missed` / `extra` key lists. Matching is
on keys only — finding prose/severity are not scored by `score_review` (the `evaluate_pr` retrieval
side separately scores reviewer/dependent recall and grounding-fact coverage).

**Implication for integration:** to be scorable, this repo's review output needs a mapping from its
`Finding`s to scenario `key`s (this knowledge-graph/ownership grounding is orthogonal to the current
local-diff guards, which target bugs/security/tests rather than SIG ownership and stability policy).

### Bridge — Stage 1 (built): `src/code_review_agents/golden_eval.py`

A self-contained bridge that scores the existing pipeline against the golden set with **no
knowledge-graph dependency**. Pure pieces + glue:

- `load_scenarios(path?)` — reads the JSON; path via arg or `GOLDEN_PR_SCENARIOS` env
  (defaults to the local clone).
- `scenario_to_review_input(scenario)` — scenario → `(diff, context)`. Scenarios carry
  `pr_description` + `files_changed` but **no patch body**, so it synthesizes a per-file
  unified-diff header (non-empty so `summarize` doesn't early-return) and puts repo/title/
  description into `context`. Agents reason from intent, not changed lines — the inherent
  Stage-1 limitation.
- `map_findings_to_keys(findings, scenario, threshold=0.5, min_shared=2)` — **deterministic**
  token-overlap (overlap coefficient) of each `Finding` against each `expected_findings` text
  (prose + key tokens); best match above threshold wins, unmatched → unique `unmapped-*` key
  (so it costs precision). Never feeds keys into a prompt — that would be teaching to the test.
- `score_review(...)` — vendored verbatim from the rag-app harness (keeps this repo
  dependency-free for self-scoring).
- `run_golden_eval(scenarios?, review_fn?, ...)` — input → review → map → score, aggregated;
  `review_fn` is injectable (tests pass a fake, no Ollama). CLI: `python -m
  code_review_agents.golden_eval`.

Tests: `tests/test_golden_eval.py` (offline).

**Measured baseline (`qwen2.5-coder:3b`, 2026-06-24): precision/recall/F1 = 0.00 on all 6
scenarios.** Not a mapper artifact — the agents emit bug/security/test findings (e.g. for the
semconv rename: four "Missing test for HTTP method renaming" findings) while the golden keys are
governance/process (`schema-version`, `broad-impact`, `sig-approval`); token overlap is literally
zero. Two scenarios produced 0 findings at all (synthetic diff has no patch body, so the
line-oriented agents had nothing to flag). This 0.00 sizes Stage 2 precisely: every point of recall
has to come from the governance agent + KG facts below.

### Bridge — Stage 2 (not built): KG retrieval + governance agent

To actually raise recall: (1) make the research node accept a pluggable `knowledge_fn(query) ->
str` (default = today's GitHub-issue fetch) so a golden adapter can inject
`retrieve(scenario['retrieval_query']).context_block()` from the rag-app — keeps Neo4j an
*optional* provider, not a core dependency; (2) add a 4th "governance/policy" specialist that
consumes the KG facts and raises stability / breaking-change / downstream-impact / process
findings — the only agent that speaks the golden vocabulary.
