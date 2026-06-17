"""Offline tests for diff parsing and finding-to-line mapping (no LLM, no network)."""

import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

from code_review_agents.comments import (  # noqa: E402
    anchor_findings,
    map_finding,
    parse_diff,
)
from code_review_agents.state import Finding  # noqa: E402

# A real-shape unified diff: two files, removed lines (which must NOT advance the new-file
# counter), added lines, and a deletion-only file.
DIFF = """\
diff --git a/app/users.py b/app/users.py
--- a/app/users.py
+++ b/app/users.py
@@ -1,4 +1,6 @@
 import os
+import sys
 def find_user(name):
-    return None
+    q = "SELECT * FROM users WHERE name = '" + name + "'"
+    return q
diff --git a/old.py b/old.py
--- a/old.py
+++ /dev/null
@@ -1,2 +0,0 @@
-print("gone")
-print("really gone")
diff --git a/utils/calc.py b/utils/calc.py
--- a/utils/calc.py
+++ b/utils/calc.py
@@ -10,3 +12,4 @@
 def average(xs):
     total = sum(xs)
+    # divide by zero risk
     return total / len(xs)
"""


def test_parse_computes_right_side_line_numbers():
    files = {f.path: f for f in parse_diff(DIFF)}
    users = files["app/users.py"]
    # new file lines: 1 import os(ctx), 2 import sys(+), 3 def find_user(ctx),
    # removed "return None" advances nothing, 4 q=...(+), 5 return q(+)
    assert users.added_lines() == [2, 4, 5]
    assert users.valid_lines() == {1, 2, 3, 4, 5}


def test_deletion_to_dev_null_is_excluded():
    paths = [f.path for f in parse_diff(DIFF)]
    assert "old.py" not in paths  # +++ /dev/null => nothing to anchor


def test_second_hunk_offset_respected():
    files = {f.path: f for f in parse_diff(DIFF)}
    calc = files["utils/calc.py"]
    # hunk starts at new line 12: 12 def(ctx),13 total(ctx),14 comment(+),15 return(ctx)
    assert calc.added_lines() == [14]
    assert calc.valid_lines() == {12, 13, 14, 15}


def test_map_finding_by_symbol():
    files = parse_diff(DIFF)
    f = Finding(severity="high", title="SQL injection", location="app/users.py: find_user")
    cand = map_finding(f, files)
    assert cand.path == "app/users.py"
    # Anchors to the `def find_user` line (line 3, a context line — a valid RIGHT-side anchor).
    assert cand.line == 3
    assert cand.anchored is True


def test_map_finding_unmatched_file():
    files = parse_diff(DIFF)
    f = Finding(severity="low", title="something", location="nonexistent/thing.py")
    cand = map_finding(f, files)
    assert cand.path == ""
    assert cand.line is None
    assert cand.anchored is False


def test_map_finding_basename_match():
    files = parse_diff(DIFF)
    # Location names only the basename; should still match utils/calc.py.
    f = Finding(severity="high", title="zero division", location="calc.py: average")
    cand = map_finding(f, files)
    assert cand.path == "utils/calc.py"


def test_anchor_findings_seeds_and_defaults():
    findings = [
        Finding(agent="security", severity="critical", title="SQLi", location="app/users.py: find_user",
                suggestion="Use parameters"),
        Finding(agent="test", severity="info", title="doc it", location="app/users.py"),
    ]
    drafts = anchor_findings(findings, DIFF)
    assert len(drafts) == 2
    assert drafts[0].included is True and drafts[0].severity == "critical"
    assert "Use parameters" in drafts[0].body
    assert drafts[0].source_finding_id == "security:0"
    assert drafts[1].included is False  # info defaults to opt-in
