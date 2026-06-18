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

import re

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


# A "missing test" claim is generic noise when the PR already adds tests, UNLESS it cites a
# specific uncovered case (which a 3B model occasionally gets right and we want to keep).
_MISSING_TEST_RE = re.compile(r"missing test|no (?:corresponding )?test|untested|lacks? test", re.I)
# Concrete uncovered cases worth keeping. NOTE: the bare phrase "edge case(s)" is deliberately
# excluded — it's generic boilerplate ("...including edge cases"); a genuinely specific finding
# names the actual condition (empty input, null, boundary, an exception, …).
_SPECIFIC_GAP_RE = re.compile(
    r"empty|boundary|error path|exception|null|none\b|negative|invalid|overflow|"
    r"off-by-one|race condition|timeout|unicode|encoding|zero|nan\b|out of range",
    re.I,
)
# Added test definitions across languages: Python `def test_x`, Go `func TestX`, Rust/JS
# `fn test_x` / `function testX`, etc. — any def/func/fn/function whose name contains "test".
_ADDED_TEST_DEF_RE = re.compile(r"\b(?:def|func|fn|function)\s+(\w*[Tt]est\w*)")
# Paths that are test files across ecosystems.
_TEST_PATH_RE = re.compile(
    r"(?:^|/)tests?/|_test\.[a-z]+$|test_[^/]*\.py$|\.test\.[jt]sx?$|\.spec\.[jt]sx?$|"
    r"conftest\.py$|Test\w*\.java$|_spec\.rb$",
    re.I,
)


def _diff_adds_tests(diff: str) -> tuple[set[str], bool]:
    """Return (names of added test functions, whether any test file gains added lines)."""
    added_defs: set[str] = set()
    adds_in_test_file = False
    current = ""
    for ln in diff.splitlines():
        if ln.startswith("+++ "):
            path = ln[4:].strip()
            current = path[2:] if path.startswith("b/") else path
        elif ln.startswith("+") and not ln.startswith("+++"):
            if _TEST_PATH_RE.search(current):
                adds_in_test_file = True
            added_defs.update(_ADDED_TEST_DEF_RE.findall(ln[1:]))
    return added_defs, adds_in_test_file


def ground_test_findings(findings: list[Finding], diff: str) -> list[Finding]:
    """Drop test-coverage findings the diff contradicts.

    Two deterministic checks (small models routinely claim 'missing tests' for behavior the
    same PR already tests — even citing the added test functions by name). Test detection is
    language-aware (Python/Go/Rust/JS/Java/Ruby) via added test-function defs and test-file
    paths, so it works beyond Python repos:

    1. Drop a finding that names a test function the diff *adds* — the test it says is
       missing literally exists in the change.
    2. If the PR adds tests, drop generic 'missing tests' findings — unless they cite a
       specific uncovered case (edge case, error path, empty input, …), which we keep.
    """
    added_defs, adds_in_test_file = _diff_adds_tests(diff)
    pr_adds_tests = bool(added_defs) or adds_in_test_file

    kept: list[Finding] = []
    for finding in findings:
        text = f"{finding.title} {finding.description}"
        named = set(re.findall(r"[A-Za-z_][A-Za-z0-9_]+", text))
        if added_defs & named:  # (1) names an already-added test
            continue
        if (
            pr_adds_tests
            and _MISSING_TEST_RE.search(text)
            and not _SPECIFIC_GAP_RE.search(text)
        ):  # (2) generic 'missing tests' but the PR adds tests
            continue
        kept.append(finding)
    return kept
