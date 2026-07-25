"""URL parser + terminal-status predicate."""

from __future__ import annotations

import pytest

from src.domain.artifacts.pr_status import (
    PrComment,
    PrRef,
    is_terminal_pr_status,
    parse_pr_url,
    reply_to_pr_comment,
)


def test_parse_canonical_github_pr_url() -> None:
    ref = parse_pr_url("https://github.com/owner/repo/pull/123")
    assert ref == PrRef(host="github.com", owner="owner", repo="repo", number=123)


def test_parse_accepts_trailing_slash_and_query() -> None:
    ref = parse_pr_url("https://github.com/owner/repo/pull/123/?utm=x")
    assert ref is not None
    assert ref.number == 123


def test_parse_accepts_files_subpath() -> None:
    """Agents sometimes paste deep-links (``/files``, ``/commits``). The
    parser should still extract the canonical PR number."""
    ref = parse_pr_url("https://github.com/owner/repo/pull/42/files")
    assert ref is not None
    assert ref.number == 42


def test_parse_accepts_url_without_scheme() -> None:
    ref = parse_pr_url("github.com/owner/repo/pull/7")
    assert ref is not None
    assert ref.host == "github.com"


def test_parse_rejects_gitlab_url() -> None:
    """Today only GitHub is wired. Non-GitHub hosts return None so the
    poller skips them silently — no spurious 'failed' logs."""
    assert parse_pr_url("https://gitlab.com/owner/repo/-/merge_requests/1") is None


def test_parse_rejects_issue_url() -> None:
    """Issues share the ``/owner/repo/`` shape but use ``/issues/``;
    must not match as a PR."""
    assert parse_pr_url("https://github.com/owner/repo/issues/123") is None


def test_parse_rejects_garbage() -> None:
    assert parse_pr_url("not a url") is None
    assert parse_pr_url("") is None


def test_parse_rejects_zero_number() -> None:
    assert parse_pr_url("https://github.com/owner/repo/pull/0") is None


def test_parse_rejects_non_string() -> None:
    assert parse_pr_url(None) is None  # type: ignore[arg-type]


@pytest.mark.parametrize("status", ["merged", "closed"])
def test_is_terminal_for_merged_and_closed(status: str) -> None:
    assert is_terminal_pr_status(status) is True


@pytest.mark.parametrize("status", ["open", "draft", ""])
def test_is_terminal_false_for_active_states(status: str) -> None:
    assert is_terminal_pr_status(status) is False


@pytest.mark.anyio
async def test_reply_action_rejects_empty_body() -> None:
    """A blank reply never reaches the external provider boundary."""
    comment = PrComment(
        "id",
        "author",
        None,
        "body",
        "2026-07-20T10:00:00Z",
        "url",
        "conversation",
        "target",
    )
    with pytest.raises(ValueError, match="must not be empty"):
        await reply_to_pr_comment(object(), PrRef("github.com", "o", "r", 1), comment, "  ")  # type: ignore[arg-type]
