#!/usr/bin/env python
"""Pre-compute knowledge-graph context for the golden PR scenarios.

This is the Stage-2 bridge between the two repos. It runs in the
**org-knowledge-graph-rag (rag-app) environment** — which has Neo4j + that repo's
`retrieve()` — and writes a `{scenario_id: kg_context}` JSON that
`code_review_agents.golden_eval` (running in *this* repo's venv) loads and injects as
`knowledge=` into the pipeline. Caching to JSON keeps the two repos decoupled (no Neo4j
dependency here) and makes the benchmark reproducible without a live graph.

Run it from the rag-app checkout, e.g.:

    cd /path/to/org-knowledge-graph-rag/rag-app
    source .venv/bin/activate
    python /path/to/code-review-agents/scripts/build_kg_context.py \
        --scenarios data/opentelemetry/golden_pr_scenarios.json \
        --out /path/to/code-review-agents/data/kg_context.json

For each scenario it runs `retrieve(scenario["retrieval_query"])` and stores a **compact
facts-only block** (relationship triples + component stability) — deliberately NOT the
retriever's full `context_block()`, whose document chunks overflow a small local model's
context window and make it flood or collapse. Only KG facts are exported — never the
scenario's expected findings or keys.
"""

from __future__ import annotations

import argparse
import json
import sys

# Cap facts so the injected block stays small enough for a 3B's context window.
MAX_FACTS = 30


def _facts_block(retrieved) -> str:
    facts = retrieved.facts[:MAX_FACTS]
    if not facts:
        return ""
    lines = "\n".join(f"- {f}" for f in facts)
    return "## Knowledge-graph facts (component stability, ownership, dependencies)\n" + lines


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--scenarios", required=True, help="path to golden_pr_scenarios.json")
    parser.add_argument("--out", required=True, help="path to write kg_context.json")
    args = parser.parse_args(argv)

    try:
        from src.retriever import retrieve  # rag-app module; needs that repo's venv
    except Exception as exc:  # noqa: BLE001
        print(
            "error: could not import the rag-app retriever. Run this from the "
            f"org-knowledge-graph-rag checkout with its venv active.\n  ({exc})",
            file=sys.stderr,
        )
        return 2

    with open(args.scenarios, encoding="utf-8") as fh:
        scenarios = json.load(fh)["scenarios"]

    out: dict[str, str] = {}
    for sc in scenarios:
        query = sc.get("retrieval_query") or sc.get("title", "")
        try:
            out[sc["id"]] = _facts_block(retrieve(query))
            print(f"  ✓ {sc['id']}")
        except Exception as exc:  # noqa: BLE001
            print(f"  ✗ {sc['id']}: {exc}", file=sys.stderr)
            out[sc["id"]] = ""

    with open(args.out, "w", encoding="utf-8") as fh:
        json.dump(out, fh, indent=2)
    print(f"\nwrote {len(out)} scenarios -> {args.out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
