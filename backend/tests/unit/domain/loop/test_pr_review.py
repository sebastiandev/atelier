"""Tests for synchronizing PR review state into a loop run."""

from __future__ import annotations

import pytest

from src.domain.artifacts.pr_status import (
    FetchedPrLifecycle,
    PrCheckSummary,
    PrComment,
    PrLifecycle,
    PrRef,
)
from src.domain.loop import pr_review
from src.domain.loop.models import LoopRunTarget


@pytest.fixture
def anyio_backend() -> str:
    return "asyncio"


class _Gateway:
    """Return one stable lifecycle and record contextual replies."""

    def __init__(
        self,
        comment: PrComment,
        *,
        head_sha: str = "",
        head_commit_url: str = "",
    ) -> None:
        self.comment = comment
        self.head_sha = head_sha
        self.head_commit_url = head_commit_url
        self.fetches: list[tuple[str | None, bool]] = []
        self.replies: list[str] = []

    async def fetch(
        self,
        ref: PrRef,
        *,
        if_none_match: str | None = None,
        force: bool = False,
    ) -> FetchedPrLifecycle:
        self.fetches.append((if_none_match, force))
        return FetchedPrLifecycle(
            lifecycle=PrLifecycle(
                status="open",
                checks=PrCheckSummary("passed", 2, 2, 0, 0),
                review_state="changes_requested",
                comments=(self.comment,),
                title="Handle empty transfers",
                head_branch="feat/empty-transfers",
                base_branch="main",
                head_sha=self.head_sha,
                head_commit_url=self.head_commit_url,
            ),
            etag='"review-v2"',
            not_modified=False,
        )

    async def reply(
        self,
        ref: PrRef,
        comment: PrComment,
        body: str,
    ) -> PrComment:
        self.replies.append(body)
        return PrComment(
            id="reply-1",
            author="atelier",
            location=comment.location,
            body=body,
            created_at="2026-07-20T12:00:00Z",
            url="https://github.com/acme/repo/pull/7#reply-1",
            kind=comment.kind,
            reply_target_id=comment.reply_target_id,
            is_viewer=True,
        )


@pytest.mark.anyio
async def test_refresh_merges_review_and_replies_to_addressed_comment_once() -> None:
    comment = PrComment(
        id="comment-1",
        author="reviewer",
        location="src/app.py:12",
        body="Handle the empty case.",
        created_at="2026-07-20T10:00:00Z",
        url="https://github.com/acme/repo/pull/7#discussion_r1",
        kind="review",
        reply_target_id="thread-1",
    )
    gateway = _Gateway(comment)
    target = LoopRunTarget(
        work_slug="WRK-016",
        run_id="run-6",
        target_id="story-1",
        title="Handle transfers",
        source_ref="story-1.md",
        run={
            "loop": {
                "pr": {
                    "url": "https://github.com/acme/repo/pull/7",
                    "etag": '"review-v1"',
                },
                "pr_comments": [
                    {
                        "id": "comment-1",
                        "addressed_in_pass": 2,
                    }
                ],
                "stages": [
                    {
                        "id": "implement",
                        "addressed_comments": [
                            {
                                "comment_id": "comment-1",
                                "instruction": "Add a regression test.",
                            }
                        ],
                    }
                ],
            }
        },
    )

    await pr_review.refresh(target, gateway)
    await pr_review.refresh(target, gateway, force=True)

    pr = target.run["loop"]["pr"]
    comments = target.run["loop"]["pr_comments"]
    assert pr["status"] == "open"
    assert pr["checks"] == {
        "state": "passed",
        "total": 2,
        "passed": 2,
        "failed": 0,
        "pending": 0,
    }
    assert pr["title"] == "Handle empty transfers"
    assert pr["branch"] == "feat/empty-transfers"
    assert pr["base"] == "main"
    assert gateway.fetches == [('"review-v1"', False), (None, True)]
    assert gateway.replies == [
        "Addressed in Atelier pass 2. Applied instruction: Add a regression test."
    ]
    assert comments[0]["reply_posted_at"]
    assert [row["id"] for row in comments] == ["comment-1", "reply-1"]
    assert comments[1]["is_viewer"] is True


@pytest.mark.anyio
async def test_post_addressed_replies_does_not_wait_for_a_refresh() -> None:
    comment = PrComment(
        id="comment-1",
        author="reviewer",
        location="src/app.py:12",
        body="Handle the empty case.",
        created_at="2026-07-20T10:00:00Z",
        url="https://github.com/acme/repo/pull/7#discussion_r1",
        kind="review",
        reply_target_id="thread-1",
    )
    gateway = _Gateway(comment)
    target = LoopRunTarget(
        work_slug="WRK-016",
        run_id="run-6",
        target_id="story-1",
        title="Handle transfers",
        source_ref="story-1.md",
        run={
            "loop": {
                "pr": {"url": "https://github.com/acme/repo/pull/7"},
                "pr_comments": [
                    {
                        "id": comment.id,
                        "author": comment.author,
                        "location": comment.location,
                        "body": comment.body,
                        "created_at": comment.created_at,
                        "url": comment.url,
                        "kind": comment.kind,
                        "reply_target_id": comment.reply_target_id,
                        "addressed_in_pass": 2,
                    }
                ],
                "stages": [
                    {
                        "id": "create-pr",
                        "addressed_comments": [
                            {
                                "comment_id": comment.id,
                                "instruction": "Add a regression test.",
                            }
                        ],
                    }
                ],
            }
        },
    )

    await pr_review.post_addressed_replies(target, gateway)
    await pr_review.post_addressed_replies(target, gateway)

    comments = target.run["loop"]["pr_comments"]
    assert gateway.fetches == []
    assert gateway.replies == [
        "Addressed in Atelier pass 2. Applied instruction: Add a regression test."
    ]
    assert [row["id"] for row in comments] == ["comment-1", "reply-1"]


