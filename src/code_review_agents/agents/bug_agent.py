"""Agent 1: bugs, logic errors, and anti-patterns."""

from __future__ import annotations

from ..state import ReviewState
from .base import run_agent

NAME = "bug"

_SYSTEM = (
    "You are a meticulous code reviewer specializing in correctness. Look for logic "
    "errors, off-by-one and boundary mistakes, null/None and type errors, incorrect "
    "error handling, race conditions, resource leaks, and anti-patterns or code smells. "
    "Prefer high-confidence, concrete bugs over speculative style nits."
)


def bug_agent(state: ReviewState) -> dict:
    return run_agent(state, name=NAME, system_prompt=_SYSTEM)
