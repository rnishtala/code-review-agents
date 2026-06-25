"""Offline tests for the research layer: issue-ref extraction and the research node.

No network and no LLM — GitHub fetches and Tavily are monkeypatched, so these exercise the
extraction priority rules and the node's assembly/degradation logic deterministically.
"""

import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

import code_review_agents.research as research_mod  # noqa: E402
from code_review_agents.diff_input import IssueContext, IssueRef, extract_issue_refs  # noqa: E402


def test_closing_keyword_refs_come_first():
    text = "Mentions #99 in passing.\n\nFixes #42. See also #7."
    refs = extract_issue_refs(text, "octo", "repo")
    # The closing-keyword ref (#42) must rank ahead of the bare mentions.
    assert refs[0] == IssueRef("octo", "repo", 42)
    numbers = [r.number for r in refs]
    assert set(numbers) == {42, 99, 7}


def test_cross_repo_and_url_refs():
    text = "Closes other/proj#5 and resolves https://github.com/foo/bar/issues/8"
    refs = extract_issue_refs(text, "octo", "repo")
    assert IssueRef("other", "proj", 5) in refs
    assert IssueRef("foo", "bar", 8) in refs


def test_excludes_pr_own_number_and_dedupes():
    text = "Fixes #42. Also references #42 again. This is PR #100."
    refs = extract_issue_refs(text, "octo", "repo", exclude=100)
    numbers = [r.number for r in refs]
    assert numbers.count(42) == 1  # deduped
    assert 100 not in numbers  # PR's own number excluded


def test_no_repo_context_drops_bare_refs():
    # A local diff has no owner/repo; bare "#5" can't be resolved, so it's dropped,
    # but an explicit owner/repo#n still resolves.
    refs = extract_issue_refs("see #5 and foo/bar#6", "", "")
    assert refs == [IssueRef("foo", "bar", 6)]


def test_limit_caps_results():
    text = "fixes #1 fixes #2 fixes #3 fixes #4 fixes #5"
    assert len(extract_issue_refs(text, "o", "r", limit=2)) == 2


def test_research_node_assembles_issue_context(monkeypatch):
    issue = IssueContext(
        ref=IssueRef("octo", "repo", 42),
        title="Crash on empty input",
        state="closed",
        body="Calling foo([]) raises ZeroDivisionError.",
        labels=["bug"],
        comments=["Confirmed on main."],
    )
    monkeypatch.setattr(research_mod, "fetch_issue", lambda o, r, n: issue)
    # No TAVILY_API_KEY -> web search stays off.
    monkeypatch.delenv("TAVILY_API_KEY", raising=False)

    out = research_mod.research(
        {"context": "Fixes #42", "diff": "", "owner": "octo", "repo": "repo", "number": 100}
    )
    text = out["research"]
    assert "Crash on empty input" in text
    assert "ZeroDivisionError" in text
    assert "Confirmed on main." in text
    assert "Web search context" not in text  # opt-in only


def test_research_node_empty_when_no_refs(monkeypatch):
    # No issue references and no repo -> nothing to research.
    monkeypatch.setattr(
        research_mod, "fetch_issue", lambda *a: (_ for _ in ()).throw(AssertionError("should not fetch"))
    )
    out = research_mod.research({"context": "no refs here", "diff": "", "owner": "", "repo": ""})
    assert out == {"research": ""}


def test_research_node_merges_injected_knowledge():
    # Pre-fetched knowledge (e.g. KG facts) is surfaced even with no issue refs/repo.
    out = research_mod.research(
        {
            "context": "no refs",
            "diff": "",
            "owner": "",
            "repo": "",
            "knowledge": "## Knowledge graph\n- OTLP Receiver stability=stable",
        }
    )
    assert "OTLP Receiver stability=stable" in out["research"]


def test_research_node_no_knowledge_key_is_safe():
    # Absent 'knowledge' must not break the node (back-compat with old state).
    out = research_mod.research({"context": "no refs", "diff": "", "owner": "", "repo": ""})
    assert out == {"research": ""}


def test_research_node_survives_fetch_failure(monkeypatch):
    def boom(*_a):
        raise RuntimeError("github down")

    monkeypatch.setattr(research_mod, "fetch_issue", boom)
    out = research_mod.research(
        {"context": "Fixes #42", "diff": "", "owner": "octo", "repo": "repo", "number": 1}
    )
    assert out == {"research": ""}  # degrades instead of raising


def test_tavily_search_disabled_without_key(monkeypatch):
    monkeypatch.delenv("TAVILY_API_KEY", raising=False)
    assert research_mod.tavily_search("anything") == []
