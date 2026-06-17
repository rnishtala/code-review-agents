"""The summarize node: explain in plain language what a PR does.

This runs before the specialist agents so its output becomes shared context for all of
them, and it is surfaced at the top of the final report. Unlike the agents, it returns
free-form prose rather than structured findings.
"""

from __future__ import annotations

from .llm import make_llm
from .state import ReviewState

_SYSTEM = (
    "You are a senior engineer summarizing a pull request for reviewers. "
    "In 4-8 sentences, explain in plain language WHAT this change does and WHY: "
    "the apparent intent, the key files/functions touched, and any notable behavioral "
    "changes. Be concrete and factual; do not review or critique it here, and do not "
    "invent details that are not in the diff."
)


def _build_prompt(state: ReviewState) -> str:
    context = state.get("context", "").strip()
    research = state.get("research", "").strip()
    diff = state.get("diff", "")
    parts = []
    if context:
        parts.append(f"Context:\n{context}")
    if research:
        parts.append(research)
    parts.append(f"Unified diff:\n{diff}")
    return "\n\n".join(parts)


def summarize(state: ReviewState) -> dict:
    """LangGraph node: produce ``state['summary']``."""
    diff = state.get("diff", "").strip()
    if not diff:
        return {"summary": "No diff was provided, so there is nothing to summarize."}

    try:
        llm = make_llm()
        response = llm.invoke(
            [
                ("system", _SYSTEM),
                ("human", _build_prompt(state)),
            ]
        )
        summary = (response.content or "").strip()
    except Exception as exc:  # noqa: BLE001 - keep the graph resilient
        summary = f"(Could not generate a summary: {exc})"

    return {"summary": summary or "(The model returned an empty summary.)"}