def _target_with_addressed_comment() -> LoopRunTarget:
    return LoopRunTarget(
        work_slug="WRK-016",
        run_id="run-6",
        target_id="story-1",
        title="Handle transfers",
        source_ref="story-1.md",
        run={
            "loop": {
                "pr": {"url": "https://github.com/acme/repo/pull/7"},
                "pr_comments": [{"id": "comment-1", "addressed_in_pass": 2}],
                "stages": [{"id": "implement", "addressed_comments": []}],
            }
        },
    )


def _reviewer_comment() -> PrComment:
    return PrComment(
        id="comment-1",
        author="reviewer",
        location="src/app.py:12",
        body="Handle the empty case.",
        created_at="2026-07-20T10:00:00Z",
        url="https://github.com/acme/repo/pull/7#discussion_r1",
        kind="review",
        reply_target_id="thread-1",
    )


@pytest.mark.anyio
async def test_reply_points_at_the_commit_that_carried_the_change() -> None:
    """A reviewer can act on a commit link; "pass 3" means nothing to them."""
    gateway = _Gateway(
        _reviewer_comment(),
        head_sha="9fceb02d1b2c3d4e5f60718293a4b5c6d7e8f901",
        head_commit_url="https://github.com/acme/repo/commit/9fceb02",
    )
    target = _target_with_addressed_comment()

    await pr_review.refresh(target, gateway)

    assert gateway.replies == [
        "Addressed in [`9fceb02`](https://github.com/acme/repo/commit/9fceb02)."
    ]


@pytest.mark.anyio
async def test_reply_uses_the_bare_sha_when_the_commit_url_is_unknown() -> None:
    gateway = _Gateway(_reviewer_comment(), head_sha="9fceb02d1b2c3d4e5")
    target = _target_with_addressed_comment()

    await pr_review.refresh(target, gateway)

    assert gateway.replies == ["Addressed in `9fceb02`."]


@pytest.mark.anyio
async def test_reply_falls_back_to_the_pass_number_without_a_head_commit() -> None:
    """Older runs have no head commit recorded; they still get a reply."""
    gateway = _Gateway(_reviewer_comment())
    target = _target_with_addressed_comment()

    await pr_review.refresh(target, gateway)

    assert gateway.replies == ["Addressed in Atelier pass 2."]


@pytest.mark.anyio
async def test_refresh_records_the_head_commit_on_the_run() -> None:
    gateway = _Gateway(
        _reviewer_comment(),
        head_sha="9fceb02d1b2c",
        head_commit_url="https://github.com/acme/repo/commit/9fceb02",
    )
    target = _target_with_addressed_comment()

    await pr_review.refresh(target, gateway)

    pr = target.run["loop"]["pr"]
    assert pr["head_sha"] == "9fceb02d1b2c"
    assert pr["head_commit_url"] == "https://github.com/acme/repo/commit/9fceb02"


@pytest.mark.anyio
async def test_refresh_keeps_one_copy_of_the_lifecycle() -> None:
    """The run holds the PR; stages and reports do not copy it.

    Three copies used to exist and the run view rendered the least
    maintained one, so the panel showed the PR as it looked when the stage
    finished -- stale checks, stale review state, no head commit.
    """
    gateway = _Gateway(
        _reviewer_comment(),
        head_sha="9fceb02d1b2c",
        head_commit_url="https://github.com/acme/repo/commit/9fceb02",
    )
    target = LoopRunTarget(
        work_slug="WRK-016",
        run_id="run-6",
        target_id="story-1",
        title="Handle transfers",
        source_ref="story-1.md",
        run={
            "loop": {
                "pr": {"url": "https://github.com/acme/repo/pull/7"},
                "pr_comments": [],
                "stages": [
                    {
                        "id": "create-pr",
                        "kind": "pr",
                        "push_at": "2026-07-20T09:00:00Z",
                        "reports": [{"pass_number": 1}],
                    }
                ],
            }
        },
    )

    await pr_review.refresh(target, gateway)

    loop = target.run["loop"]
    assert loop["pr"]["head_sha"] == "9fceb02d1b2c"
    assert loop["pr"]["checks"]["total"] == 2
    stage = loop["stages"][0]
    assert "pr" not in stage
    assert "pr" not in stage["reports"][-1]
    # What a pass pushed is per-pass, and survives.
    assert stage["push_at"] == "2026-07-20T09:00:00Z"
