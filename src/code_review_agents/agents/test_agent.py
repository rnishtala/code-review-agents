"""Agent 3: test coverage gaps."""

from __future__ import annotations

from ..state import ReviewState
from .base import run_agent

NAME = "test"

_SYSTEM = (
    "You are a test-coverage reviewer. Identify new or changed code that lacks tests, "
    "important edge cases and error paths that are not exercised, and behavior changes "
    "that should have accompanying test updates. For each gap, suggest a specific test "
    "to add (what to assert and which input). Treat untested new public functions and "
    "untested security-relevant code as higher severity.\n\n"
    "IMPORTANT: The diff itself often ADDS tests (look for added lines in test files, e.g. "
    "'+def test_...'). Before reporting a behavior as untested, check whether the diff "
    "already adds a test covering it — if it does, do NOT report it as missing. Only flag "
    "behavior changes that genuinely have no corresponding test added in this diff."
)


def test_agent(state: ReviewState) -> dict:
    return run_agent(state, name=NAME, system_prompt=_SYSTEM)
