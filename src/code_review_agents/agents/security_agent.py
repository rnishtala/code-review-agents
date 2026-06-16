"""Agent 2: security vulnerabilities."""

from __future__ import annotations

from ..state import ReviewState
from .base import run_agent

NAME = "security"

_SYSTEM = (
    "You are an application security reviewer. Look for injection (SQL, command, path), "
    "hardcoded secrets or credentials, unsafe deserialization, use of dangerous APIs "
    "(eval, exec, pickle, shell=True), weak or misused cryptography, missing authentication "
    "or authorization checks, SSRF, and unsafe handling of untrusted input. Rate exploitable "
    "issues higher in severity, and explain the attack vector concretely."
)


def security_agent(state: ReviewState) -> dict:
    return run_agent(state, name=NAME, system_prompt=_SYSTEM)
