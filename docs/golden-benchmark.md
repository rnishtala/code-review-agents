# Golden PR-Review Benchmark — Stages & Results

This document is the full account of bridging this multi-agent code reviewer to an external
**golden PR-review dataset**, and the measured results at each stage. It is self-contained;
`CLAUDE.md` carries the condensed version and `README.md` the user-facing summary.

- **Headline:** average **F1 0.00 → 0.62** (precision 0.81, recall 0.61) on `qwen2.5-coder:3b`,
  across three stages.
- **Code:** `src/code_review_agents/golden_eval.py`, `agents/governance_agent.py`,
  `scripts/build_kg_context.py`. **Tests:** `tests/test_golden_eval.py` (offline).

---

## 1. The dataset and the scoring contract

The golden set (`golden_pr_scenarios.json`) lives in a separate repo,
`org-knowledge-graph-rag` (`develop` branch; local clone `…/jarvis-org/rag-app`). It contains
**6 OpenTelemetry PR-review scenarios**. Each scenario gives a `pr_description`, the list of
`files_changed`, a `retrieval_query`, and the findings a correct, knowledge-grounded review must
surface — each tagged with a stable `key`:

```jsonc
{
  "id": "pr-otlp-receiver-breaking",
  "files_changed": ["receiver/otlpreceiver/config.go", ...],
  "pr_description": "Rename the `grpc` config block to `grpc_settings` ...",
  "retrieval_query": "OTLP receiver stability ... which SIG owns it and what depends on it",
  "expected_findings": [
    {"key": "stability-guarantee", "severity": "high",
     "finding": "otlpreceiver is a STABLE component; renaming a config field is a breaking change ...",
     "grounded_in": ["OTLP Receiver stability=stable", "IN_REPO opentelemetry-collector"]},
    {"key": "downstream-impact", ...},
    {"key": "changelog", ...}
  ],
  "expected_reviewers": ["Collector"],
  "affected_dependents": ["opentelemetry-collector-contrib"]
}
```

These are **governance / stability / process** concerns — breaking change to a *stable* component,
missing `metadata.yaml`/codeowners, additive proto field, semantic-convention rename, additive
config, breaking spec API change — **not line-level bugs**.

**Scoring** (`evaluate.score_review`, vendored into `golden_eval.score_review`): the agent emits
findings tagged with a `key`; the harness computes **precision / recall / F1 over the set of keys**
(extra/unknown keys hurt precision, missed expected keys hurt recall). Matching is on keys only.

---

## 2. The three gaps

| | this reviewer produces/wants | the golden set expects |
|---|---|---|
| **Input** | a unified `diff` | `pr_description` + `files_changed[]`, **no diff** |
| **Knowledge** | diff text + linked GitHub issues | findings grounded in a **knowledge graph** (stability, ownership, `DEPENDS_ON`, SIG) |
| **Output** | free-form `Finding` (no key) | findings keyed to `expected_findings[].key` |

Under that, a conceptual mismatch: the bug / security / test specialists speak a different language
than the governance/process golden set. Closing the gap takes more than reshaping data — the
pipeline needs the *knowledge* and a specialist that *speaks governance*.

---

## 3. Stage 1 — bridge the existing pipeline (no knowledge graph)

**What:** `golden_eval.py` adapts each scenario into pipeline inputs, runs the existing review,
deterministically maps the free-form findings to expected keys, and scores them. Pieces:

