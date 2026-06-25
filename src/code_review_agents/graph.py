"""Assemble the LangGraph pipeline.

    START -> research -> summarize -> {bug, security, test, governance} (parallel)
          -> orchestrate -> END

``research`` fetches context about the issue the PR fixes (linked GitHub issues, an
optional Tavily web search, plus any pre-fetched ``knowledge`` such as knowledge-graph
facts) and threads it into the state before anything else runs, so both the summary and
every specialist agent benefit from it. The specialist agents fan out from ``summarize``
(one parallel superstep, each reading the shared summary) and fan in to ``orchestrate``,
which runs only once all of them have completed. Agents append to ``findings`` via the
additive reducer on the state.
"""

from __future__ import annotations

from langgraph.graph import END, START, StateGraph

from .agents.bug_agent import bug_agent
from .agents.governance_agent import governance_agent
from .agents.security_agent import security_agent
from .agents.test_agent import test_agent
from .orchestrator import orchestrate
from .research import research
from .state import ReviewState
from .summarizer import summarize


def build_graph():
    """Build and compile the review graph."""
    graph = StateGraph(ReviewState)

    graph.add_node("research", research)
    graph.add_node("summarize", summarize)
    graph.add_node("bug", bug_agent)
    graph.add_node("security", security_agent)
    graph.add_node("test", test_agent)
    graph.add_node("governance", governance_agent)
    graph.add_node("orchestrate", orchestrate)

    graph.add_edge(START, "research")
    graph.add_edge("research", "summarize")
    # Fan out: all specialists depend on the summary.
    graph.add_edge("summarize", "bug")
    graph.add_edge("summarize", "security")
    graph.add_edge("summarize", "test")
    graph.add_edge("summarize", "governance")
    # Fan in: orchestrate waits for all of them.
    graph.add_edge("bug", "orchestrate")
    graph.add_edge("security", "orchestrate")
    graph.add_edge("test", "orchestrate")
    graph.add_edge("governance", "orchestrate")
    graph.add_edge("orchestrate", END)

    return graph.compile()


def review_diff(
    diff: str,
    context: str = "",
    *,
    owner: str = "",
    repo: str = "",
    number: int | None = None,
    knowledge: str = "",
    pr_url: str = "",
) -> dict:
    """Convenience helper: run the full pipeline on a diff and return the final state.

    ``knowledge`` is optional pre-fetched external context (e.g. knowledge-graph facts)
    merged into the shared research context so every agent — notably the governance
    specialist — can reason over it. ``pr_url`` is surfaced at the top of the report.
    """
    app = build_graph()
    return app.invoke(
        {
            "diff": diff,
            "context": context,
            "owner": owner,
            "repo": repo,
            "number": number,
            "knowledge": knowledge,
            "pr_url": pr_url,
            "findings": [],
        }
    )
