"""Assemble the LangGraph pipeline.

    START -> research -> summarize -> {bug, security, test} (parallel) -> orchestrate -> END

``research`` fetches context about the issue the PR fixes (linked GitHub issues, plus an
optional Tavily web search) and threads it into the state before anything else runs, so
both the summary and every specialist agent benefit from it. The three specialist agents
fan out from ``summarize`` (one parallel superstep, each reading the shared summary) and
fan in to ``orchestrate``, which runs only once all three have completed. Agents append to
``findings`` via the additive reducer on the state.
"""

from __future__ import annotations

from langgraph.graph import END, START, StateGraph

from .agents.bug_agent import bug_agent
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
    graph.add_node("orchestrate", orchestrate)

    graph.add_edge(START, "research")
    graph.add_edge("research", "summarize")
    # Fan out: all three specialists depend on the summary.
    graph.add_edge("summarize", "bug")
    graph.add_edge("summarize", "security")
    graph.add_edge("summarize", "test")
    # Fan in: orchestrate waits for all three.
    graph.add_edge("bug", "orchestrate")
    graph.add_edge("security", "orchestrate")
    graph.add_edge("test", "orchestrate")
    graph.add_edge("orchestrate", END)

    return graph.compile()


def review_diff(
    diff: str,
    context: str = "",
    *,
    owner: str = "",
    repo: str = "",
    number: int | None = None,
) -> dict:
    """Convenience helper: run the full pipeline on a diff and return the final state."""
    app = build_graph()
    return app.invoke(
        {
            "diff": diff,
            "context": context,
            "owner": owner,
            "repo": repo,
            "number": number,
            "findings": [],
        }
    )
