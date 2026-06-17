"""Offline tests for the conversational drafting agent (LLM monkeypatched)."""

import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

import pytest  # noqa: E402

from code_review_agents.comments import (  # noqa: E402
    DraftComment,
    DraftResult,
    draft_comments,
    is_abort,
)
from code_review_agents.state import Finding  # noqa: E402

DIFF = """\
diff --git a/app/users.py b/app/users.py
--- a/app/users.py
+++ b/app/users.py
@@ -1,2 +1,4 @@
 import os
+import sys
+x = 1
+y = 2
"""


class _FakeStructured:
    def __init__(self, result):
        self._result = result

    def invoke(self, _messages):
        return self._result


class _FakeLLM:
    """Returns a canned DraftResult via structured output (the project's test pattern)."""

    def __init__(self, *, structured=None, raw=None, raise_structured=False):
        self._structured = structured
        self._raw = raw
        self._raise_structured = raise_structured

    def with_structured_output(self, _schema):
        if self._raise_structured:
            raise RuntimeError("structured output unsupported")
        return _FakeStructured(self._structured)

    def invoke(self, _messages):
        class _Resp:
            content = self._raw

        return _Resp()


def _findings():
    return [Finding(agent="bug", severity="high", title="bug", location="app/users.py")]


def test_is_abort_truth_table():
    for yes in ["abort", "Abort.", "discard", "discard all", "please abort", "cancel", "start over"]:
        assert is_abort(yes) is True, yes
    for no in ["make #2 softer", "drop info-level ones", "add a fix snippet", ""]:
        assert is_abort(no) is False, no


def test_abort_short_circuits_without_calling_llm():
    # If the LLM were called it would blow up; abort must not touch it.
    class _Boom:
        def with_structured_output(self, _):
            raise AssertionError("LLM must not be called on abort")

    out = draft_comments(_findings(), DIFF, [DraftComment(path="app/users.py", line=2, body="x")],
                         [], "abort", llm=_Boom())
    assert out.action == "abort"
    assert out.comments == []


def test_structured_update_revalidates_out_of_range_line():
    # Model returns a line (999) that isn't in the diff -> clamp to first added line (2).
    canned = DraftResult(
        action="update",
        comments=[DraftComment(path="app/users.py", line=999, body="please fix")],
        message="done",
    )
    out = draft_comments(_findings(), DIFF, [], [], "tighten it up", llm=_FakeLLM(structured=canned))
    assert out.action == "update"
    assert out.comments[0].line == 2  # revalidated into the diff
    assert out.comments[0].anchored is True


def test_unknown_path_demoted_to_file_level():
    canned = DraftResult(
        action="update",
        comments=[DraftComment(path="", line=5, body="orphan")],
        message="",
    )
    out = draft_comments(_findings(), DIFF, [], [], "edit", llm=_FakeLLM(structured=canned))
    assert out.comments[0].line is None
    assert out.comments[0].anchored is False


def test_tolerant_fallback_when_structured_output_raises():
    raw = 'Here you go:\n```json\n{"action":"update","comments":[{"path":"app/users.py","line":3,"body":"hi"}],"message":"ok"}\n```'
    llm = _FakeLLM(raise_structured=True, raw=raw)
    out = draft_comments(_findings(), DIFF, [], [], "edit", llm=llm)
    assert out.action == "update"
    assert out.comments[0].body == "hi"
    assert out.comments[0].line == 3  # 3 is a valid added line


def test_unparseable_output_keeps_drafts():
    llm = _FakeLLM(raise_structured=True, raw="sorry, no json here")
    current = [DraftComment(path="app/users.py", line=2, body="keep me")]
    out = draft_comments(_findings(), DIFF, current, [], "edit", llm=llm)
    assert out.action == "noop"
    assert out.comments == current
