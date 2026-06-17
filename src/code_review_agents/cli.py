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
from .llm import get_base_url, get_model_name


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
    return parser


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

    bundle = _load_input(args)
    if bundle.truncated:
        print("Warning: the diff was truncated before review.", file=sys.stderr)

    print(
        f"Reviewing with model '{get_model_name()}' at {get_base_url()} ...",
        file=sys.stderr,
    )

    app = build_graph()
    final_state = app.invoke(
        {
            "diff": bundle.diff,
            "context": bundle.context,
            "owner": bundle.owner,
            "repo": bundle.repo,
            "number": bundle.number,
            "findings": [],
        }
    )
    report = final_state.get("report", "(no report produced)")

    if args.out:
        with open(args.out, "w", encoding="utf-8") as fh:
            fh.write(report)
        print(f"Report written to {args.out}", file=sys.stderr)
    else:
        print(report)

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
