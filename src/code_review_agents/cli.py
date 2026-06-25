"""Command-line entrypoint for the multi-agent code reviewer.

Examples
--------
    # Review a local unified diff (offline; needs a local Ollama running):
    python -m code_review_agents.cli --diff samples/sample.diff --out report.md

    # Review a specific GitHub PR:
    python -m code_review_agents.cli --pr https://github.com/owner/repo/pull/42

    # Pick a PR from a repo's open PRs:
    python -m code_review_agents.cli --repo-url https://github.com/owner/repo
    python -m code_review_agents.cli --repo-url owner/repo --pr-number 42
"""

from __future__ import annotations

import argparse
import os
import sys

from .diff_input import (
    DiffBundle,
    fetch_pr_diff,
    list_open_prs,
    load_local_diff,
    parse_pr_url,
    parse_repo_url,
)
from .graph import build_graph
from .llm import (
    configure_tracing,
    get_base_url,
    get_model_name,
    has_langsmith_key,
    tracing_enabled,
)


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="code_review_agents",
        description="Multi-agent code review (LangGraph + local Ollama).",
    )
    source = parser.add_mutually_exclusive_group(required=True)
    source.add_argument("--pr", metavar="URL", help="GitHub pull request URL.")
    source.add_argument(
        "--repo-url",
        metavar="REPO",
        help="GitHub repo URL or 'owner/repo'; lists open PRs to pick from.",
    )
    source.add_argument(
        "--diff", metavar="PATH", help="Path to a local unified diff file."
    )
    parser.add_argument(
        "--pr-number",
        type=int,
        help="With --repo-url, review this PR number instead of prompting.",
    )
    parser.add_argument(
        "--out",
        metavar="PATH",
        help="Write the report here (default: stdout).",
    )
    parser.add_argument(
        "--model",
        help="Override the Ollama model for this run (default: $OLLAMA_MODEL).",
    )
    parser.add_argument(
        "--trace",
        action="store_true",
        help="Send an end-to-end trace to LangSmith (opt-in). NOTE: this uploads prompts, "
        "the diff, and model outputs to LangSmith. Requires a LangSmith API key.",
    )
    return parser


def _inject_trace_link(report: str, url: str) -> str:
    """Add a `**LangSmith trace:** <url>` line near the top of the report.

    Placed right after the `**Pull request:**` line when present, otherwise just under the
    `# Code Review Report` title.
    """
    line = f"**LangSmith trace:** {url}"
    lines = report.split("\n")
    for i, ln in enumerate(lines):
        if ln.startswith("**Pull request:**"):
            lines.insert(i + 1, "")
            lines.insert(i + 2, line)
            return "\n".join(lines)
    if lines and lines[0].startswith("# "):
        lines.insert(1, "")
        lines.insert(2, line)
        return "\n".join(lines)
    return f"{line}\n\n{report}"


def _select_pr(owner: str, repo: str, requested: int | None) -> int:
    """Resolve a PR number from a repo, prompting if needed."""
    if requested is not None:
        return requested

    prs = list_open_prs(owner, repo)
    if not prs:
        raise SystemExit(f"No open pull requests found in {owner}/{repo}.")
    if len(prs) == 1:
        only = prs[0]
        print(f"Only one open PR — selecting #{only.number}: {only.title}", file=sys.stderr)
        return only.number

    print(f"Open pull requests in {owner}/{repo}:", file=sys.stderr)
    for pr in prs:
        print(f"  #{pr.number:<5} {pr.title}  (@{pr.author})", file=sys.stderr)
    while True:
        try:
            choice = input("Enter a PR number to review: ").strip()
        except EOFError:
            raise SystemExit("No PR number provided (non-interactive). Use --pr-number.")
        if choice.lstrip("#").isdigit():
            return int(choice.lstrip("#"))
        print("Please enter a numeric PR number.", file=sys.stderr)