- `scenario_to_review_input(scenario)` → `(diff, context)`. No patch body exists, so it synthesizes
  a per-file unified-diff header (non-empty so `summarize` doesn't early-return) and puts
  repo/title/description into `context`.
- `map_findings_to_keys(...)` — deterministic token-overlap of each finding against each expected
  finding's text; unmatched → unique `unmapped-*` key (costs precision). **Keys never go into a
  prompt** — that would be teaching to the test.
- `run_golden_eval(...)` — input → review → map → score, aggregated. CLI: `python -m
  code_review_agents.golden_eval`.

**Result (`qwen2.5-coder:3b`): 0.00 / 0.00 / 0.00 on every scenario.**

| scenario | prec | recall | f1 | findings | matched |
|---|---|---|---|---|---|
| pr-otlp-receiver-breaking | 0.00 | 0.00 | 0.00 | 3 | — |
| pr-contrib-new-exporter-missing-metadata | 0.00 | 0.00 | 0.00 | 0 | — |
| pr-otlp-proto-new-field | 0.00 | 0.00 | 0.00 | 1 | — |
| pr-semconv-http-breaking | 0.00 | 0.00 | 0.00 | 4 | — |
| pr-kafka-exporter-config | 0.00 | 0.00 | 0.00 | 0 | — |
| pr-spec-tracer-api-change | 0.00 | 0.00 | 0.00 | 1 | — |
| **average** | **0.00** | **0.00** | **0.00** | | |

**Why zero (verified, not a mapper artifact):** for the semconv rename the pipeline emitted four
*"Missing test for HTTP method renaming"* findings while the gold keys are `schema-version`,
`broad-impact`, `sig-approval` — token overlap is literally zero. Two scenarios produced **no
findings at all** (the synthetic diff has no lines for the line-oriented agents to flag). This
baseline sizes Stage 2: every point of recall must come from new capability.

---

## 4. Stage 2 — knowledge seam + governance agent

Two additions, keeping Neo4j an **optional** provider (never a core dependency):

1. **Knowledge seam.** `ReviewState.knowledge` + `review_diff(..., knowledge="")`; the `research`
   node merges it into the shared research context, so every agent sees it. Core code never imports
   Neo4j or the rag-app.
2. **Governance agent** (`agents/governance_agent.py`) — a 4th specialist fanning out alongside
   bug/security/test. It reasons about stability / breaking-change / downstream-impact / process
   (changelog, codeowners, metadata, README, spec/SIG approval), grounded in the KG facts in
   context. The only agent that speaks the golden vocabulary.
3. **KG cache bridge** (`scripts/build_kg_context.py`) — runs in the **rag-app venv** (which has
   Neo4j + `retrieve()`), and for each scenario stores a compact **facts** block into
   `data/kg_context.json` (committed, so the eval re-runs without a live graph). `run_golden_eval`
   injects it as `knowledge=` per scenario.

### The flooding bug (and fix)

The first Stage-2 run fed the retriever's full `context_block()` — facts **plus document chunks** —
which overflowed the 3B's 4096-token window. Two scenarios produced **352 and 364 findings**
(degenerate repetition); others collapsed to none. Exporting a **facts-only** block (~1.5 KB) fixed
both. Hence `build_kg_context.py` stores `.facts`, not `.context_block()`.

### Result with the lexical mapper: 0.14 / 0.17 / 0.14

(The mapper also gained light stemming + a key-anchor rule — a finding naming a key's distinctive
token, e.g. "changelog", is a decisive match — threshold 0.34, guarded by negative tests.)

| scenario | prec | recall | f1 | matched |
|---|---|---|---|---|
| pr-otlp-receiver-breaking | 0.33 | 0.67 | 0.44 | changelog, downstream-impact |
| pr-contrib-new-exporter-missing-metadata | 0.00 | 0.00 | 0.00 | — |
| pr-otlp-proto-new-field | 0.50 | 0.33 | 0.40 | cross-language-rollout |
| pr-semconv-http-breaking | 0.00 | 0.00 | 0.00 | — |
| pr-kafka-exporter-config | 0.00 | 0.00 | 0.00 | — |
| pr-spec-tracer-api-change | 0.00 | 0.00 | 0.00 | — |
| **average** | **0.14** | **0.17** | **0.14** | |

The governance agent now emits genuine governance findings ("Breaking change in OTLP Receiver
config", "Version bump … changelog"). But the **lexical mapper scores them ~0** when the wording
differs from the gold prose — the agent says "deprecate + add an alias" where gold says "stable
component / stability guarantees" (~2 shared tokens). The bottleneck moved from *"agents can't"* to
*"the mapper can't match phrasing."*

---

## 5. Stage 2 — the key mappers (lexical → semantic → hybrid)

Three interchangeable mappers (same return shape; selected via `mapper=` or `--hybrid`/`--semantic`):

- **lexical** (default) — stemmed token overlap + key-anchor. High precision on literal matches;
  blind to paraphrases.
- **semantic** — cosine over local `nomic-embed-text` embeddings.
  **Calibration finding:** the model packs all this OTel/software text into a narrow band
  (~0.66–0.81), and an *unrelated* finding (a nil-pointer bug) scored **0.711** against
  `stability-guarantee` — higher than some true matches elsewhere. An absolute threshold cannot
  separate signal from noise. What survives the compression is the **ranking within a finding**, so
  the mapper uses **argmax + a runner-up margin** (accept the top key only when it beats the next by
  ≥ 0.035 and clears a 0.55 floor).
- **hybrid** (`--hybrid`) — lexical first, then the semantic argmax+margin pass over whatever stayed
  unmapped. They are complementary: lexical nails "changelog" (literal token); semantic nails
  "deprecate + add an alias" → `stability-guarantee` (paraphrase), while still rejecting the
  nil-pointer finding.

### Result with the hybrid mapper: 0.81 / 0.61 / 0.62

| scenario | prec | recall | f1 | matched | missed |
|---|---|---|---|---|---|
| pr-otlp-receiver-breaking | 0.60 | **1.00** | 0.75 | changelog, downstream-impact, stability-guarantee | — |
| pr-contrib-new-exporter-missing-metadata | 0.29 | 0.67 | 0.40 | missing-metadata, missing-readme | missing-codeowners |
| pr-otlp-proto-new-field | 1.00 | 0.67 | 0.80 | cross-language-rollout, wire-compat | spec-approval |
| pr-semconv-http-breaking | 1.00 | 0.67 | 0.80 | broad-impact, sig-approval | schema-version |
| pr-kafka-exporter-config | 1.00 | 0.33 | 0.50 | additive-ok | codeowner-review, docs-changelog |
| pr-spec-tracer-api-change | 1.00 | 0.33 | 0.50 | api-stability | all-sdks-impacted, spec-governance |
| **average** | **0.81** | **0.61** | **0.62** | | |

Every scenario now scores > 0, and the matched keys are the *correct* ones for each scenario. Four
scenarios reach 1.00 precision; the OTLP-receiver scenario reaches 1.00 recall (all three keys).

---

## 6. Summary of results

| Stage / mapper | avg precision | avg recall | avg F1 |
|---|---|---|---|
| Stage 1 — no knowledge graph | 0.00 | 0.00 | 0.00 |
| Stage 2 — KG + governance agent, **lexical** mapper | 0.14 | 0.17 | 0.14 |
| Stage 2 — KG + governance agent, **hybrid** mapper | **0.81** | **0.61** | **0.62** |

---

## 7. Honest limitations

- **Run-to-run variance.** On a 3B the agent's output varies between runs (one run missed
  `downstream-impact`, the next caught it). The numbers above are single runs, ~approximate.
- **Genuine model misses.** The remaining recall gap is mostly findings the 3B simply didn't raise
  (e.g. `schema-version`, `spec-governance`, `codeowner-review`) — not a mapping failure. A larger
  `qwen2.5-coder:7b` would help, but it needs 16 GB and does not fit this 8 GB MacBook Air.
- **Semantic false matches.** Argmax can occasionally place a finding on a plausible-but-wrong key;
  precision stays high (0.81) but is not guaranteed.
- **Not pinned in CI.** The headline numbers require Ollama + a one-time KG-cache build; the offline
  test suite covers the pure logic (adapter, mappers with a fake `embed_fn`, scorer) but not the
  live figures.

---

## 8. Reproducing

```bash
# 1) One-time: build the KG-facts cache (run in the rag-app checkout + its venv; needs Neo4j up).
cd <rag-app> && source .venv/bin/activate
PYTHONPATH=<rag-app> python <this-repo>/scripts/build_kg_context.py \
  --scenarios data/opentelemetry/golden_pr_scenarios.json \
  --out <this-repo>/data/kg_context.json     # already committed; only needed to refresh

# 2) Run the benchmark (this repo, Ollama running).
cd <this-repo>
PYTHONPATH=src OLLAMA_MODEL=qwen2.5-coder:3b python -m code_review_agents.golden_eval            # lexical
PYTHONPATH=src OLLAMA_MODEL=qwen2.5-coder:3b python -m code_review_agents.golden_eval --hybrid   # hybrid (best)

# Stage-1 baseline: same command with an empty/absent data/kg_context.json.

# 3) Offline tests for the pure pieces (no Ollama, no network):
PYTHONPATH=src pytest tests/test_golden_eval.py -q
```

Override paths with `GOLDEN_PR_SCENARIOS`, `GOLDEN_KG_CONTEXT`, `GOLDEN_EMBED_MODEL`.
