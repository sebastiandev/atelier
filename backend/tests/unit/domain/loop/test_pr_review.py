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

    def __init__(self, comment: PrComment) -> None:
        self.comment = comment
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
