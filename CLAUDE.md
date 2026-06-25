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

### Bridge — Stage 2 (built): KG retrieval + governance agent

Two pieces raise recall off the Stage-1 zero, keeping Neo4j an *optional* provider rather than a
core dependency:

1. **Knowledge seam.** `ReviewState` gained a `knowledge: str` field; `review_diff(...,
   knowledge="")` threads it in, and the `research` node merges it into the shared research
   context so every agent sees it. Core code never imports Neo4j or the rag-app.
2. **Governance agent** (`agents/governance_agent.py`) — a 4th specialist fanning out alongside
   bug/security/test. It reasons about stability / breaking-change / downstream-impact / process
   (changelog, codeowners, metadata, README, spec/SIG approval), grounded in the KG facts in
   context. It's the only agent that speaks the golden vocabulary.
3. **KG cache bridge** (`scripts/build_kg_context.py`) — run in the **rag-app venv** (which has
   Neo4j + `retrieve()`); for each scenario it stores `retrieve(retrieval_query).context_block()`
   into `data/kg_context.json`. `golden_eval.load_kg_context()` loads it (path via
   `GOLDEN_KG_CONTEXT`) and `run_golden_eval` injects it as `knowledge=` per scenario. Caching to
   JSON decouples the repos and makes re-runs reproducible without a live graph. Only KG facts are
   exported — never the expected findings/keys.

Regenerate the cache when the graph changes:
```
cd <rag-app> && source .venv/bin/activate
PYTHONPATH=<rag-app> python <this-repo>/scripts/build_kg_context.py \
  --scenarios data/opentelemetry/golden_pr_scenarios.json \
  --out <this-repo>/data/kg_context.json
```
`data/kg_context.json` is committed so the Stage-2 eval runs standalone (no Neo4j needed to
re-score). The key mapper got light stemming + a key-anchor rule (a finding naming a key's
distinctive token — e.g. "changelog" — is a decisive match) to absorb phrasing variance between
model wording and gold prose; threshold 0.34, guarded by negative tests against false matches.

**Measured (`qwen2.5-coder:3b`, 2026-06-25): precision/recall/F1 ≈ 0.14 / 0.17 / 0.14** (Stage 1
was 0.00). The governance agent now emits genuine governance findings: the OTLP-receiver scenario
matched `changelog` + `downstream-impact` (0.67 recall), the proto scenario matched
`cross-language-rollout`. Two findings worth noting:

- **Compact facts are essential.** Feeding the retriever's full `context_block()` (facts + doc
  chunks) overflowed the 3B's 4096-token window → it flooded (350+ findings) or collapsed to none.
  The facts-only block (~1.5 KB) fixed both. Hence `build_kg_context.py` exports `.facts`, not
  `.context_block()`.
- **Residual misses are two distinct kinds, neither closable by more wiring.** (1) *Process
  knowledge* the KG doesn't carry and a 3B doesn't know — `missing-metadata`/`codeowners`/`readme`,
  `schema-version`, `sig-approval` (these scenarios scored 0). (2) The *lexical-vs-semantic matching
  ceiling* — the agent says "deprecate + add an alias" where gold says "stable component / stability
  guarantees"; only ~2 tokens overlap, below threshold. A larger model (`qwen2.5-coder:7b`, needs
  16 GB — doesn't fit this 8 GB Air) addresses (1); embedding-based mapping would address (2). Run-
  to-run variance is real on a 3B (one run missed `downstream-impact`, the next caught it).
