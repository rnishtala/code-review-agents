"""Offline tests for pending-review payload construction and submission (requests mocked)."""

import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

import pytest  # noqa: E402

import code_review_agents.comments as comments  # noqa: E402
from code_review_agents.comments import (  # noqa: E402
    DraftComment,
    build_pending_review_payload,
    submit_pending_review,
)


def test_payload_line_comments_and_file_level_to_body():
    drafts = [
        DraftComment(path="a.py", line=10, body="line comment"),
        DraftComment(path="b.py", line=None, body="file comment"),
    ]
    payload = build_pending_review_payload(drafts, "sha123")
    assert payload["commit_id"] == "sha123"
    assert "event" not in payload  # pending review: never auto-submitted
    # Line comment is anchored; file-level comment is folded into the review body.
    assert payload["comments"] == [
        {"path": "a.py", "line": 10, "side": "RIGHT", "body": "line comment"}
    ]
    assert "subject_type" not in payload["comments"][0]  # invalid for create-review
    assert "`b.py`: file comment" in payload["body"]


def test_payload_only_file_level_uses_body_no_comments():
    drafts = [DraftComment(path="b.py", line=None, body="whole-file note")]
    payload = build_pending_review_payload(drafts, "sha")
    assert "comments" not in payload  # nothing line-anchored
    assert "whole-file note" in payload["body"]


def test_payload_drops_excluded_and_pathless():
    drafts = [
        DraftComment(path="a.py", line=1, body="keep"),
        DraftComment(path="a.py", line=2, body="excluded", included=False),
        DraftComment(path="", line=3, body="no path"),
    ]
    payload = build_pending_review_payload(drafts, "sha")
    assert len(payload["comments"]) == 1
    assert payload["comments"][0]["body"] == "keep"


def test_payload_empty_raises():
    with pytest.raises(ValueError):
        build_pending_review_payload([DraftComment(path="a.py", line=1, body="x", included=False)], "sha")


class _Resp:
    def __init__(self, status=200, payload=None):
        self.status_code = status
        self._payload = payload or {}

    def raise_for_status(self):
        if self.status_code >= 400:
            raise RuntimeError(f"HTTP {self.status_code}")

    def json(self):
        return self._payload


def test_submit_posts_pending_review(monkeypatch):
    captured = {}

    def fake_post(url, headers=None, json=None, timeout=None):
        captured["url"] = url
        captured["headers"] = headers
        captured["json"] = json
        return _Resp(201, {"id": 99, "html_url": "https://github.com/o/r/pull/5#pullrequestreview-99"})

    monkeypatch.setattr(comments.requests, "post", fake_post)

    drafts = [DraftComment(path="a.py", line=10, body="hi")]
    out = submit_pending_review("o", "r", 5, drafts, head_sha="abc")

    assert captured["url"] == "https://api.github.com/repos/o/r/pulls/5/reviews"
    assert captured["json"]["commit_id"] == "abc"
    assert "event" not in captured["json"]  # stays PENDING
    assert "Accept" in captured["headers"]  # came from _headers()
    assert out["id"] == 99


def test_find_pending_review(monkeypatch):
    def fake_get(url, headers=None, timeout=None):
        return _Resp(200, [
            {"id": 1, "state": "APPROVED"},
            {"id": 2, "state": "PENDING"},
        ])

    monkeypatch.setattr(comments.requests, "get", fake_get)
    from code_review_agents.comments import find_pending_review

    found = find_pending_review("o", "r", 5)
    assert found["id"] == 2


def test_submit_replace_existing_deletes_then_posts(monkeypatch):
    calls = []

    def fake_get(url, headers=None, timeout=None):
        if url.endswith("/reviews"):
            return _Resp(200, [{"id": 77, "state": "PENDING"}])
        return _Resp(200, {"head": {"sha": "s"}})  # head sha lookup

    def fake_delete(url, headers=None, timeout=None):
        calls.append(("delete", url))
        return _Resp(200)

    def fake_post(url, headers=None, json=None, timeout=None):
        calls.append(("post", url))
        return _Resp(201, {"id": 78, "html_url": "x"})

    monkeypatch.setattr(comments.requests, "get", fake_get)
    monkeypatch.setattr(comments.requests, "delete", fake_delete)
    monkeypatch.setattr(comments.requests, "post", fake_post)

    drafts = [DraftComment(path="a.py", line=1, body="b")]
    submit_pending_review("o", "r", 5, drafts, head_sha="s", replace_existing=True)

    # The stale pending review (77) is deleted before the new one is posted.
    assert calls[0] == ("delete", "https://api.github.com/repos/o/r/pulls/5/reviews/77")
    assert calls[1][0] == "post"


def test_submit_fetches_head_sha_when_missing(monkeypatch):
    def fake_get(url, headers=None, timeout=None):
        assert url.endswith("/pulls/5")
        return _Resp(200, {"head": {"sha": "fetched-sha"}})

    posted = {}

    def fake_post(url, headers=None, json=None, timeout=None):
        posted["commit_id"] = json["commit_id"]
        return _Resp(201, {"id": 1, "html_url": "x"})

    monkeypatch.setattr(comments.requests, "get", fake_get)
    monkeypatch.setattr(comments.requests, "post", fake_post)

    submit_pending_review("o", "r", 5, [DraftComment(path="a.py", line=1, body="b")])
    assert posted["commit_id"] == "fetched-sha"
