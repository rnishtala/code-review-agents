"""Offline tests for the orchestrator: prioritization, dedupe, and rendering.

These run without an LLM or any network access — they exercise the pure-Python merge and
render logic that turns specialist findings into the final report.
"""

import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

from code_review_agents.orchestrator import (  # noqa: E402
    _collapse_similar,
    _dedupe,
    dedupe_and_collapse,
    orchestrate,
    render_report,
)
from code_review_agents.state import Finding  # noqa: E402


def _sample_findings():
    return [
        Finding(
            agent="test",
            category="missing-test",
            severity="low",
            title="average_score has no tests",
            location="app/users.py: average_score",
            confidence="medium",
        ),
        Finding(
            agent="security",
            category="sql-injection",
            severity="critical",
            title="SQL injection in find_user",
            location="app/users.py: find_user",
            confidence="high",
        ),
        Finding(
            agent="bug",
            category="logic-bug",
            severity="high",
            title="ZeroDivisionError on empty list",
            location="app/users.py: average_score",
            confidence="high",
        ),
        Finding(
            agent="security",
            category="info",
            severity="info",
            title="Consider documenting the schema",
            location="app/users.py",
            confidence="low",
        ),
    ]


def test_findings_sorted_by_severity():
    report = render_report("A summary.", _sample_findings())
    # Critical must appear before high, high before low, low before info.
    pos_critical = report.index("SQL injection")
    pos_high = report.index("ZeroDivisionError")
    pos_low = report.index("average_score has no tests")
    pos_info = report.index("documenting the schema")
    assert pos_critical < pos_high < pos_low < pos_info


def test_report_structure():
    report = render_report("This PR changes user lookup.", _sample_findings())
    assert "# Code Review Report" in report
    assert "## PR Summary" in report
    assert "This PR changes user lookup." in report  # summary block present
    assert "## Overall Risk Rating" in report
    assert "**Critical**" in report  # driven by the critical finding
    assert "| Severity | Count |" in report  # counts table
    assert "### 🔴 Critical" in report
    assert "### 🟠 High" in report


def test_dedupe_collapses_duplicates_keeping_highest_severity():
    findings = [
        Finding(
            severity="low",
            title="SQL injection in find_user",
            location="app/users.py: find_user",
            confidence="low",
        ),
        Finding(
            severity="critical",
            title="SQL injection in find_user",  # same title + location
            location="app/users.py: find_user",
            confidence="high",
        ),
    ]
    deduped = _dedupe(findings)
    assert len(deduped) == 1
    assert deduped[0].severity == "critical"


def test_report_includes_pr_url_when_provided():
    url = "https://github.com/owner/repo/pull/42"
    report = render_report("A summary.", _sample_findings(), pr_url=url)
    assert f"**Pull request:** {url}" in report
    # Default (no url) omits the line entirely.
    assert "**Pull request:**" not in render_report("A summary.", _sample_findings())


def test_empty_findings_reports_clean():
    report = render_report("Nothing risky here.", [])
    assert "**Minimal**" in report
    assert "No issues were reported" in report


def test_collapse_merges_near_duplicates_keeping_strongest():
    findings = [
        Finding(severity="medium", confidence="medium",
                title="Optional Choice arguments reuse the type's brackets instead of doubling",
                description="make_metavar should not wrap an already-bracketed metavar again"),
        Finding(severity="high", confidence="high",
                title="Incorrect bracket wrapping for optional Choice metavar",
                description="make_metavar wraps an already-bracketed metavar again, doubling brackets"),
    ]
    collapsed = _collapse_similar(findings)
    assert len(collapsed) == 1
    assert collapsed[0].severity == "high"  # strongest kept


def test_collapse_preserves_distinct_findings():
    findings = [
        Finding(severity="high", title="SQL injection in find_user",
                description="user input concatenated into a query string"),
        Finding(severity="medium", title="Missing timeout on HTTP request",
                description="network call can hang forever without a timeout"),
    ]
    assert len(_collapse_similar(findings)) == 2


def test_same_line_distinct_findings_survive_collapse():
    # Two distinct points on the SAME location must NOT collapse — the valuable medium-severity
    # one would otherwise be dropped in favor of the high-severity sibling (the otel case).
    loc = "pkg/ottl/ottlfuncs/func_trim.go: trim"
    findings = [
        Finding(severity="high", title="Add a null check before strings.Trim",
                description="handle a nil target before trimming", location=loc),
        Finding(severity="medium", title="Use strings.TrimSpace instead of strings.Trim",
                description="default only strips spaces, not tabs or newlines", location=loc),
    ]
    out = _collapse_similar(findings)
    titles = " ".join(f.title for f in out)
    assert len(out) == 2
    assert "TrimSpace" in titles  # the useful medium finding is preserved


def test_dedupe_and_collapse_combines_both_stages():
    findings = [
        Finding(severity="low", title="Same title", location="a.py", description="x"),
        Finding(severity="high", title="Same title", location="a.py", description="x"),  # exact dup
        Finding(severity="medium", title="Totally unrelated parser bug",
                description="off-by-one in token scan"),
    ]
    out = dedupe_and_collapse(findings)
    assert len(out) == 2  # exact dup folded, unrelated kept


def test_orchestrate_node_returns_report_key():
    state = {"summary": "S", "findings": _sample_findings()}
    out = orchestrate(state)
    assert "report" in out
    assert "# Code Review Report" in out["report"]
