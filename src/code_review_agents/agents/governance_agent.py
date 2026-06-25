"""Agent 4: project governance, stability, and release process.

Unlike the bug/security/test specialists (which read the changed lines), this agent
reasons about a change's *organizational* impact: does it break a stability guarantee,
ripple into downstream/dependent repositories, or skip a required process step
(changelog, code owners, component metadata, docs, spec/SIG approval)?

It is most useful when the shared research context carries **knowledge-graph facts**
(component stability, repo ownership, `DEPENDS_ON` edges, the responsible SIG) — those
facts are what let it judge "is this component stable?" and "what depends on it?". With no
such facts it stays conservative, flagging only process gaps visible in the PR itself.
"""

from __future__ import annotations

from ..state import ReviewState
from .base import run_agent

NAME = "governance"

_SYSTEM = (
    "You are a project maintainer reviewing a pull request for governance, API/wire "
    "stability, and release-process compliance — NOT line-level bugs. Use the knowledge-"
    "graph facts in the context (component stability, repository ownership, DEPENDS_ON "
    "relationships, the responsible SIG) as your source of truth. Raise concrete findings "
    "such as: a breaking change (renamed/removed config, API, or proto field) to a STABLE "
    "component or wire protocol that needs deprecation/aliasing or a version bump; "
    "downstream impact on dependent repositories or other language SDKs that requires "
    "coordinated rollout; and missing required process artifacts for the change — a "
    "changelog entry, component metadata, code owners, a README, or spec/SIG approval. "
    "Ground each finding in the stability or dependency facts provided; if the facts do "
    "not support a concern, do not raise it. Cite the relevant component, repo, or SIG in "
    "the description."
)


def governance_agent(state: ReviewState) -> dict:
    return run_agent(state, name=NAME, system_prompt=_SYSTEM)
