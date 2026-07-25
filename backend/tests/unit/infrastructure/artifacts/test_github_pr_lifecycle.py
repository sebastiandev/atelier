"""Focused contract tests for the GitHub PR lifecycle gateway."""

from __future__ import annotations

import json

import httpx
import pytest

from src.domain.artifacts.pr_status import PrComment, PrRef
from src.infrastructure.artifacts.github_pr_lifecycle import GitHubPrLifecycleGateway


@pytest.fixture
def anyio_backend() -> str:
    return "asyncio"


@pytest.mark.anyio
async def test_fetch_parses_status_checks_review_and_comments() -> None:
    """One GraphQL response becomes the complete lifecycle snapshot."""

    def handler(request: httpx.Request) -> httpx.Response:
        body = json.loads(request.content)
        assert body["variables"] == {"owner": "o", "repo": "r", "number": 7}
        return httpx.Response(
            200,
            headers={"ETag": '"life-v1"'},
            json={
                "data": {
                    "viewer": {"login": "seba"},
                    "repository": {
                        "pullRequest": {
                            "id": "PR_node",
                            "state": "OPEN",
                            "isDraft": True,
                            "mergedAt": None,
                            "reviewDecision": "CHANGES_REQUESTED",
                            "comments": {
                                "nodes": [
                                    {
                                        "id": "IC_1",
                                        "author": {"login": "maria"},
                                        "body": "Please add a test",
                                        "createdAt": "2026-07-20T10:00:00Z",
                                        "url": "https://github.com/o/r/pull/7#issuecomment-1",
                                    }
                                ]
                            },
                            "reviewThreads": {
                                "nodes": [
                                    {
                                        "id": "THREAD_1",
                                        "comments": {
                                            "nodes": [
                                                {
                                                    "id": "RC_1",
                                                    "author": {"login": "devon"},
                                                    "body": "Handle this branch",
                                                    "createdAt": "2026-07-20T10:01:00Z",
                                                    "url": "https://github.com/o/r/pull/7#discussion_r1",
                                                    "path": "src/run.py",
                                                    "line": 42,
                                                    "originalLine": 40,
                                                },
                                                {
                                                    "id": "RC_2",
                                                    "author": {"login": "seba"},
                                                    "body": "Handled in the latest push",
                                                    "createdAt": "2026-07-20T10:02:00Z",
                                                    "url": "https://github.com/o/r/pull/7#discussion_r2",
                                                    "path": "src/run.py",
                                                    "line": 42,
                                                    "originalLine": 40,
                                                },
                                            ]
                                        },
                                    }
                                ]
                            },
                            "commits": {
                                "nodes": [
                                    {
                                        "commit": {
                                            "statusCheckRollup": {
                                                "state": "FAILURE",
                                                "contexts": {
                                                    "totalCount": 3,
                                                    "nodes": [
                                                        {
                                                            "__typename": "CheckRun",
                                                            "status": "COMPLETED",
                                                            "conclusion": "SUCCESS",
                                                        },
                                                        {
                                                            "__typename": "CheckRun",
                                                            "status": "COMPLETED",
                                                            "conclusion": "FAILURE",
                                                        },
                                                        {
                                                            "__typename": "StatusContext",
                                                            "state": "PENDING",
                                                        },
                                                    ],
                                                },
                                            }
                                        }
                                    }
                                ]
                            },
                        }
                    },
                }
            },
        )

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        gateway = GitHubPrLifecycleGateway(client, token_supplier=lambda: "tok")
        result = await gateway.fetch(PrRef("github.com", "o", "r", 7))

    assert result is not None and result.lifecycle is not None
    assert result.etag == '"life-v1"'
    assert result.lifecycle.status == "draft"
    assert result.lifecycle.review_state == "changes_requested"
    assert result.lifecycle.checks.total == 3
    assert result.lifecycle.checks.state == "failed"
    assert [comment.id for comment in result.lifecycle.comments] == ["IC_1", "RC_1", "RC_2"]
    assert result.lifecycle.comments[1].location == "src/run.py:42"
    assert result.lifecycle.comments[1].reply_target_id == "THREAD_1"
    assert result.lifecycle.comments[1].is_viewer is False
    assert result.lifecycle.comments[2].is_viewer is True


@pytest.mark.anyio
async def test_fetch_supports_conditional_and_force_polling() -> None:
    """Conditional requests may return 304; force explicitly omits the ETag."""
    seen: list[str | None] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request.headers.get("If-None-Match"))
        if seen[-1]:
            return httpx.Response(304)
        return httpx.Response(
            200,
            json={
                "data": {
                    "repository": {
                        "pullRequest": {
                            "id": "PR",
                            "state": "OPEN",
                            "isDraft": False,
                            "mergedAt": None,
                            "reviewDecision": None,
                            "comments": {"nodes": []},
                            "reviewThreads": {"nodes": []},
                            "commits": {"nodes": []},
                        }
                    }
                }
            },
        )

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        gateway = GitHubPrLifecycleGateway(client, token_supplier=lambda: "tok")
        cached = await gateway.fetch(PrRef("github.com", "o", "r", 1), if_none_match='"old"')
        forced = await gateway.fetch(
            PrRef("github.com", "o", "r", 1), if_none_match='"old"', force=True
        )

    assert cached is not None and cached.not_modified is True
    assert forced is not None and forced.lifecycle is not None
    assert seen == ['"old"', None]


@pytest.mark.anyio
@pytest.mark.parametrize("kind", ["conversation", "review"])
async def test_reply_uses_the_comment_reply_target(kind: str) -> None:
    """Replies retain the selected PR or review-thread target."""

    def handler(request: httpx.Request) -> httpx.Response:
        body = json.loads(request.content)
        assert body["variables"] == {"target": "TARGET", "body": "fixed"}
        node = {
            "id": "REPLY",
            "author": {"login": "seba"},
            "body": "fixed",
            "createdAt": "2026-07-20T11:00:00Z",
            "url": "https://github.com/reply",
        }
        if kind == "review":
            node.update({"path": "src/run.py", "line": 42, "originalLine": 40})
            data = {"addPullRequestReviewThreadReply": {"comment": node}}
        else:
            data = {"addComment": {"commentEdge": {"node": node}}}
        return httpx.Response(200, json={"data": data})

    comment = PrComment(
        "ORIGINAL",
        "maria",
        None,
        "please fix",
        "2026-07-20T10:00:00Z",
        "https://github.com/original",
        kind,
        "TARGET",  # type: ignore[arg-type]
    )
    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        gateway = GitHubPrLifecycleGateway(client, token_supplier=lambda: "tok")
        result = await gateway.reply(PrRef("github.com", "o", "r", 7), comment, "fixed")

    assert result is not None
    assert result.body == "fixed"
    assert result.reply_target_id == "TARGET"
    assert result.is_viewer is True
