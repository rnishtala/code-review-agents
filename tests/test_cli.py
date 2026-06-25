"""Offline tests for small CLI helpers (no network, no model)."""

import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

from code_review_agents.cli import _inject_trace_link  # noqa: E402

_URL = "https://smith.langchain.com/o/x/projects/p/r/abc123"


def test_inject_trace_link_after_pr_line():
    report = "# Code Review Report\n\n**Pull request:** https://github.com/o/r/pull/1\n\n## PR Summary\n..."
    out = _inject_trace_link(report, _URL)
    assert f"**LangSmith trace:** {_URL}" in out
    # The trace line follows the pull-request line.
    assert out.index("**Pull request:**") < out.index("**LangSmith trace:**") < out.index("## PR Summary")


def test_inject_trace_link_falls_back_to_title():
    report = "# Code Review Report\n\n## PR Summary\n..."
    out = _inject_trace_link(report, _URL)
    assert f"**LangSmith trace:** {_URL}" in out
    assert out.index("# Code Review Report") < out.index("**LangSmith trace:**")
