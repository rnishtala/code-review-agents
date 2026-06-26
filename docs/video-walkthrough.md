# Multi‑Agent Code Reviewer — From Zero to 0.62

A private, on‑laptop pull‑request reviewer — and the story of how I measured it and made it
good. Runs entirely on a local model (Ollama). No cloud, no API keys, no code leaves the machine.

*(Scroll through this top‑to‑bottom during the talk. Links open the real files.)*

---

## What it is

A small team of AI agents reviews a pull request together:

```
research → summarize → ┌─ bug agent ────────┐
                       ├─ security agent ────┤
                       ├─ test agent ────────┤ → orchestrator → report
                       └─ governance agent ──┘
```

- **Four specialists** — bug, security, test, and governance — each look at the change from one angle.
- A **research** step first pulls in the linked issue for context; an **orchestrator** merges
  everything into one prioritized report.
- Built on **LangGraph**, running a **3‑billion‑parameter model locally** on an 8 GB laptop.

> **Quick glossary:** **agent** = one specialist reviewer · **golden dataset** = a graded answer
> key · **knowledge graph** = a map of the project (what's stable, who owns what, what depends on
> what) · **embeddings** = matching by *meaning* instead of exact words · **trace** = a recording
> of one run.

---

## How do we know it's any good? An answer key.

To measure a reviewer, you need an answer key. This project uses a **golden dataset**: six real
OpenTelemetry pull‑request scenarios. Each one lists the findings a correct review *must* raise,
each with a short label such as `stability-guarantee` or `changelog`.

📄 Open: [`data/golden_pr_scenarios.json`](../data/golden_pr_scenarios.json) *(mirrored here from the separate `org-knowledge-graph-rag` repo)*

Two scores tell us how it did:

- **Precision** — of what it flagged, how much was correct.
- **Recall** — of what it *should* have caught, how much it actually got.

**The catch:** these scenarios test **project rules** — breaking changes, ownership, release
process — **not** ordinary code bugs. That mismatch is the whole story.

---

## The journey: three changes, zero → 0.62

| Stage | The change that helped | Precision | Recall | F1 |
|---|---|---|---|---|
| 1 · Connect & grade | *(revealed the gap)* | 0.00 | 0.00 | 0.00 |
| 2a · Governance agent + facts | feed short **facts**, not documents | 0.14 | 0.17 | 0.14 |
| 2b · Smarter grader | match by **meaning** (hybrid) | **0.81** | **0.61** | **0.62** |

---

## Stage 1 · Connect it and grade → **0.00**

I fed the scenarios into the reviewer I already had — bug, security, and test agents — and graded
it. It scored a flat **zero**.

Not a bug — a **mismatch**. The answer key asks about project *rules*; my agents only knew how to
spot code *problems*. They were taking a different exam. That zero pointed straight at what was
missing: **project knowledge**, and a **reviewer who thinks about rules**.

📄 The bridge that runs and grades it: [`golden_eval.py`](../src/code_review_agents/golden_eval.py)

---

## Stage 2a · A governance agent + real facts → **0.14**

Two changes:

1. **A fourth agent — "governance"** — whose whole job is project rules: breaking changes,
   stability, changelogs, ownership.
   📄 [`graph.py`](../src/code_review_agents/graph.py) · [`governance_agent.py`](../src/code_review_agents/agents/governance_agent.py)
2. **Facts from a knowledge graph** — a map that knows "this component is stable" and "these repos
   depend on it" — merged into every agent's context.
   📄 [`research.py`](../src/code_review_agents/research.py)

**The hiccup:** I fed it *too much* — facts plus long documents — and the small model choked,
producing **hundreds** of junk findings.

**The fix (simple but decisive):** feed only the short **facts**, not the long documents. That
alone moved the score from **0 → 0.14**, and the agent started finding real governance issues.
📄 [`build_kg_context.py`](../scripts/build_kg_context.py) · [`data/kg_context.json`](../data/kg_context.json)

---

## Stage 2b · Grade by meaning, not exact words → **0.62**

0.14 was still low — but looking closely, the agent was **right more often than the score showed**.
The problem was the **grader**: it matched word‑for‑word.

> The agent said *"deprecate the old field and add an alias."*
> The answer key said *"stable component, stability guarantee."*
> Same idea — almost no shared words — so it scored **zero**.

**The fix:** add a second grader that matches by **meaning** (embeddings), and combine it with the
exact‑word one — a **hybrid**. Now paraphrases count.

Result: **0.81 precision · 0.61 recall · 0.62 F1.**

📄 The three graders, side by side: [`golden_eval.py`](../src/code_review_agents/golden_eval.py)
(`map_findings_to_keys`, `map_findings_to_keys_semantic`, `map_findings_to_keys_hybrid`)

**Three changes — a new agent, the right knowledge, and a smarter grader — took it from zero to a
genuinely useful reviewer.**

---

## Seeing inside a run: the LangSmith trace

A **trace** is a flight recorder for one review. It shows the whole run as a tree —
**research → summarize → the four agents → orchestrator** — and lets me click any step to see the
exact prompt sent to the model and the exact answer it gave back.

🔎 Open the live trace: https://smith.langchain.com/public/3710c656-b640-4d00-999a-00967c937b5e/r/019f0107-0190-7e30-9ecd-f47bb673137c

This is how I caught the "flooding" problem — I could literally see the agent being handed too much
text. It turns a black box into something I can inspect step by step.

---

## Honest about limits

This runs on a tiny laptop model, so it makes some mistakes — the sample report even flags a
couple of them, like a hallucinated "hardcoded secret." That's expected at this size; a larger
model cleans it up. Showing the warts is the point — the measurement is real.

📄 A finished report (PR link + trace link + the honest note at the top):
[`reports/otel-python-5241.md`](../reports/otel-python-5241.md)

---

## Recap

- A **private, on‑laptop** code reviewer — nothing leaves the machine.
- **Measured** against a real answer key, not vibes.
- **Three changes** took it from **0.00 → 0.62**: a governance **agent**, the right **knowledge**
  (short facts), and **meaning‑based** grading.
- Every run is **fully traceable**.

*Deeper detail, with per‑scenario tables:* [`docs/golden-benchmark.md`](golden-benchmark.md)
