"""Loading diffs and PR context from GitHub or local files.

Three input shapes are supported by the CLI:
  * ``--pr <github PR url>``    -> :func:`fetch_pr_diff`
  * ``--repo-url <github repo>`` -> :func:`list_open_prs` then :func:`fetch_pr_diff`
  * ``--diff <path>``          -> :func:`load_local_diff`

Only the GitHub modes touch the network. A ``GITHUB_TOKEN`` is optional but raises rate
limits and allows private repos. Model inference is always local and unaffected by this.
"""

from __future__ import annotations

import os
import re
from dataclasses import dataclass

import requests

GITHUB_API = "https://api.github.com"
# Above this size we still review, but warn that the tail was truncated so a small local
# model is not overwhelmed (and to stay under its context window).
MAX_DIFF_CHARS = 60_000

_PR_URL_RE = re.compile(
    r"github\.com/(?P<owner>[^/]+)/(?P<repo>[^/]+)/pull/(?P<number>\d+)"
)
_REPO_URL_RE = re.compile(
    r"github\.com/(?P<owner>[^/]+)/(?P<repo>[^/]+?)(?:\.git)?/?$"
)


@dataclass
class DiffBundle:
    """A diff plus the human context that frames it for the agents."""

    diff: str
    context: str
    truncated: bool = False


@dataclass
class PullRequestRef:
    number: int
    title: str
    author: str


def _headers(accept: str) -> dict[str, str]:
    headers = {"Accept": accept, "X-GitHub-Api-Version": "2022-11-28"}
    token = os.environ.get("GITHUB_TOKEN")
    if token:
        headers["Authorization"] = f"Bearer {token}"
    return headers


def parse_pr_url(url: str) -> tuple[str, str, int]:
    """Extract (owner, repo, number) from a GitHub PR URL."""
    match = _PR_URL_RE.search(url)
    if not match:
        raise ValueError(f"Not a recognizable GitHub PR URL: {url!r}")
    return match["owner"], match["repo"], int(match["number"])


def parse_repo_url(url: str) -> tuple[str, str]:
    """Extract (owner, repo) from a GitHub repo URL or 'owner/repo' shorthand."""
    shorthand = re.fullmatch(r"(?P<owner>[^/\s]+)/(?P<repo>[^/\s]+)", url.strip())
    if shorthand:
        return shorthand["owner"], shorthand["repo"].removesuffix(".git")
    match = _REPO_URL_RE.search(url)
    if not match:
        raise ValueError(f"Not a recognizable GitHub repo URL: {url!r}")
    return match["owner"], match["repo"]


def _truncate(diff: str) -> tuple[str, bool]:
    if len(diff) <= MAX_DIFF_CHARS:
        return diff, False
    head = diff[:MAX_DIFF_CHARS]
    note = "\n\n... [diff truncated for review — too large to send to a local model] ...\n"
    return head + note, True


def list_open_prs(owner: str, repo: str) -> list[PullRequestRef]:
    """Return open PRs for a repo (most recently updated first)."""
    resp = requests.get(
        f"{GITHUB_API}/repos/{owner}/{repo}/pulls",
        headers=_headers("application/vnd.github+json"),
        params={"state": "open", "sort": "updated", "direction": "desc", "per_page": 50},
        timeout=30,
    )
    resp.raise_for_status()
    return [
        PullRequestRef(
            number=pr["number"],
            title=pr.get("title", ""),
            author=(pr.get("user") or {}).get("login", "unknown"),
        )
        for pr in resp.json()
    ]


def fetch_pr_diff(owner: str, repo: str, number: int) -> DiffBundle:
    """Fetch a PR's unified diff plus its title/body for context."""
    base = f"{GITHUB_API}/repos/{owner}/{repo}/pulls/{number}"

    diff_resp = requests.get(
        base, headers=_headers("application/vnd.github.v3.diff"), timeout=30
    )
    diff_resp.raise_for_status()
    diff, truncated = _truncate(diff_resp.text)

    meta_resp = requests.get(
        base, headers=_headers("application/vnd.github+json"), timeout=30
    )
    meta_resp.raise_for_status()
    meta = meta_resp.json()

    context = (
        f"Pull request #{number} in {owner}/{repo}\n"
        f"Title: {meta.get('title', '')}\n"
        f"Author: {(meta.get('user') or {}).get('login', 'unknown')}\n"
        f"Description:\n{(meta.get('body') or '(no description provided)').strip()}"
    )
    return DiffBundle(diff=diff, context=context, truncated=truncated)


def load_local_diff(path: str) -> DiffBundle:
    """Load a unified diff from a local file."""
    with open(path, "r", encoding="utf-8") as fh:
        raw = fh.read()
    diff, truncated = _truncate(raw)
    context = f"Local unified diff loaded from {path!r}."
    return DiffBundle(diff=diff, context=context, truncated=truncated)
