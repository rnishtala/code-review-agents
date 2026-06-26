# 5‑Minute Talk — Presenter Script (with files to show)

This doc *is* the presentation. Read the **SAY** lines to the camera; open the **▶ SHOW**
links when you reach them. Everything is beginner‑friendly — speak to the ideas, not the words.
Total ≈ 5 minutes.

> **Before you start — open these tabs/files so you can switch fast:**
> 1. The answer key → [`golden_pr_scenarios.json`](/Users/rnishtala/src/jarvis-org/rag-app/data/opentelemetry/golden_pr_scenarios.json) *(in the separate `org-knowledge-graph-rag` repo)*
> 2. The scoreboard code → [`src/code_review_agents/golden_eval.py`](../src/code_review_agents/golden_eval.py)
> 3. The 4‑agent pipeline → [`src/code_review_agents/graph.py`](../src/code_review_agents/graph.py)
> 4. The new agent → [`src/code_review_agents/agents/governance_agent.py`](../src/code_review_agents/agents/governance_agent.py)
> 5. A finished report → [`reports/otel-python-5241.md`](../reports/otel-python-5241.md)
> 6. The live trace → https://smith.langchain.com/public/3710c656-b640-4d00-999a-00967c937b5e/r/019f0107-0190-7e30-9ecd-f47bb673137c

