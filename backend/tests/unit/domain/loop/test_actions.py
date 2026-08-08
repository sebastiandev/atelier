"""Tests for shared Loop state actions."""

import pytest

from src.domain.loop.actions import (
    approved_command_prefix_from_request,
    claim_stale_permission_recovery,
    command_matches_approved_prefix,
    declared_report_rows,
    latest_changed_file_prompt_lines,
    start_next_pass,
    unresolved_report_warnings,
)
from src.domain.loop.dtos import LoopReportReference


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


def _stage(*sources: str):
    class _Stage:
        reports = tuple(LoopReportReference(from_stage=source) for source in sources)

    return _Stage()


_LOOP = {
    "stages": [
        {"id": "implementation", "summary": "Built the thing."},
        {"id": "lint", "summary": "Fixed imports."},
    ]
}


def test_a_named_report_resolves_to_that_stage() -> None:
    rows = declared_report_rows(_LOOP, _stage("implementation"), "lint")

    assert [(step, row["summary"]) for step, row in rows] == [
        ("implementation", "Built the thing.")
    ]


def test_several_reports_resolve_in_declaration_order() -> None:
    """A reviewer judging whether a correction answered the original finding
    needs both accounts, which the single previous_report could never carry."""
    rows = declared_report_rows(_LOOP, _stage("implementation", "lint"), "")

    assert [step for step, _ in rows] == ["implementation", "lint"]


def test_previous_resolves_to_the_stage_that_reported_into_this_one() -> None:
    rows = declared_report_rows(_LOOP, _stage("previous"), "lint")

    assert [step for step, _ in rows] == ["lint"]


def test_a_previous_that_repeats_a_named_stage_renders_once() -> None:
    rows = declared_report_rows(_LOOP, _stage("implementation", "previous"), "implementation")

    assert [step for step, _ in rows] == ["implementation"]


def test_a_stage_declaring_no_report_resolves_none() -> None:
    assert declared_report_rows(_LOOP, _stage(), "implementation") == []


def test_a_report_naming_a_stage_with_no_row_is_skipped() -> None:
    assert declared_report_rows(_LOOP, _stage("security-review"), "") == []


def test_a_previous_with_nothing_to_resolve_against_is_skipped() -> None:
    assert declared_report_rows(_LOOP, _stage("previous"), "") == []


def test_a_required_report_that_was_never_produced_warns() -> None:
    """Neither the stage nor the user can conjure an account of work that was
    never reported, so this says so plainly rather than blocking the run."""
    warnings = unresolved_report_warnings(_LOOP, _stage("security-review"), "")

    assert warnings == ["required report from security-review: never reported"]


def test_an_optional_report_that_is_absent_says_nothing() -> None:
    class _Stage:
        reports = (LoopReportReference(from_stage="security-review", required=False),)

    assert unresolved_report_warnings(_LOOP, _Stage(), "") == []


def test_a_resolved_required_report_warns_about_nothing() -> None:
    assert unresolved_report_warnings(_LOOP, _stage("implementation"), "") == []
