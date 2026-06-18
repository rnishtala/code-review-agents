"""Shared machinery for the specialist agents.

Each specialist is a thin LangGraph node that delegates here with its own name and
system prompt. We ask the model for structured output (a :class:`FindingList`), but small
local models are not always reliable at strict JSON, so we fall back to tolerant parsing
and, failing that, degrade gracefully to an informational finding rather than crashing
the whole graph.
"""

from __future__ import annotations

import json
import re

from ..grounding import ground_security_findings, ground_test_findings
from ..llm import make_llm
from ..state import Finding, FindingList, ReviewState

_PROMPT_TEMPLATE = """\
{context_block}{research_block}\
PR summary (shared context):
{summary}

Review the following unified diff. Focus ONLY on your specialty. Report concrete issues
you can point to in the diff; do not invent problems. Use the linked issue context (if any)
to judge whether the change actually addresses the reported problem. For each issue give:
category, severity (critical|high|medium|low|info), a short title, a description of why it
matters, the location, a concrete suggestion, and your confidence (high|medium|low).

For `location`, give the file path and, when you can, the specific changed line as
`path:line` (e.g. `app/users.py:42`) or otherwise `path:function_name`. If you find no
issues, return an empty list.

Unified diff:
{diff}
"""


def _build_prompt(state: ReviewState) -> str:
    context = state.get("context", "").strip()
    research = state.get("research", "").strip()
    context_block = f"Context:\n{context}\n\n" if context else ""
    research_block = f"{research}\n\n" if research else ""
    return _PROMPT_TEMPLATE.format(
        context_block=context_block,
        research_block=research_block,
        summary=state.get("summary", "(no summary available)"),
        diff=state.get("diff", ""),
    )


def _coerce_findings(payload: object) -> list[Finding]:
    """Turn a loosely-shaped object into a list of Finding models."""
    if isinstance(payload, FindingList):
        return payload.findings
    if isinstance(payload, dict):
        items = payload.get("findings", [])
    elif isinstance(payload, list):
        items = payload
    else:
        items = []
    findings: list[Finding] = []
    for item in items:
        if isinstance(item, Finding):
            findings.append(item)
        elif isinstance(item, dict):
            try:
                findings.append(Finding(**item))
            except Exception:  # noqa: BLE001 - skip malformed entries
                continue
    return findings


def _tolerant_json_parse(text: str) -> list[Finding]:
    """Best-effort extraction of findings from a raw model string."""
    if not text:
        return []
    # Strip ```json fences if present.
    fenced = re.search(r"```(?:json)?\s*(.*?)```", text, re.DOTALL)
    candidate = fenced.group(1) if fenced else text
    # Grab the outermost JSON object/array.
    match = re.search(r"[\{\[].*[\}\]]", candidate, re.DOTALL)
    if not match:
        return []
    try:
        return _coerce_findings(json.loads(match.group(0)))
    except (json.JSONDecodeError, TypeError):
        return []


def run_agent(state: ReviewState, *, name: str, system_prompt: str) -> dict:
    """Run one specialist agent and return ``{"findings": [...]}``."""
    prompt = _build_prompt(state)
    findings: list[Finding] = []

    try:
        structured = make_llm().with_structured_output(FindingList)
        result = structured.invoke(
            [("system", system_prompt), ("human", prompt)]
        )
        findings = _coerce_findings(result)
    except Exception:  # noqa: BLE001 - fall back to tolerant parsing
        try:
            raw = make_llm().invoke(
                [
                    (
                        "system",
                        system_prompt
                        + "\n\nRespond ONLY with a JSON object of the form "
                        '{"findings": [...]}.',
                    ),
                    ("human", prompt),
                ]
            )
            findings = _tolerant_json_parse(getattr(raw, "content", "") or "")
        except Exception as exc:  # noqa: BLE001 - degrade gracefully
            findings = [
                Finding(
                    category="agent-error",
                    severity="info",
                    title=f"{name} could not complete its review",
                    description=f"The agent failed to produce parseable output: {exc}",
                    confidence="low",
                )
            ]

    # Drop fabricated findings the diff contradicts (small models hallucinate vuln classes
    # absent from the change, and claim 'missing tests' for behavior the PR already tests).
    if name == "security":
        findings = ground_security_findings(findings, state.get("diff", ""))
    elif name == "test":
        findings = ground_test_findings(findings, state.get("diff", ""))

    # Stamp the agent name so the orchestrator can attribute findings reliably.
    for finding in findings:
        finding.agent = name

    return {"findings": findings}
