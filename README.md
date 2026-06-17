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
  orchestrator.py   dedupe, rank, render report
  graph.py          builds the LangGraph StateGraph
  cli.py            argparse entrypoint
samples/sample.diff bundled fixture with planted issues
reports/            sample reports generated on real PRs
tests/              offline tests (orchestrator, full graph, research)
```
