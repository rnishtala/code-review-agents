"""The GitHub API base is configurable via GITHUB_API_URL (for GitHub Enterprise)."""

import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

from code_review_agents.diff_input import DEFAULT_GITHUB_API, resolve_github_api  # noqa: E402


def test_defaults_to_public_github(monkeypatch):
    monkeypatch.delenv("GITHUB_API_URL", raising=False)
    assert resolve_github_api() == "https://api.github.com"
    assert DEFAULT_GITHUB_API == "https://api.github.com"


def test_honors_env_override_and_strips_trailing_slash(monkeypatch):
    monkeypatch.setenv("GITHUB_API_URL", "https://github.example.com/api/v3/")
    assert resolve_github_api() == "https://github.example.com/api/v3"
