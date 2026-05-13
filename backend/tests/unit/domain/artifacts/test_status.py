"""Status-vocabulary validation tests.

Each artifact type now has its own per-type enum (PR/Doc/Jira) and the
shared ``validate_status`` dispatcher routes a (type, value) pair to
the matching frozenset. These tests pin the contract so future
additions (a new artifact type, a renamed status) trip a test failure
rather than silently widening the wire.
"""

from __future__ import annotations

import pytest

from src.domain.artifacts import (
    DOC_STATUSES,
    JIRA_STATUSES,
    PR_STATUSES,
    InvalidStatus,
    validate_status,
)


def test_pr_status_vocabulary() -> None:
    assert PR_STATUSES == frozenset({"draft", "open", "merged", "closed"})


def test_doc_status_vocabulary_excludes_published() -> None:
    """``published`` was the old doc terminal value; the new derived
    vocabulary collapses it into ``committed`` via the v10 migration."""
    assert DOC_STATUSES == frozenset({"draft", "pending", "committed"})
    assert "published" not in DOC_STATUSES


def test_jira_status_vocabulary_includes_closed() -> None:
    assert JIRA_STATUSES == frozenset(
        {"todo", "in_progress", "in_review", "done", "closed", "blocked"}
    )


@pytest.mark.parametrize("status", ["draft", "open", "merged", "closed"])
def test_validate_status_accepts_each_pr_status(status: str) -> None:
    validate_status("pr", status)  # must not raise


@pytest.mark.parametrize("status", ["draft", "pending", "committed"])
def test_validate_status_accepts_each_doc_status(status: str) -> None:
    validate_status("doc", status)


@pytest.mark.parametrize(
    "status",
    ["todo", "in_progress", "in_review", "done", "closed", "blocked"],
)
def test_validate_status_accepts_each_jira_status(status: str) -> None:
    validate_status("jira", status)


def test_validate_rejects_mismatched_status() -> None:
    """PR statuses can't leak into Doc and vice versa — the per-type
    chip vocabularies are independent."""
    with pytest.raises(InvalidStatus, match="invalid 'doc' status"):
        validate_status("doc", "open")
    with pytest.raises(InvalidStatus, match="invalid 'pr' status"):
        validate_status("pr", "committed")
    with pytest.raises(InvalidStatus, match="invalid 'doc' status"):
        validate_status("doc", "published")  # legacy value


def test_validate_rejects_unknown_type() -> None:
    with pytest.raises(InvalidStatus, match="unknown artifact type"):
        validate_status("ticket", "open")
