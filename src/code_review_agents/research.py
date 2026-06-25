"""The research node: gather context about the issue a PR fixes.

Runs first in the graph so its output enriches the summary and every specialist agent.
A diff alone says *what changed*; the linked issue says *what problem it was meant to
solve* — which lets the reviewers judge whether the change actually fixes it.

Two sources, in priority order:
  1. **Linked GitHub issues** — references like ``Fixes #123`` are extracted from the PR
     body/diff and fetched via the GitHub API (the same network path ``--pr`` already
     uses). Always on.
  2. **Web search (Tavily)** — OPTIONAL and OFF unless ``TAVILY_API_KEY`` is set. It sends
     the issue text to a third party, so it is opt-in to preserve the local-only default.

The node never raises: research is best-effort enrichment, so any fetch failure degrades
to less context rather than breaking the review.
"""

from __future__ import annotations

import os

import requests

from .diff_input import IssueContext, extract_issue_refs, fetch_issue
from .state import ReviewState

TAVILY_ENDPOINT = "https://api.tavily.com/search"
MAX_WEB_RESULTS = 3
MAX_WEB_CONTENT_CHARS = 500


def tavily_search(query: str) -> list[dict]:
    """Best-effort web search via Tavily. Returns [] unless TAVILY_API_KEY is set."""
    api_key = os.environ.get("TAVILY_API_KEY")
    if not api_key or not query.strip():
        return []
    try:
        resp = requests.post(
            TAVILY_ENDPOINT,
            json={
                "api_key": api_key,
                "query": query,
                "max_results": MAX_WEB_RESULTS,
                "search_depth": "basic",
            },
            timeout=30,
        )
        resp.raise_for_status()
        return resp.json().get("results", [])[:MAX_WEB_RESULTS]
    except Exception:  # noqa: BLE001 - web search is optional; never break the review
        return []


def _render_issue(issue: IssueContext) -> str:
    ref = issue.ref
    parts = [f"### Issue {ref.owner}/{ref.repo}#{ref.number}: {issue.title} ({issue.state})"]
    if issue.labels:
        parts.append("Labels: " + ", ".join(issue.labels))
    if issue.body:
        parts.append(issue.body)
    for i, comment in enumerate(issue.comments, 1):
        parts.append(f"Comment {i}: {comment}")
    return "\n".join(parts)


def _render_web(results: list[dict]) -> str:
    lines = ["### Web search context"]
    for r in results:
        title = (r.get("title") or "").strip()
        url = (r.get("url") or "").strip()
        content = (r.get("content") or "").strip()[:MAX_WEB_CONTENT_CHARS]
        lines.append(f"- {title} ({url})\n  {content}")
    return "\n".join(lines)


def research(state: ReviewState) -> dict:
    """LangGraph node: produce ``state['research']`` from linked issues (+ optional web)."""
    owner = state.get("owner", "")
    repo = state.get("repo", "")
    search_text = f"{state.get('context', '')}\n{state.get('diff', '')}"
    refs = extract_issue_refs(
        search_text, owner, repo, exclude=state.get("number"), limit=3
    )

    blocks: list[str] = []
    issues: list[IssueContext] = []
    for ref in refs:
        try:
            issues.append(fetch_issue(ref.owner, ref.repo, ref.number))
        except Exception:  # noqa: BLE001 - skip issues we can't fetch
            continue
    blocks.extend(_render_issue(issue) for issue in issues)

    # Web search keys off the linked-issue titles (most relevant query we have).
    if issues:
        query = issues[0].title
        web = tavily_search(query)
        if web:
            blocks.append(_render_web(web))

    # Pre-fetched external knowledge (e.g. knowledge-graph facts an upstream caller
    # supplied) is injected verbatim so every downstream agent reasons over it.
    knowledge = state.get("knowledge", "").strip()
    if knowledge:
        blocks.append(knowledge)

    if not blocks:
        return {"research": ""}

    header = "## Linked issue & external context\n"
    return {"research": header + "\n\n".join(blocks)}
