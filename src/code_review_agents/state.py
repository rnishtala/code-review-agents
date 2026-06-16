"""Shared data types for the review pipeline.

`Finding` is the structured unit every specialist agent emits. `FindingList` is the
schema we hand to the model's structured-output mode. `ReviewState` is the LangGraph
state that flows through the graph; the three specialist agents run in parallel and each
appends to `findings`, so it uses an additive reducer to merge concurrent writes.
"""

from __future__ import annotations

import operator
from typing import Annotated, Literal, TypedDict

from pydantic import BaseModel, Field

Severity = Literal["critical", "high", "medium", "low", "info"]
Confidence = Literal["high", "medium", "low"]

# Lower number = more urgent. Used by the orchestrator to sort findings.
SEVERITY_RANK: dict[str, int] = {
    "critical": 0,
    "high": 1,
    "medium": 2,
    "low": 3,
    "info": 4,
}
CONFIDENCE_RANK: dict[str, int] = {"high": 0, "medium": 1, "low": 2}


class Finding(BaseModel):
    """A single issue raised by one of the specialist agents."""

    agent: str = Field(default="", description="Which agent produced this finding.")
    category: str = Field(
        default="general",
        description="Short tag, e.g. 'logic-bug', 'sql-injection', 'missing-test'.",
    )
    severity: Severity = Field(
        default="medium",
        description="critical | high | medium | low | info",
    )
    title: str = Field(description="One-line summary of the issue.")
    description: str = Field(
        default="",
        description="What the problem is and why it matters.",
    )
    location: str = Field(
        default="",
        description="File and/or hunk the issue refers to, e.g. 'app/db.py: build_query'.",
    )
    suggestion: str = Field(
        default="",
        description="Concrete recommended fix.",
    )
    confidence: Confidence = Field(
        default="medium",
        description="high | medium | low — how sure the agent is.",
    )


class FindingList(BaseModel):
    """Structured-output wrapper: models return a list of findings under this key."""

    findings: list[Finding] = Field(default_factory=list)


class ReviewState(TypedDict, total=False):
    """State threaded through the LangGraph pipeline."""

    # Inputs / context
    diff: str
    context: str  # PR title/body or local-diff label, injected into prompts
    # Produced by the summarize node, shared with every downstream node
    summary: str
    # Specialist agents append here; the reducer merges parallel writes
    findings: Annotated[list[Finding], operator.add]
    # Final rendered markdown report
    report: str
