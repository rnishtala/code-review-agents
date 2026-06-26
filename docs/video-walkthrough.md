# 5‑Minute Video Walkthrough (talking script)

A plain‑language script for narrating this project on camera. Each section has **what to
say** and roughly how long to spend. Total ≈ 5 minutes. Speak to the ideas, not the words —
this is a guide, not a teleprompter.

> Plain‑language glossary (say these casually if they come up):
> - **Agent** = one specialist reviewer (we have four: bug, security, test, governance).
> - **Golden dataset** = a graded answer key for PR reviews — the "right answers."
> - **Knowledge graph** = a map of the project: what's stable, who owns what, what depends on what.
> - **Mapper** = the grader that matches our reviewer's notes to the answer key's bullet points.
> - **Embeddings** = matching by *meaning* instead of exact words.
> - **LangSmith trace** = a flight recorder / X‑ray of one run, showing every step.

---

## 0. The hook (≈ 20 sec)

**Say:** "This is a code reviewer built from a team of small AI agents that runs entirely on
my laptop — no cloud, no API keys. The interesting part isn't that it reviews code; it's how I
measured whether it's any *good*, and how I took its score from **zero to 0.62** with three
focused changes. Let me walk through it."

---

## 1. The golden dataset — how we grade it (≈ 50 sec)

**Say:** "To know if a reviewer is good, you need an answer key. I used a **golden dataset** —
six real OpenTelemetry pull‑request scenarios. For each one, experts wrote down the findings a
correct review *must* raise — things like 'this renames a config field on a stable component,
that's a breaking change,' or 'this is missing a changelog entry.' Each expected finding has a
short label, like `stability-guarantee` or `missing-changelog`."

**Say:** "So grading is simple: our reviewer produces findings, and we check how many match the
answer key. Two numbers matter — **precision** (of what it flagged, how much was correct) and
**recall** (of what it *should* have caught, how much it actually caught)."

**On screen:** open `golden_pr_scenarios.json`, point at one `expected_findings` block.

**Key point:** "Crucially, these scenarios test *project‑rules* knowledge — stability, ownership,
process — not just code bugs. Hold that thought."

---

## 2. The three stages — what changed and why it helped (≈ 2.5 min)

This is the heart of the video. One sentence per stage: the change, then the result.

### Stage 1 — just connect it and grade (score: 0.00)

**Say:** "First I fed those PRs into the reviewer I already had — three agents for bugs,
security, and tests — and graded it. It scored a flat **zero**."

**Say:** "Not a bug — a mismatch. The answer key asks about *project rules* ('is this a breaking
change?'), but my agents only knew how to spot *code problems*. They were answering a different
exam. That zero told me exactly what was missing: project knowledge, and a reviewer who thinks
about rules."

**The change that mattered:** identifying the gap, which set up the next two fixes.

### Stage 2a — add a governance agent + give it facts (score: 0.14)

**Say:** "So I made two changes. One: I added a **fourth agent — a 'governance' reviewer** — whose
whole job is project rules: breaking changes, stability, changelogs, ownership. Two: I gave it
**facts from a knowledge graph** — a map that knows things like 'this component is stable' and
'these other repos depend on it.'"

**Say:** "First try, it broke in a funny way: I dumped *too much* text in — facts plus long
documents — and the small model choked and spat out **hundreds** of junk findings."

**The change that mattered:** "The fix was almost embarrassingly simple — feed it only the short
**facts**, not the long documents. That alone took it from **0 to 0.14**. The agent was now
finding real governance issues."

### Stage 2b — match by meaning, not exact words (score: 0.62)

**Say:** "But 0.14 was still low, and when I looked closely the agent was *right* more often than
the score showed. The problem was the **grader**. It matched word‑for‑word. The agent would say
'deprecate the old field and add an alias,' the answer key said 'stable component, stability
guarantee' — same idea, almost no shared words, so it scored zero."

**The change that mattered:** "I switched the grader to match by **meaning** using embeddings, and
combined it with the old exact‑word matching — a **hybrid**. Now paraphrases count. The score
jumped to **0.81 precision and 0.61 recall** — an F1 of **0.62**."

**On screen:** show this table.

| Stage | What changed | Precision | Recall | F1 |
|---|---|---|---|---|
| 1 — connect & grade | (revealed the gap) | 0.00 | 0.00 | 0.00 |
| 2a — governance agent + facts | feed short facts, not documents | 0.14 | 0.17 | 0.14 |
| 2b — better grader | match by meaning (hybrid) | **0.81** | **0.61** | **0.62** |

**Say (one‑liner to land it):** "Three changes — a new agent, the *right* knowledge, and a smarter
grader — took it from zero to a genuinely useful reviewer."

---

## 3. The LangSmith trace — looking inside one run (≈ 1 min)

**Say:** "One more thing: how do I *see* what the agents are doing? I turned on **LangSmith**,
which records a **trace** — think of it as a flight recorder for one review."

**Say:** "It shows the whole run as a tree, top to bottom: first it **researches** the PR (pulls in
the linked issue), then **summarizes** it, then the four agents — bug, security, test, governance —
run, and finally an **orchestrator** merges everything into the report. I can click any step and
see the exact prompt that went to the model and the exact answer that came back."

**Say:** "That's how I debugged the flooding problem from earlier — I could literally see the agent
being handed too much text. It turns a black box into something I can inspect step by step."

**On screen:** open the public trace link, expand the tree, click one agent node to show its
input/output.

**Trace link:** `reports/otel-python-5241.md` (top of the file) — or the public URL there.

**Honesty note (say it, it builds trust):** "And it's an honest demo — the report flags a couple
of the small model's mistakes, like a hallucinated 'hardcoded secret.' On a tiny laptop model that's
expected; a bigger model cleans it up."

---

## 4. Close (≈ 15 sec)

**Say:** "So: a private, on‑laptop code reviewer, measured against a real answer key, improved from
zero to 0.62 with three targeted changes — and every run is fully traceable. Thanks for watching."

---

### Cheat‑sheet (glance before recording)

- Stage scores: **0.00 → 0.14 → 0.62** (F1).
- Three changes: **(1)** governance agent, **(2)** feed short facts not documents, **(3)** match by meaning (hybrid grader).
- Precision = how much of what it flagged was right. Recall = how much of the answer key it caught.
- Golden dataset = 6 OpenTelemetry PR scenarios with expected findings (the answer key).
- LangSmith trace = step‑by‑step recording of one run (research → summarize → 4 agents → orchestrator).
- Deeper detail lives in [`docs/golden-benchmark.md`](golden-benchmark.md).
