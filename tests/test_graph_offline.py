"""End-to-end test of the LangGraph pipeline without a real model.

There is no Ollama daemon in CI, so we inject a fake chat model in place of
``make_llm`` for both the summarizer and the specialist agents. This exercises the full
graph wiring — summarize -> {bug, security, test} in parallel -> orchestrate — and proves
the additive ``findings`` reducer merges concurrent agent writes and the report renders.
"""

import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

import code_review_agents.agents.base as base_mod  # noqa: E402
import code_review_agents.summarizer as summarizer_mod  # noqa: E402
from code_review_agents.graph import build_graph  # noqa: E402
from code_review_agents.state import Finding, FindingList  # noqa: E402


class _FakeResponse:
    def __init__(self, content):
        self.content = content


class _FakeStructured:
    def __init__(self, findings):
        self._findings = findings

    def invoke(self, _messages):
        # Return fresh copies, as a real model would on each call, so per-agent
        # stamping does not mutate objects shared with the other agents.
        return FindingList(findings=[f.model_copy() for f in self._findings])


class _FakeLLM:
    """Stand-in for ChatOllama: returns canned prose and structured findings."""

    def __init__(self, *, summary, findings):
        self._summary = summary
        self._findings = findings

    def invoke(self, _messages):
        return _FakeResponse(self._summary)

    def with_structured_output(self, _schema):
        return _FakeStructured(self._findings)


def test_full_pipeline_offline(monkeypatch):
    canned = [
        Finding(
            category="sql-injection",
            severity="critical",
            title="SQL injection in find_user",
            location="app/users.py: find_user",
            confidence="high",
        ),
        Finding(
            category="logic-bug",
            severity="high",
            title="ZeroDivisionError on empty list",
            location="app/users.py: average_score",
            confidence="high",
        ),
    ]

    monkeypatch.setattr(
        summarizer_mod,
        "make_llm",
        lambda *a, **k: _FakeLLM(summary="This PR adds user lookup helpers.", findings=[]),
    )
    monkeypatch.setattr(
        base_mod,
        "make_llm",
        lambda *a, **k: _FakeLLM(summary="", findings=canned),
    )

    # Diff carries real SQL footprint so the security grounding guard keeps the canned
    # SQL-injection finding (the guard drops only ungrounded vuln findings).
    diff = (
        "diff --git a/app/users.py b/app/users.py\n"
        "+++ b/app/users.py\n"
        "@@ -1 +1,2 @@\n"
        "+    cursor.execute(\"SELECT * FROM users WHERE name = '\" + name + \"'\")\n"
    )
    app = build_graph()
    final = app.invoke({"diff": diff, "context": "PR #1", "findings": []})

    # Summary flowed through to the report.
    assert final["summary"] == "This PR adds user lookup helpers."
    report = final["report"]
    assert "This PR adds user lookup helpers." in report

    # All three agents ran and their (deduped) findings made it into the report.
    assert "SQL injection in find_user" in report
    assert "ZeroDivisionError on empty list" in report
    assert "**Critical**" in report  # risk rating driven by the critical finding

    # Findings were stamped with the agent that produced them.
    agents = {f.agent for f in final["findings"]}
    assert agents == {"bug", "security", "test"}
    # Three agents each emitted the two canned findings -> six pre-dedupe.
    assert len(final["findings"]) == 6
