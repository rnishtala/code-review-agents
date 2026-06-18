"""Drop security findings that aren't grounded in the diff.

Small models tend to walk a vulnerability checklist and emit findings for classes that
have no footprint in the change at all — e.g. "SSRF" or "weak cryptography" on a diff that
touches neither network nor crypto code (observed on pallets/click#3578 with a 3B model).

This is a deterministic guard: if a finding invokes a vulnerability *concept* but the diff
contains none of that concept's evidence tokens, the finding is almost certainly fabricated,
so we drop it. Evidence tokens are intentionally specific to avoid substring false positives
(e.g. plain "auth" would match "author"). Findings that don't invoke any tracked concept are
always kept — this only prunes obvious hallucinations, it doesn't second-guess real findings.
"""

from __future__ import annotations

from .state import Finding

# concept-substring (matched in the finding text) -> evidence substrings (looked for in diff)
_VULN_EVIDENCE: dict[str, list[str]] = {
    "ssrf": ["http", "urllib", "requests", "socket", "urlopen", "fetch"],
    "csrf": ["csrf", "cookie", "session", "request.form"],
    "cryptograph": ["hashlib", "crypto", "ssl", "tls", "encrypt", "decrypt", "cipher",
                    "md5", "sha1", "sha256", "secret"],
    "https": ["http", "ssl", "tls", "urlopen", "requests"],
    "sql": ["select ", "insert ", "update ", "delete ", "cursor", "execute(", ".sql",
            "sqlite", "sqlalchemy"],
    "injection": ["execute(", "subprocess", "os.system", "eval(", "exec(", "cursor",
                  "shell=true", "popen"],
    "deserial": ["pickle", "yaml.load", "marshal", "__reduce__"],
    "pickle": ["pickle"],
    "authentic": ["login", "password", "credential", "authenticate", "session", "token", "oauth"],
    "authoriz": ["permission", "role", "authoriz", "access control", "login_required", "is_admin"],
    "xss": ["<html", "render_template", "innerhtml", "mark_safe", "escape(", "|safe"],
    "traversal": ["os.path", "pathlib", "../", "abspath", "realpath", "open("],
}


def ground_security_findings(findings: list[Finding], diff: str) -> list[Finding]:
    """Return only findings whose invoked vulnerability concepts have evidence in the diff."""
    haystack = diff.lower()
    kept: list[Finding] = []
    for finding in findings:
        text = f"{finding.title} {finding.description} {finding.category}".lower()
        fabricated = any(
            concept in text and not any(tok in haystack for tok in evidence)
            for concept, evidence in _VULN_EVIDENCE.items()
        )
        if not fabricated:
            kept.append(finding)
    return kept
