"""The security grounding guard drops vuln findings with no footprint in the diff."""

import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

from code_review_agents.grounding import ground_security_findings  # noqa: E402
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
