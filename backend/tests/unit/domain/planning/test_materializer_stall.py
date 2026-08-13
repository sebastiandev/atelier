"""Tests for the materializer's stall budget and its bounds."""

from src.domain.commands.planning.materialize import (
    _MATERIALIZER_STALL_POLICY,
    _MAX_CONNECTION_RECOVERIES,
    _MAX_IDLE_FOLLOW_UPS,
)


def test_the_materializer_is_far_more_patient_than_a_loop_stage() -> None:
    # Materializing a plan means reading a repository and writing epics, which
    # is quiet, long work. Five minutes of silence is normal here.
    assert _MATERIALIZER_STALL_POLICY.silence_seconds == 20 * 60


def test_the_ladder_is_bounded_even_at_its_widest() -> None:
    # The poll is re-entered by the outer follow-up/recovery loop. The stall
    # budget is carried across those re-entries rather than restarting, or the
    # "did not restart after N attempts" message would understate the real
    # bound several times over.
    re_entries = _MAX_IDLE_FOLLOW_UPS + _MAX_CONNECTION_RECOVERIES + 1
    worst_case_hours = (
        _MATERIALIZER_STALL_POLICY.max_strikes
        * _MATERIALIZER_STALL_POLICY.silence_seconds
        / 3600
    )

    assert re_entries > 1, "the budget would be trivially bounded otherwise"
    assert worst_case_hours <= 1
