"""Merge specialist findings into a single prioritized markdown report.

This node is pure Python (no LLM call), which keeps it fast, deterministic, and unit
testable offline. It dedupes near-identical findings, sorts by severity then confidence,
computes an overall risk rating, and renders a markdown report that opens with the PR
summary.
"""

from __future__ import annotations

import re

from .state import (
    CONFIDENCE_RANK,
    SEVERITY_RANK,
    Finding,
    ReviewState,
)

_SEVERITY_ORDER = ["critical", "high", "medium", "low", "info"]
_SEVERITY_EMOJI = {
    "critical": "🔴",
    "high": "🟠",
    "medium": "🟡",
    "low": "🔵",
    "info": "⚪",
}


def _normalize(text: str) -> str:
    """Lowercase and collapse whitespace/punctuation for fuzzy comparison."""
    return re.sub(r"[^a-z0-9]+", " ", text.lower()).strip()


def _dedupe(findings: list[Finding]) -> list[Finding]:
    """Drop near-duplicate findings (same location + similar title).

    When duplicates collide we keep the one with the highest severity, breaking ties by
    higher confidence.
    """
    best: dict[tuple[str, str], Finding] = {}
    for finding in findings:
        key = (_normalize(finding.location), _normalize(finding.title))
        existing = best.get(key)
        if existing is None or _sort_key(finding) < _sort_key(existing):
            best[key] = finding
    # Preserve a stable, prioritized order.
    return sorted(best.values(), key=_sort_key)


def _sort_key(finding: Finding) -> tuple[int, int, str]:
    return (
        SEVERITY_RANK.get(finding.severity, 99),
        CONFIDENCE_RANK.get(finding.confidence, 99),
        finding.title.lower(),
    )


def _risk_rating(findings: list[Finding]) -> str:
    """Overall risk derived from the most severe findings present."""
    if not findings:
        return "Minimal"
    severities = {f.severity for f in findings}
    if "critical" in severities:
        return "Critical"
    if "high" in severities:
        return "High"
    if "medium" in severities:
        return "Medium"
    if "low" in severities:
        return "Low"
    return "Minimal"


def _counts_table(findings: list[Finding]) -> str:
    counts = {sev: 0 for sev in _SEVERITY_ORDER}
    for finding in findings:
        counts[finding.severity] = counts.get(finding.severity, 0) + 1
    lines = ["| Severity | Count |", "| --- | --- |"]
    for sev in _SEVERITY_ORDER:
        lines.append(f"| {_SEVERITY_EMOJI[sev]} {sev.capitalize()} | {counts[sev]} |")
    return "\n".join(lines)


def _render_finding(index: int, finding: Finding) -> str:
    bits = [f"#### {index}. {finding.title}"]
    meta = f"*{finding.severity.capitalize()}*"
    if finding.confidence:
        meta += f" · confidence: {finding.confidence}"
    if finding.agent:
        meta += f" · agent: {finding.agent}"
    if finding.category:
        meta += f" · {finding.category}"
    bits.append(meta)
    if finding.location:
        bits.append(f"**Location:** `{finding.location}`")
    if finding.description:
        bits.append(finding.description)
    if finding.suggestion:
        bits.append(f"**Suggestion:** {finding.suggestion}")
    return "\n\n".join(bits)


def render_report(summary: str, findings: list[Finding], research: str = "") -> str:
    """Render the full markdown report. Exposed for offline testing."""
    deduped = _dedupe(findings)
    rating = _risk_rating(deduped)

    sections = [
        "# Code Review Report",
        "## PR Summary",
        summary.strip() or "_No summary available._",
    ]
    if research.strip():
        # `research` already carries its own "## Linked issue & external context" heading.
        sections.append(research.strip())
    sections += [
        "## Overall Risk Rating",
        f"**{rating}** — {len(deduped)} finding(s) after de-duplication.",
        "## Findings by Severity",
        _counts_table(deduped),
    ]

    if not deduped:
        sections.append("No issues were reported by the specialist agents. ✅")
        return "\n\n".join(sections) + "\n"

    counter = 1
    for sev in _SEVERITY_ORDER:
        group = [f for f in deduped if f.severity == sev]
        if not group:
            continue
        sections.append(f"### {_SEVERITY_EMOJI[sev]} {sev.capitalize()}")
        for finding in group:
            sections.append(_render_finding(counter, finding))
            counter += 1

    return "\n\n".join(sections) + "\n"


def orchestrate(state: ReviewState) -> dict:
    """LangGraph node: produce ``state['report']``."""
    findings = state.get("findings", [])
    summary = state.get("summary", "")
    research = state.get("research", "")
    return {"report": render_report(summary, findings, research)}
