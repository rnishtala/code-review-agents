"""Streamlit app: run a review on a PR, draft inline comments, iterate via chat, submit a draft.

Flow: enter a PR URL -> run the multi-agent review live -> comments are drafted (anchored to
diff lines, best-effort) -> edit cards and/or chat with the drafting agent to refine ("make #2
softer", "drop info-level", "abort") -> click Submit to create a PENDING GitHub review you
finalize on github.com. Nothing is ever auto-submitted.

All business logic lives in ``code_review_agents.comments`` and the review pipeline; this file
is wiring + session state only. Launch with::

    streamlit run src/code_review_agents/ui/streamlit_app.py
    # or, after `pip install -e ".[ui]"`:
    code-review-ui
"""

from __future__ import annotations

import sys
from pathlib import Path


def render() -> None:
    """Build the Streamlit page. Imported lazily so the module is importable without streamlit."""
    import requests
    import streamlit as st

    from code_review_agents.comments import (
        anchor_findings,
        draft_comments,
        fetch_pr_head_sha,
        is_abort,
        parse_diff,
        revalidate_lines,
        submit_pending_review,
    )
    from code_review_agents.diff_input import fetch_pr_diff, parse_pr_url
    from code_review_agents.graph import review_diff

    st.set_page_config(page_title="PR Review Comments", layout="wide")
    st.title("🧑‍⚖️ Draft PR Review Comments")
    st.caption(
        "Runs the multi-agent review locally, drafts inline comments, and lets you iterate "
        "before submitting a **pending** (draft) review you finalize on GitHub."
    )

    ss = st.session_state
    ss.setdefault("review", None)
    ss.setdefault("pr", None)  # (owner, repo, number, head_sha)
    ss.setdefault("drafts", [])
    ss.setdefault("chat", [])  # list[(role, text)]
    ss.setdefault("aborted", False)
    ss.setdefault("rev", 0)  # bumped when drafts are replaced, so widgets re-seed
    ss.setdefault("status", None)

    # --- 1. Run a review ---------------------------------------------------
    with st.form("run"):
        url = st.text_input("Pull request URL", placeholder="https://github.com/owner/repo/pull/123")
        run = st.form_submit_button("Run review", type="primary")
    if run and url.strip():
        try:
            owner, repo, number = parse_pr_url(url.strip())
            with st.spinner("Fetching diff and running the multi-agent review…"):
                bundle = fetch_pr_diff(owner, repo, number)
                state = review_diff(bundle.diff, bundle.context, owner=owner, repo=repo, number=number)
                head_sha = fetch_pr_head_sha(owner, repo, number)
            ss.review = {**state, "diff": bundle.diff, "truncated": bundle.truncated}
            ss.pr = (owner, repo, number, head_sha)
            ss.drafts = anchor_findings(state.get("findings", []), bundle.diff)
            ss.chat = []
            ss.aborted = False
            ss.rev += 1
            ss.status = None
            if bundle.truncated:
                st.warning("Diff was truncated before review; later-file comments may be file-level.")
        except Exception as exc:  # noqa: BLE001 - surface any failure to the user
            st.error(f"Could not run the review: {exc}")

    if ss.review is None:
        st.info("Enter a PR URL above to begin.")
        return

    owner, repo, number, head_sha = ss.pr

    # --- 2. Review panel ---------------------------------------------------
    with st.expander("Review report", expanded=False):
        st.markdown(ss.review.get("report", "_No report._"))

    left, right = st.columns([3, 2])

    # --- 3. Editable comment cards ----------------------------------------
    with left:
        st.subheader("Draft comments")
        if ss.aborted:
            st.info("All drafts were discarded. Re-run the review or refine again to start over.")
        files = parse_diff(ss.review.get("diff", ""))
        for i, d in enumerate(ss.drafts, 1):
            badge = f"{d.severity}" + ("" if d.anchored else " · file-level")
            with st.container(border=True):
                top = st.columns([1, 4, 2])
                d.included = top[0].checkbox("Include", value=d.included, key=f"inc_{ss.rev}_{d.id}")
                d.path = top[1].text_input("Path", value=d.path, key=f"path_{ss.rev}_{d.id}")
                file_level = top[2].checkbox(
                    "File-level", value=d.line is None, key=f"fl_{ss.rev}_{d.id}",
                    help="Comment on the whole file instead of a specific line.",
                )
                if file_level:
                    d.line = None
                else:
                    val = st.number_input(
                        "Line (RIGHT side)", min_value=1, value=d.line or 1, step=1,
                        key=f"line_{ss.rev}_{d.id}",
                    )
                    d.line = int(val)
                d.body = st.text_area("Comment", value=d.body, key=f"body_{ss.rev}_{d.id}", height=140)
                st.caption(f"#{i} · {badge} · {d.mapping_note}")
        # Re-validate any user-entered lines against the diff before they can be submitted.
        revalidate_lines(ss.drafts, files)

    # --- 4. Chat: iterate / abort -----------------------------------------
    with right:
        st.subheader("Refine via chat")
        st.caption("e.g. “make #2 softer”, “drop info-level ones”, “add a fix snippet to #1”, “abort”.")
        for role, text in ss.chat:
            with st.chat_message(role):
                st.write(text)
        instruction = st.chat_input("Tell the agent how to change the comments…")
        if instruction:
            ss.chat.append(("user", instruction))
            if is_abort(instruction):
                ss.drafts = []
                ss.aborted = True
                ss.chat.append(("assistant", "Discarded all draft comments."))
                ss.rev += 1
                st.rerun()
            with st.spinner("Updating drafts…"):
                result = draft_comments(
                    ss.review.get("findings", []),
                    ss.review.get("diff", ""),
                    ss.drafts,
                    ss.chat,
                    instruction,
                )
            if result.action == "abort":
                ss.drafts = []
                ss.aborted = True
            elif result.action == "update":
                ss.drafts = result.comments
                ss.aborted = False
            ss.chat.append(("assistant", result.message))
            ss.rev += 1
            st.rerun()

    # --- 5. Submit as a pending (draft) review ----------------------------
    st.divider()
    n_incl = sum(1 for d in ss.drafts if d.included and d.path)
    st.subheader("Submit")
    st.write(f"**{n_incl}** comment(s) will be included in a **pending** review on {owner}/{repo}#{number}.")
    confirm = st.checkbox(
        "I understand this creates a PENDING review on GitHub that I must finalize manually.",
        key="confirm_submit",
    )
    disabled = n_incl == 0 or not confirm or ss.aborted
    submit = st.button("Submit as draft review", type="primary", disabled=disabled)
    if disabled:
        reasons = []
        if ss.aborted:
            reasons.append("drafts were discarded — refine again to recreate them")
        elif n_incl == 0:
            reasons.append("no comments are currently included with a file path — tick **Include** on at least one comment (a refine may have excluded them all)")
        if not confirm:
            reasons.append("the confirmation checkbox above is unchecked")
        st.caption("Submit is disabled because: " + "; ".join(reasons) + ".")
    if submit:
        try:
            with st.spinner("Creating pending review on GitHub…"):
                review = submit_pending_review(owner, repo, number, ss.drafts, head_sha=head_sha)
            html_url = review.get("html_url", f"https://github.com/{owner}/{repo}/pull/{number}")
            st.success("Pending review created — open it on GitHub to finalize and submit.")
            st.markdown(f"➡️ [Open your pending review]({html_url})")
        except requests.HTTPError as exc:  # noqa: BLE001
            code = getattr(exc.response, "status_code", "?")
            detail = ""
            try:
                detail = exc.response.json().get("message", "")
            except Exception:  # noqa: BLE001
                pass
            if code in (401, 403):
                st.error(
                    f"GitHub rejected the request ({code}): {detail}. "
                    "Your GITHUB_TOKEN needs Pull requests **write** access (classic `repo` scope "
                    "or fine-grained Pull requests: Read and write)."
                )
            elif code == 422:
                st.error(
                    f"GitHub could not create the review (422): {detail}. "
                    "You may already have a pending review on this PR — finalize or dismiss it first."
                )
            else:
                st.error(f"GitHub error ({code}): {detail or exc}")
        except Exception as exc:  # noqa: BLE001
            st.error(f"Could not submit: {exc}")


def main() -> None:
    """Console-script entrypoint: launch this file under `streamlit run`."""
    import subprocess

    script = str(Path(__file__).resolve())
    raise SystemExit(
        subprocess.call([sys.executable, "-m", "streamlit", "run", script, *sys.argv[1:]])
    )


# Streamlit executes this file as __main__; the console script calls main() instead.
if __name__ == "__main__":
    render()
