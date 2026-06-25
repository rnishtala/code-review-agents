"""The tracing policy: off by default (privacy), on only when explicitly opted in.

These assert env-var behavior without touching the network. `monkeypatch.setenv` records
the original value of each var it touches and restores it on teardown — including vars the
function under test mutates directly — so these tests don't leak tracing state into others.
"""

import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

from code_review_agents import llm as llm_mod  # noqa: E402


def _track(monkeypatch):
    # Ensure monkeypatch owns (and will restore) every var the policy writes.
    for var in ("CODE_REVIEW_ENABLE_TRACING", "LANGCHAIN_TRACING_V2", "LANGSMITH_TRACING",
                "LANGCHAIN_PROJECT", "LANGSMITH_PROJECT"):
        monkeypatch.setenv(var, os.environ.get(var, ""))


def test_tracing_forced_off_by_default(monkeypatch):
    _track(monkeypatch)
    monkeypatch.delenv("CODE_REVIEW_ENABLE_TRACING", raising=False)
    assert llm_mod.configure_tracing() is False
    assert os.environ["LANGCHAIN_TRACING_V2"] == "false"
    assert os.environ["LANGSMITH_TRACING"] == "false"


def test_tracing_forced_off_even_if_shell_enables_it(monkeypatch):
    # A shell exporting LANGCHAIN_TRACING_V2=true must NOT enable tracing without opt-in.
    _track(monkeypatch)
    monkeypatch.delenv("CODE_REVIEW_ENABLE_TRACING", raising=False)
    monkeypatch.setenv("LANGCHAIN_TRACING_V2", "true")
    assert llm_mod.configure_tracing() is False
    assert os.environ["LANGCHAIN_TRACING_V2"] == "false"


def test_tracing_on_when_opted_in_sets_default_project(monkeypatch):
    _track(monkeypatch)
    monkeypatch.setenv("CODE_REVIEW_ENABLE_TRACING", "1")
    monkeypatch.delenv("LANGCHAIN_PROJECT", raising=False)
    monkeypatch.delenv("LANGSMITH_PROJECT", raising=False)
    assert llm_mod.configure_tracing() is True
    assert os.environ["LANGCHAIN_TRACING_V2"] == "true"
    assert os.environ["LANGSMITH_TRACING"] == "true"
    assert os.environ["LANGCHAIN_PROJECT"] == llm_mod.DEFAULT_PROJECT


def test_tracing_respects_custom_project(monkeypatch):
    _track(monkeypatch)
    monkeypatch.setenv("CODE_REVIEW_ENABLE_TRACING", "true")
    monkeypatch.setenv("LANGCHAIN_PROJECT", "my-project")
    assert llm_mod.configure_tracing() is True
    assert os.environ["LANGCHAIN_PROJECT"] == "my-project"


def test_has_langsmith_key_detects_either_spelling(monkeypatch):
    monkeypatch.delenv("LANGSMITH_API_KEY", raising=False)
    monkeypatch.delenv("LANGCHAIN_API_KEY", raising=False)
    assert llm_mod.has_langsmith_key() is False
    monkeypatch.setenv("LANGCHAIN_API_KEY", "ls-xxx")
    assert llm_mod.has_langsmith_key() is True
