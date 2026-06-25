# Multi-Agent Code Review (LangGraph + local Ollama)

A code-review pipeline built on [LangGraph](https://langchain-ai.github.io/langgraph/).
A pull request is first **researched** (linked issues fetched for context) and
**summarized**, then three **specialist agents** review the diff in parallel, and an
**orchestrator** merges their findings into one prioritized markdown report with an overall
risk rating.

Model inference always runs **locally via [Ollama](https://ollama.com)** — no API keys, no
Claude, no diff data sent to any LLM. The default model is small enough to run on an 8 GB
MacBook Air. The research step fetches linked GitHub issues (same network path `--pr`
already uses); an optional web search is off unless you set a key (see below).

## Pipeline

```
START → research → summarize → ┌─ bug_agent ─────┐
                               ├─ security_agent ─┤ → orchestrator → report.md
                               └─ test_agent ─────┘
```

- **research** — extracts issue references (`Fixes #123`, `owner/repo#45`, issue URLs) from
  the PR body/diff, fetches those GitHub issues (title, body, labels, top comments), and —
  *only if `TAVILY_API_KEY` is set* — adds a web search. This context tells the agents *what
  problem the PR was meant to solve*, so they can judge whether it actually does.
- **summarize** — plain-language explanation of *what the PR does* (shared with every agent
  and shown at the top of the report).
- **bug_agent** — logic errors, edge cases, anti-patterns.
- **security_agent** — injection, secrets, unsafe APIs, weak crypto, authz gaps.
- **test_agent** — untested new/changed code and missing edge cases.
- **orchestrator** — dedupes, sorts by severity then confidence, computes a risk rating,
  and renders the markdown report (pure Python, no LLM call).

## Quality guards (taming small models)

Small local models are fast and private but noisy: they hallucinate vulnerability classes
that aren't in the diff, claim "missing tests" for behavior the PR already tests, and
describe one issue several ways. Three **deterministic, model-independent** guards prune
that noise after the agents run (each is pure Python and unit-tested):

- **Security grounding** (`grounding.ground_security_findings`) — drops a security finding
  when it invokes a vulnerability *concept* (SSRF, SQL injection, weak crypto, auth, …) but
  the diff contains none of that concept's evidence tokens. A finding that names no tracked
  concept is always kept, so real issues survive. *(On `pallets/click#3578` this removed 4
  fabricated findings — SSRF/crypto/auth/injection — on a CLI help-formatting change.)*
- **Test grounding** (`grounding.ground_test_findings`) — drops a "missing tests" finding
  that names a test function the diff actually *adds*, and drops generic "missing tests"
  claims when the PR adds tests — unless the finding cites a *concrete* uncovered case
  (empty input, null, boundary, exception, …). The bare phrase "edge cases" doesn't count
  as concrete (it's boilerplate). Added-test detection is **language-aware** (Python `def
  test_…`, Go `func Test…`, Rust/JS, and test-file paths like `*_test.go` / `*.spec.ts`),
  and the guard runs on **every agent's** findings, since a bug agent can also say "add
  tests."
- **Near-duplicate collapse** (`orchestrator._collapse_similar`) — merges findings that
  describe the same issue in different words (Jaccard overlap of significant title/
  description tokens; 0.5 by default, relaxed to 0.4 for findings on the same location),
  keeping the strongest per cluster. It is **content-gated, not a same-line merge**: two
  *distinct* findings on the same line (e.g. a `medium` "use `TrimSpace`" note alongside a
  `high` null-check) both survive — collapsing by line alone would drop the useful one.
  Applied to both the report and the drafted PR comments, so one issue yields one comment.

These guards are why the system stays usable on a 3B model: they suppress the *fabricated*
and *boilerplate* output. What they deliberately do **not** touch is *plausible-but-wrong*
findings (a confident critique that happens to be incorrect) — detecting those needs model
comprehension, not deterministic post-processing. That residual reflects model capacity; a
larger `qwen2.5-coder:7b` (16 GB+) reduces it.

## Setup

1. **Install Ollama** and pull a model:

   ```bash
   # https://ollama.com/download
   ollama serve            # if not already running
   ollama pull qwen2.5-coder:3b
   ```

   | Model | Approx size | Notes |
   | --- | --- | --- |
   | `qwen2.5-coder:3b` (default) | ~2 GB | Code-tuned; runs on an 8 GB MacBook Air. |
   | `qwen2.5-coder:7b` | ~4.7 GB | Higher quality; needs 16 GB+. |
   | `llama3.2:3b` | ~2 GB | General-purpose alternative. |

2. **Install the Python deps** (Python 3.11+):

   ```bash
   pip install -r requirements.txt
   ```

3. **Configure** (optional) by copying `.env.example` and exporting the variables, or set
   them inline:

   ```bash
   export OLLAMA_MODEL=qwen2.5-coder:3b
   export OLLAMA_BASE_URL=http://localhost:11434
   export GITHUB_TOKEN=...    # optional, only for --pr / --repo-url
   ```

## Usage

Run as a module (with `src` on the path — `pip install -e .` makes this automatic, or use
`PYTHONPATH=src`):

```bash
# 1) Review the bundled sample diff (offline; only needs local Ollama).
#    sample.diff is a self-contained fixture, not tied to any repo.
python -m code_review_agents.cli --diff samples/sample.diff --out report.md

# 2) Review a specific GitHub PR.
python -m code_review_agents.cli --pr https://github.com/owner/repo/pull/42

# 3) Point at a GitHub repo and pick from its open PRs.
python -m code_review_agents.cli --repo-url https://github.com/owner/repo
python -m code_review_agents.cli --repo-url owner/repo --pr-number 42
```

Omit `--out` to print the report to stdout.

## Drafting PR comments (Streamlit UI)

Beyond the markdown report, a Streamlit app turns the findings into **targeted inline PR
comments** you iterate on conversationally and submit **as a draft** — nothing is ever
auto-posted.

```bash
pip install -e ".[ui]"          # adds streamlit
OLLAMA_MODEL=llama3.2:3b GITHUB_TOKEN=$(gh auth token) code-review-ui
# or: streamlit run src/code_review_agents/ui/streamlit_app.py
```

Workflow:
1. **Paste a PR URL and Run** — the app runs the full multi-agent review live, then drafts
   one comment per finding, each **anchored to a diff line** (best-effort; unmappable ones
   fall back to file-level and are collected in the review's summary body on submit, since
   GitHub's review API only anchors line-level comments). Info-level findings start unchecked.
2. **Edit** any comment's path / line / body, or toggle include per comment.
3. **Refine via chat** — tell the drafting agent things like *“make #2 softer”*, *“drop
   info-level ones”*, *“add a fix snippet to #1”*. Iterate as many times as you like. Type
   *“abort”* (or “discard”) to clear all drafts instantly. Each comment's anchor
   (path/line) is preserved across refines, so wording edits never strip it — and an
   instruction the model botches into an empty result keeps your current drafts rather than
   wiping them.
4. **Submit as draft review** — gated behind a confirmation checkbox. This creates a
   **pending** GitHub review (the API call omits the `event` field), so the comments appear
   as an unsubmitted draft you finalize and submit yourself on github.com. If the button is
   disabled, the UI says why (e.g. nothing is included, or the confirmation box is unchecked).

> Submitting requires a `GITHUB_TOKEN` with **Pull requests: write** (classic `repo` scope
> or a fine-grained token). Reading a PR/diff does not. Every agent-suggested line is
> re-validated against the diff before it can be submitted, so a bad line never reaches
> GitHub. GitHub allows only one pending review per PR per reviewer — finalize or dismiss an
> existing draft before creating another.
>
> **Model quality:** drafting/refine quality tracks the local model. On a 3B model, refines
> can be hit-or-miss (vague anchors, over-aggressive exclusions); a larger `qwen2.5-coder:7b`
> behaves noticeably better.

## Benchmarking against a golden dataset

The pipeline can be scored against an external **golden PR-review dataset**
(`golden_pr_scenarios.json`, maintained in the `org-knowledge-graph-rag` repo): code-change
scenarios each listing the findings a correct review must surface, tagged with a stable `key`,
plus the upstream `score_review` precision/recall/F1 contract.

`code_review_agents.golden_eval` is a self-contained **Stage-1 bridge** (no knowledge-graph
dependency): it adapts each scenario into pipeline inputs, runs the review, **deterministically**
maps the free-form `Finding`s to expected keys (token overlap — never feeding keys into a prompt),
and scores them. Point it at the dataset and run:

```bash
GOLDEN_PR_SCENARIOS=/path/to/golden_pr_scenarios.json \
PYTHONPATH=src python -m code_review_agents.golden_eval
```

The measured baseline on `qwen2.5-coder:3b` is **0.00 precision/recall/F1** across all six
scenarios — the scenarios reward governance/stability/process findings grounded in a knowledge
graph, while these agents are bug/security/test specialists with no KG, so nothing overlaps. That
baseline is the point: it sizes how much a future "governance" agent + KG retrieval would need to
close. The pure pieces are unit-tested offline in `tests/test_golden_eval.py`.

## Testing

The orchestrator's prioritization and rendering are pure Python and tested offline (no LLM,
no network):

```bash
pytest -q
```

The bundled `samples/sample.diff` contains three planted issues — a SQL-injection
vulnerability, a `ZeroDivisionError` bug, and two new functions with no tests — so an
end-to-end run should surface a critical security finding, a bug finding, and missing-test
findings.

## Configuration reference

| Variable | Default | Purpose |
| --- | --- | --- |
| `OLLAMA_MODEL` | `qwen2.5-coder:3b` | Model used by every agent. |
| `OLLAMA_BASE_URL` | `http://localhost:11434` | Local Ollama endpoint. |
| `GITHUB_TOKEN` | _(unset)_ | Optional; raises API limits / allows private PRs and private issues. |
| `GITHUB_API_URL` | `https://api.github.com` | GitHub REST API base. Set to a GitHub Enterprise Server endpoint (e.g. `https://github.example.com/api/v3`) to use the tool there. |
| `TAVILY_API_KEY` | _(unset)_ | Optional; enables the research agent's web search. **When set, issue/PR text is sent to Tavily** (a third party). Leave unset to keep the research step GitHub-only. |
| `CODE_REVIEW_ENABLE_TRACING` | _(unset)_ | Opt back into LangChain/LangSmith tracing. By default the app forces tracing **off** — even if your shell exports `LANGCHAIN_TRACING_V2=true` / a LangSmith key — so no review data leaves your machine. |

## Sample reports on real PRs

The pipeline was run end-to-end (LangGraph + a local `llama3.2:3b`) against real, merged
open-source pull requests. The generated reports are checked in under `reports/`:

| Report | PR | Result |
| --- | --- | --- |
| [`reports/click-3578.md`](reports/click-3578.md) | [pallets/click#3578](https://github.com/pallets/click/pull/3578) — fix double-bracketing of choices | Medium — test-coverage gaps |
| [`reports/requests-7502.md`](reports/requests-7502.md) | [psf/requests#7502](https://github.com/psf/requests/pull/7502) — fix `_encode_files` detection | Minimal — clean fix, no findings |
| [`reports/typer-1821.md`](reports/typer-1821.md) | [tiangolo/typer#1821](https://github.com/tiangolo/typer/pull/1821) — fix list-argument default | High — untested new code |
| [`reports/flask-5917.md`](reports/flask-5917.md) | [pallets/flask#5917](https://github.com/pallets/flask/pull/5917) — fix `provide_automatic_options` | Medium — **research agent** pulled in linked issue #5916 + web context |

Reproduce any of them with:

```bash
PYTHONPATH=src OLLAMA_MODEL=llama3.2:3b GITHUB_TOKEN=$(gh auth token) \
  python -m code_review_agents.cli --pr https://github.com/pallets/click/pull/3578 --out reports/click-3578.md
```

Note: report quality tracks the local model. On a 3B model some findings are imprecise
(e.g. for typer#1821 the test agent flagged the PR's own newly-added test functions as
"untested"); a larger `qwen2.5-coder:7b` improves precision.

## Project layout

```
src/code_review_agents/
  state.py          Finding model + ReviewState (additive findings reducer)
  llm.py            ChatOllama factory
  diff_input.py     PR-URL / repo-URL / local-diff loading + issue extraction/fetch
  research.py       research node: linked-issue fetch + optional Tavily web search
  summarizer.py     summarize node
  agents/           bug, security, test specialists (+ shared base)
  grounding.py      deterministic guards: security + test finding grounding
  orchestrator.py   dedupe, near-duplicate collapse, rank, render report
  graph.py          builds the LangGraph StateGraph
  comments.py       draft inline PR comments: line mapping, refine agent, pending-review submit
  ui/streamlit_app.py  Streamlit UI to draft, iterate, and submit a draft review
  cli.py            argparse entrypoint
samples/sample.diff bundled fixture with planted issues
reports/            sample reports generated on real PRs
tests/              offline tests (orchestrator, grounding, full graph, research, comments)
```
