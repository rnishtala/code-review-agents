"""The security grounding guard drops vuln findings with no footprint in the diff."""

import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

from code_review_agents.grounding import (  # noqa: E402
    ground_security_findings,
    ground_test_findings,
)
from code_review_agents.state import Finding  # noqa: E402

# A diff with NO network / crypto / sql / auth footprint (à la pallets/click#3578).
HARMLESS_DIFF = """\
diff --git a/src/click/core.py b/src/click/core.py
--- a/src/click/core.py
+++ b/src/click/core.py
@@ -1,3 +1,4 @@
 def make_metavar(self):
-    return "[" + var + "]"
+    # reuse the type's existing brackets instead of doubling them
+    return var if var.startswith("[") else "[" + var + "]"
"""

# A diff that genuinely touches network + crypto code.
RISKY_DIFF = """\
diff --git a/app/net.py b/app/net.py
--- a/app/net.py
+++ b/app/net.py
@@ -1,2 +1,4 @@
+import requests, hashlib
+def fetch(u): return requests.get(u)
+def digest(p): return hashlib.md5(p).hexdigest()
"""


def _f(title, desc="", category="security"):
    return Finding(severity="medium", title=title, description=desc, category=category)


def test_drops_fabricated_vulns_on_harmless_diff():
    findings = [
        _f("SSRF (Server-Side Request Forgery)", "could lead to SSRF attacks"),
        _f("Weak or misused cryptography", "does not use HTTPS to protect data"),
        _f("Missing authentication or authorization checks", "no authentication before commands"),
        _f("Unsafe handling of untrusted input", "vulnerable to injection attacks"),
    ]
    kept = ground_security_findings(findings, HARMLESS_DIFF)
    assert kept == []  # none have any footprint in the diff


def test_keeps_real_vulns_when_evidence_present():
    findings = [
        _f("SSRF via user-controlled URL", "requests.get(u) hits an attacker URL"),
        _f("Weak cryptography: MD5", "hashlib.md5 used for hashing"),
    ]
    kept = ground_security_findings(findings, RISKY_DIFF)
    assert len(kept) == 2  # both grounded in the diff


def test_keeps_findings_that_invoke_no_tracked_concept():
    # A generic finding not naming a tracked vuln class is always kept.
    findings = [_f("Unvalidated index access", "list index may be out of range")]
    kept = ground_security_findings(findings, HARMLESS_DIFF)
    assert len(kept) == 1


# Diff that ADDS tests (the pallets/click#3578 situation).
DIFF_ADDS_TESTS = """\
diff --git a/tests/test_basic.py b/tests/test_basic.py
--- a/tests/test_basic.py
+++ b/tests/test_basic.py
@@ -569,3 +569,8 @@
+def test_choice_argument_optional_metavar(runner):
+    assert "[[foo|bar|baz]]" not in out
+def test_datetime_argument_optional_metavar(runner):
+    assert ok
"""


def test_drops_finding_naming_an_added_test():
    findings = [_f("Missing tests for `test_choice_argument_optional_metavar` function",
                   "no test exists", category="untested")]
    assert ground_test_findings(findings, DIFF_ADDS_TESTS) == []


def test_drops_generic_missing_tests_when_pr_adds_tests():
    findings = [_f("Missing tests for new functionality",
                   "there are no corresponding tests", category="untested")]
    assert ground_test_findings(findings, DIFF_ADDS_TESTS) == []


def test_keeps_specific_gap_even_when_pr_adds_tests():
    # A concrete uncovered case survives — the PR added tests but maybe not this edge case.
    findings = [_f("Empty-list edge case untested",
                   "no test exercises average_score([]) which raises ZeroDivisionError",
                   category="untested")]
    kept = ground_test_findings(findings, DIFF_ADDS_TESTS)
    assert len(kept) == 1


def test_keeps_missing_test_finding_when_pr_adds_no_tests():
    findings = [_f("Missing tests", "no test for the new helper", category="untested")]
    # Diff adds no test functions -> the claim stands.
    assert len(ground_test_findings(findings, HARMLESS_DIFF)) == 1


# A Go PR that adds a test file + test function (the otel#36400 situation).
GO_DIFF_ADDS_TESTS = """\
diff --git a/pkg/ottl/ottlfuncs/func_trim_test.go b/pkg/ottl/ottlfuncs/func_trim_test.go
--- /dev/null
+++ b/pkg/ottl/ottlfuncs/func_trim_test.go
@@ -0,0 +1,5 @@
+func TestTrim(t *testing.T) {
+    // basic cases
+}
"""


def test_detects_go_added_tests():
    # Before the multi-language fix this diff looked test-free (no 'def test_').
    findings = [_f("Missing Test Cases for Trim", "add more test cases", category="untested")]
    assert ground_test_findings(findings, GO_DIFF_ADDS_TESTS) == []


def test_generic_edge_cases_boilerplate_dropped_but_concrete_kept():
    # "including edge cases" is generic boilerplate -> dropped when the PR adds tests;
    # a concrete condition ("empty input") is specific -> kept.
    generic = [_f("Missing Test Cases", "add more tests, including edge cases and error handling",
                  category="untested")]
    concrete = [_f("Missing test", "no test for empty input which raises", category="untested")]
    assert ground_test_findings(generic, GO_DIFF_ADDS_TESTS) == []
    assert len(ground_test_findings(concrete, GO_DIFF_ADDS_TESTS)) == 1


def test_detects_test_file_path_even_without_recognized_def():
    # Added lines in a *_test.go file count as "PR adds tests" even if no def matched.
    diff = (
        "diff --git a/x_test.go b/x_test.go\n+++ b/x_test.go\n@@ -0,0 +1,2 @@\n"
        "+func TestX(t *testing.T) { assertSomething() }\n"
    )
    findings = [_f("No tests", "the change is not tested", category="untested")]
    assert ground_test_findings(findings, diff) == []
