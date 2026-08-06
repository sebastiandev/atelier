"""Tests for shared Loop state actions."""

import pytest

from src.domain.loop.actions import (
    approved_command_prefix_from_request,
    claim_stale_permission_recovery,
    command_matches_approved_prefix,
    declared_previous_row,
    latest_changed_file_prompt_lines,
    start_next_pass,
)
from src.domain.loop.dtos import LoopContextKind, LoopContextReference


def test_start_next_pass_persists_incremented_counter() -> None:
    loop: dict[str, object] = {"pass_number": 3}

    assert start_next_pass(loop) == 4
    assert loop["pass_number"] == 4


def test_stale_permission_recovery_is_claimed_once_without_replaying_user_denial() -> None:
    stage: dict[str, object] = {}
    events = [
        {
            "type": "permission_decision",
            "request_id": "expired",
            "decision": "deny",
            "stale": True,
        },
        {
            "type": "permission_decision",
            "request_id": "user-denied",
            "decision": "deny",
        },
    ]

    assert claim_stale_permission_recovery(stage, events) is True
    assert claim_stale_permission_recovery(stage, events) is False
    assert stage["recovered_stale_permission_ids"] == ["expired"]


@pytest.mark.parametrize(
    ("command", "expected"),
    [
        (
            'source .venv/bin/activate\ndt sh -s app-endpoints -c "pytest -q kernel/tests"',
            True,
        ),
        ('dt sh -s app-endpoints -c "pytest -q kernel/tests"', True),
        ("dt start -s app-endpoints", False),
        ("dt sh -s app-endpoints ; rm -rf outside", False),
    ],
)
def test_command_approval_uses_safe_token_prefixes(
    command: str,
    expected: bool,
) -> None:
    assert (
        command_matches_approved_prefix(
            command,
            ("dt sh -s app-endpoints",),
        )
        is expected
    )


def test_always_allow_command_request_yields_conservative_prefix() -> None:
    assert (
        approved_command_prefix_from_request(
            {
                "type": "permission_request",
                "request_id": "permission-1",
                "tool_input": {"command": "git push -u origin feat/looping"},
            }
        )
        == "git push"
    )


def test_latest_changed_files_follow_report_time_across_backward_passes() -> None:
    loop = {
        "stages": [
            {
                "id": "implementation",
                "reports": [
                    {
                        "recorded_at": "2026-07-22T10:00:00+00:00",
                        "changed_files": [{"path": "src/new.py", "additions": 4, "deletions": 1}],
                    }
                ],
            },
            {
                "id": "create-pr",
                "reports": [
                    {
                        "recorded_at": "2026-07-21T10:00:00+00:00",
                        "changed_files": [{"path": "src/old.py", "additions": 1, "deletions": 0}],
                    }
                ],
            },
        ]
    }

    assert latest_changed_file_prompt_lines(loop) == ("src/new.py (+4/-1)",)


def _stage(*steps: str | None):
    class _Stage:
        context = tuple(
            LoopContextReference(kind=LoopContextKind.PREVIOUS_REPORT, step=step)
            for step in steps
        )

    return _Stage()


def test_declared_previous_row_returns_the_named_stage() -> None:
    loop = {
        "stages": [
            {"id": "implementation", "summary": "Built the thing."},
            {"id": "lint", "summary": "Fixed imports."},
        ]
    }

    row = declared_previous_row(loop, _stage("implementation"))

    assert row is not None
    assert row["summary"] == "Built the thing."


def test_declared_previous_row_is_none_without_an_explicit_step() -> None:
    loop = {"stages": [{"id": "implementation", "summary": "Built the thing."}]}

    assert declared_previous_row(loop, _stage(None)) is None


def test_declared_previous_row_is_none_when_the_named_stage_has_no_row() -> None:
    loop = {"stages": [{"id": "lint", "summary": "Fixed imports."}]}

    assert declared_previous_row(loop, _stage("implementation")) is None


def test_declared_previous_row_ignores_other_context_kinds() -> None:
    class _Stage:
        context = (
            LoopContextReference(kind=LoopContextKind.WORKSPACE_DIFF, step="implementation"),
        )

    loop = {"stages": [{"id": "implementation", "summary": "Built the thing."}]}

    assert declared_previous_row(loop, _Stage()) is None
