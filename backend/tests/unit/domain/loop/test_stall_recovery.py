"""Tests for how a stage row carries the shared stall bookkeeping.

The escalation itself is provider-agnostic and tested in
``tests/unit/domain/agents/test_stall_recovery.py``. What is loop-specific is
the row it is stored on, including rows written before strikes were counted.
"""

from datetime import UTC, datetime
from typing import Any

from src.domain.agents.stall_recovery import StallState
from src.domain.loop.monitor import _stall_state, _store_stall_state

_STAMP = datetime(2026, 8, 13, 12, 0, tzinfo=UTC)


def _row(**values: Any) -> dict[str, Any]:
    """Return a stage row carrying the given recovery bookkeeping."""
    return {"attempt": 1, **values}


def test_a_row_that_never_stalled_has_spent_nothing() -> None:
    assert _stall_state(_row()) == StallState()


def test_strikes_and_stamps_round_trip_through_the_row() -> None:
    state = StallState(strikes=2, recovered_at=_STAMP, interrupted_at=_STAMP)
    row = _row()

    _store_stall_state(row, state)

    assert _stall_state(row) == state


def test_a_cleared_episode_leaves_no_bookkeeping_behind() -> None:
    row = _row(recovery_strikes=2, recovered_at=_STAMP.isoformat())

    _store_stall_state(row, StallState())

    assert "recovery_strikes" not in row
    assert "recovered_at" not in row
    assert _stall_state(row) == StallState()


def test_a_legacy_row_that_used_its_one_recovery_counts_as_one_strike() -> None:
    # Written before recovery counted strikes: a run mid-flight across that
    # change escalates from where it was rather than starting over.
    assert _stall_state(_row(attempt=1, recovered_attempt=1)).strikes == 1


def test_a_legacy_row_from_an_earlier_attempt_has_spent_nothing() -> None:
    assert _stall_state(_row(attempt=2, recovered_attempt=1)).strikes == 0


def test_writing_state_retires_the_pre_strike_field() -> None:
    row = _row(attempt=1, recovered_attempt=1)

    _store_stall_state(row, StallState(strikes=1, recovered_at=_STAMP))

    assert "recovered_attempt" not in row
    assert _stall_state(row).strikes == 1


def test_a_naive_stamp_is_read_as_utc_rather_than_raising() -> None:
    # Stage rows are on-disk state an older build may have written without an
    # offset; comparing naive to aware raises inside the poll loop.
    row = _row(recovery_strikes=1, recovered_at=_STAMP.replace(tzinfo=None).isoformat())

    assert _stall_state(row).recovered_at == _STAMP