> Plain‑language glossary (drop these in casually):
> **Agent** = one specialist reviewer · **Golden dataset** = a graded answer key ·
> **Knowledge graph** = a map of the project (what's stable, who owns what, what depends on what) ·
> **Mapper** = the grader that matches our notes to the answer key · **Embeddings** = matching by
> *meaning* not exact words · **LangSmith trace** = a flight recorder for one run.

---

## 0 · Hook (≈ 20 sec) — *camera, no file*

**SAY:** "This is a code reviewer built from a team of small AI agents that runs entirely on my
laptop — no cloud, no API keys. The interesting part isn't that it reviews code; it's how I
*measured* whether it's any good, and how I took its score from **zero to 0.62** with three
focused changes. Let me show you."

---

## 1 · The answer key (≈ 50 sec)

**▶ SHOW:** [`golden_pr_scenarios.json`](/Users/rnishtala/src/jarvis-org/rag-app/data/opentelemetry/golden_pr_scenarios.json) — scroll to one scenario's `expected_findings` block.

**SAY:** "To know if a reviewer is good, you need an answer key. This is my **golden dataset** —
six real OpenTelemetry pull‑request scenarios. For each one, the expected findings a correct
review must raise are written down, each with a short label like `stability-guarantee` or
`changelog`." *(point at a label on screen)*

**SAY:** "So grading is simple: our reviewer produces findings, we check how many match these
labels. Two scores — **precision** (of what it flagged, how much was right) and **recall** (of
what it should've caught, how much it got)."

**▶ SHOW (optional, 3 sec):** [`evaluate.py` → `score_review`](/Users/rnishtala/src/jarvis-org/rag-app/src/evaluate.py) — "this tiny function is the grader."

**SAY (set up the punchline):** "Notice these are *project‑rules* questions — breaking changes,
ownership, process — **not** code bugs. Hold that thought."

---

## 2 · The three stages — what changed, and why it helped (≈ 2.5 min)

This is the heart. One change per stage; show the file that made it happen.

### Stage 1 — connect it and grade → **0.00**

**▶ SHOW:** [`golden_eval.py` → `scenario_to_review_input` (line 72)](../src/code_review_agents/golden_eval.py) and `run_golden_eval` (line 349).

**SAY:** "First I fed those PRs into the reviewer I already had — three agents for bugs, security,
and tests — and graded it. Flat **zero**."

**SAY:** "Not a bug — a mismatch. The answer key asks about project *rules*; my agents only knew
how to find code *problems*. Different exam. That zero told me exactly what was missing:
project knowledge, and a reviewer who thinks about rules."

### Stage 2a — add a governance agent + give it facts → **0.14**

**▶ SHOW #1:** [`graph.py` (lines 33–46)](../src/code_review_agents/graph.py) — point at the four
`add_node` lines: bug, security, test, **governance**. "I added a fourth reviewer."

**▶ SHOW #2:** [`governance_agent.py` (the `_SYSTEM` prompt, line 21)](../src/code_review_agents/agents/governance_agent.py) — "its whole job is project rules: breaking changes, stability, changelogs, ownership."

**▶ SHOW #3:** [`research.py` (lines 103–105)](../src/code_review_agents/research.py) — "and I gave
every agent **facts from a knowledge graph**, merged in right here."

**SAY:** "First try, it broke in a funny way — I fed it *too much* text, facts plus long documents,
and the small model choked and produced **hundreds** of junk findings."

**▶ SHOW #4 (the fix):** [`build_kg_context.py` → `_facts_block` (line 36)](../scripts/build_kg_context.py)
and the result file [`data/kg_context.json`](../data/kg_context.json).

**SAY:** "The fix was almost embarrassingly simple: feed it only the short **facts**, not the long
documents. That alone took it from **0 to 0.14** — the agent was now finding real governance
issues."

### Stage 2b — grade by *meaning*, not exact words → **0.62**

**▶ SHOW:** [`golden_eval.py` → the three mappers](../src/code_review_agents/golden_eval.py):
`map_findings_to_keys` (lexical, line 153), `map_findings_to_keys_semantic` (line 244),
`map_findings_to_keys_hybrid` (line 299).

**SAY:** "0.14 was still low — and when I looked closely, the agent was *right* more often than the
score showed. The problem was the **grader**: it matched word‑for‑word. The agent would say
'deprecate the old field, add an alias'; the answer key said 'stable component, stability
guarantee' — same idea, almost no shared words, so it scored zero."

**SAY:** "So I added a second grader that matches by **meaning** using embeddings, and combined it
with the exact‑word one — a **hybrid**. Now paraphrases count. The score jumped to **0.81
precision, 0.61 recall** — an F1 of **0.62**."

**▶ SHOW (the payoff table):** [`docs/golden-benchmark.md`](golden-benchmark.md) — scroll to the summary table, or just show this:

| Stage | The change that helped | Precision | Recall | F1 |
|---|---|---|---|---|
| 1 — connect & grade | *(revealed the gap)* | 0.00 | 0.00 | 0.00 |
| 2a — governance agent + facts | feed short **facts**, not documents | 0.14 | 0.17 | 0.14 |
| 2b — smarter grader | match by **meaning** (hybrid) | **0.81** | **0.61** | **0.62** |

**SAY (land it):** "Three changes — a new agent, the *right* knowledge, and a smarter grader —
took it from zero to a genuinely useful reviewer."

---

## 3 · The LangSmith trace — inside one run (≈ 1 min)

**▶ SHOW:** the public trace → https://smith.langchain.com/public/3710c656-b640-4d00-999a-00967c937b5e/r/019f0107-0190-7e30-9ecd-f47bb673137c
*(it's linked at the top of [`reports/otel-python-5241.md`](../reports/otel-python-5241.md))*

**SAY:** "How do I *see* what the agents are doing? I turned on **LangSmith**, which records a
**trace** — a flight recorder for one review."

**SAY (point at the tree):** "Top to bottom: it **researches** the PR — pulls in the linked issue —
then **summarizes** it, then the four agents run, then an **orchestrator** merges everything into
the report. I can click any step and see the exact prompt sent to the model and the exact answer
back." *(click one agent node)*

**SAY:** "This is how I caught the flooding bug earlier — I could literally see the agent being
handed too much text. It turns a black box into something I can inspect step by step."

**▶ SHOW (honesty beat):** [`reports/otel-python-5241.md`](../reports/otel-python-5241.md) — the
reviewer's note near the top.

**SAY:** "And it's an honest demo — the report itself flags a couple of the small model's mistakes,
like a hallucinated 'hardcoded secret.' On a tiny laptop model that's expected; a bigger model
cleans it up."

---

## 4 · Close (≈ 15 sec) — *camera*

**SAY:** "So: a private, on‑laptop code reviewer, measured against a real answer key, improved from
zero to 0.62 with three targeted changes — and every run is fully traceable. Thanks for watching."

---

## Presenter cheat‑sheet (glance before recording)

- **Scores:** 0.00 → 0.14 → 0.62 (F1).
- **Three changes:** (1) governance agent, (2) feed short *facts* not documents, (3) grade by *meaning* (hybrid).
- **Precision** = how much of what it flagged was right · **Recall** = how much of the answer key it caught.
- **Files in order:** golden dataset → `golden_eval.py` → `graph.py` + `governance_agent.py` + `research.py` → `build_kg_context.py` → the three mappers in `golden_eval.py` → trace → report.
- **Deeper detail if asked:** [`docs/golden-benchmark.md`](golden-benchmark.md).