def _load_input(args: argparse.Namespace) -> DiffBundle:
    if args.diff:
        return load_local_diff(args.diff)
    if args.pr:
        owner, repo, number = parse_pr_url(args.pr)
        return fetch_pr_diff(owner, repo, number)
    # --repo-url
    owner, repo = parse_repo_url(args.repo_url)
    number = _select_pr(owner, repo, args.pr_number)
    return fetch_pr_diff(owner, repo, number)


def main(argv: list[str] | None = None) -> int:
    args = _build_parser().parse_args(argv)

    if args.model:
        os.environ["OLLAMA_MODEL"] = args.model
    if args.pr_number is not None and not args.repo_url:
        print("--pr-number only applies with --repo-url; ignoring.", file=sys.stderr)

    # Tracing is off by default for privacy; --trace (or CODE_REVIEW_ENABLE_TRACING=1) opts in.
    if args.trace:
        os.environ["CODE_REVIEW_ENABLE_TRACING"] = "1"
    if configure_tracing():
        project = os.environ.get("LANGCHAIN_PROJECT", "code-review-agents")
        if has_langsmith_key():
            print(
                f"LangSmith tracing ON → project '{project}'. "
                "NOTE: prompts, the diff, and model outputs are sent to LangSmith.",
                file=sys.stderr,
            )
        else:
            print(
                "LangSmith tracing requested but no LANGSMITH_API_KEY / LANGCHAIN_API_KEY "
                "found — traces will not upload.",
                file=sys.stderr,
            )

    bundle = _load_input(args)
    if bundle.truncated:
        print("Warning: the diff was truncated before review.", file=sys.stderr)

    # Surface the source PR at the top of the report. Use the URL the user gave verbatim;
    # otherwise reconstruct the canonical github.com URL from the resolved PR coordinates.
    pr_url = args.pr or ""
    if not pr_url and bundle.owner and bundle.repo and bundle.number:
        pr_url = f"https://github.com/{bundle.owner}/{bundle.repo}/pull/{bundle.number}"

    print(
        f"Reviewing with model '{get_model_name()}' at {get_base_url()} ...",
        file=sys.stderr,
    )

    app = build_graph()
    source_label = pr_url or args.diff or f"{bundle.owner}/{bundle.repo}#{bundle.number}"
    # run_name / tags / metadata make the LangSmith trace a single, labeled end-to-end run.
    # Harmless when tracing is off.
    run_config = {
        "run_name": f"code-review: {source_label}",
        "tags": ["code-review-agents"],
        "metadata": {"model": get_model_name(), "source": source_label},
    }
    inputs = {
        "diff": bundle.diff,
        "context": bundle.context,
        "owner": bundle.owner,
        "repo": bundle.repo,
        "number": bundle.number,
        "pr_url": pr_url,
        "findings": [],
    }

    # When tracing, run inside the LangSmith context so we can capture the run's URL and
    # surface it in the report. Falls back to a plain run if anything about it goes wrong.
    trace_url = ""
    if tracing_enabled():
        try:
            from langchain_core.tracers.context import tracing_v2_enabled

            project = os.environ.get("LANGCHAIN_PROJECT", "code-review-agents")
            with tracing_v2_enabled(project_name=project) as cb:
                final_state = app.invoke(inputs, config=run_config)
            try:
                trace_url = cb.get_run_url()
            except Exception:  # noqa: BLE001 - URL is best-effort; the trace still uploaded
                trace_url = ""
        except Exception:  # noqa: BLE001 - never let tracing break the review
            final_state = app.invoke(inputs, config=run_config)
    else:
        final_state = app.invoke(inputs, config=run_config)

    report = final_state.get("report", "(no report produced)")
    if trace_url:
        report = _inject_trace_link(report, trace_url)
        print(f"LangSmith trace: {trace_url}", file=sys.stderr)

    if args.out:
        with open(args.out, "w", encoding="utf-8") as fh:
            fh.write(report)
        print(f"Report written to {args.out}", file=sys.stderr)
    else:
        print(report)

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
