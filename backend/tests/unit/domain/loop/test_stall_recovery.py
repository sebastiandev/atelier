"""Tests for how a stalled stage's recovery budget is counted."""

from datetime import UTC, datetime, timedelta
from typing import Any

import pytest

from src.domain.loop.monitor import _agent_moved_since_recovery, _recovery_strikes

_RECOVERED_AT = datetime(2026, 8, 12, 12, 0, tzinfo=UTC)


def _event(event_type: str, *, after_seconds: int) -> dict[str, Any]:
    """Return one transcript event offset from the recovery stamp."""
    return {
        "type": event_type,
        "ts": (_RECOVERED_AT + timedelta(seconds=after_seconds)).isoformat(),
    }


def _row(**values: Any) -> dict[str, Any]:
    """Return a stage row carrying the given recovery bookkeeping."""
    return {"attempt": 1, **values}


def test_a_row_that_never_stalled_has_spent_nothing() -> None:
    assert _recovery_strikes(_row()) == 0


def test_strikes_are_read_back_as_written() -> None:
    assert _recovery_strikes(_row(recovery_strikes=2)) == 2


def test_a_legacy_row_that_used_its_one_recovery_counts_as_one_strike() -> None:
    # Written before recovery counted strikes: a run mid-flight across that
    # change escalates from where it was rather than starting over.
    assert _recovery_strikes(_row(attempt=1, recovered_attempt=1)) == 1


def test_a_legacy_row_from_an_earlier_attempt_has_spent_nothing() -> None:
    assert _recovery_strikes(_row(attempt=2, recovered_attempt=1)) == 0


def test_the_loops_own_nudge_is_not_the_agent_moving() -> None:
    row = _row(recovered_at=_RECOVERED_AT.isoformat())

    assert not _agent_moved_since_recovery(row, [_event("user_input", after_seconds=1)])


def test_the_loops_own_interrupt_is_not_the_agent_moving() -> None:
    # The interrupt lands in the same transcript as the agent's own events.
    # Reading it as progress reset the counter and looped the ladder forever.
    row = _row(recovered_at=_RECOVERED_AT.isoformat())

    assert not _agent_moved_since_recovery(row, [_event("user_stop", after_seconds=1)])


@pytest.mark.parametrize(
    "event_type",
    ["status_change", "error", "permission_request", "session_established", "turn_metrics"],
)
def test_what_an_interrupt_stirs_up_is_not_the_agent_moving(event_type: str) -> None:
    # A cancelled turn settles noisily. Every one of these is newer than the
    # recovery stamp without the agent having produced a thing, and a denylist
    # of the loop's own two event types let all of them reset the ladder.
    row = _row(recovered_at=_RECOVERED_AT.isoformat())

    assert not _agent_moved_since_recovery(row, [_event(event_type, after_seconds=1)])


@pytest.mark.parametrize(
    "event_type",
    ["message_delta", "message_complete", "thinking_delta", "tool_call", "tool_result"],
)
def test_any_agent_output_counts_as_the_agent_moving(event_type: str) -> None:
    row = _row(recovered_at=_RECOVERED_AT.isoformat())

    assert _agent_moved_since_recovery(row, [_event(event_type, after_seconds=1)])


def test_a_naive_stamp_is_read_as_utc_rather_than_raising() -> None:
    # Stage rows are on-disk state an older build may have written without an
    # offset; comparing naive to aware raises inside the poll loop.
    row = _row(recovered_at=_RECOVERED_AT.replace(tzinfo=None).isoformat())

    assert _agent_moved_since_recovery(row, [_event("message_complete", after_seconds=1)])


def test_agent_output_after_the_recovery_is_the_agent_moving() -> None:
    row = _row(recovered_at=_RECOVERED_AT.isoformat())

    assert _agent_moved_since_recovery(row, [_event("message_complete", after_seconds=1)])


def test_agent_output_from_before_the_recovery_is_not_progress() -> None:
    row = _row(recovered_at=_RECOVERED_AT.isoformat())

    assert not _agent_moved_since_recovery(row, [_event("message_complete", after_seconds=-1)])


def test_a_stage_with_no_recovery_stamp_has_no_progress_to_measure() -> None:
    assert not _agent_moved_since_recovery(_row(), [_event("message_complete", after_seconds=1)])
