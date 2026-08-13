"""Tests for the shared stalled-turn escalation policy."""

from datetime import UTC, datetime, timedelta
from typing import Any

import pytest

from src.domain.agents.stall_recovery import (
    StallAction,
    StallPolicy,
    StallState,
    next_stall_action,
    record_nudge,
)
from src.domain.agents.turn_monitor import TurnObservation

_NOW = datetime(2026, 8, 13, 12, 0, tzinfo=UTC)
_POLICY = StallPolicy(silence_seconds=300)


def _observation(
    *,
    silent_for: int = 600,
    finished: bool = False,
    tool_in_flight: bool = False,
    waiting_permission: bool = False,
) -> TurnObservation:
    """Return an observation whose last activity is ``silent_for`` seconds old."""
    return TurnObservation(
        terminal_error=None,
        finished=finished,
        started_at=_NOW - timedelta(seconds=silent_for),
        last_activity_at=_NOW - timedelta(seconds=silent_for),
        elapsed_seconds=float(silent_for),
        waiting_permission=waiting_permission,
        tool_in_flight=tool_in_flight,
    )


def _decide(
    state: StallState,
    *,
    events: list[dict[str, Any]] | None = None,
    policy: StallPolicy = _POLICY,
    **observation: Any,
) -> Any:
    """Run one decision against a stalled turn."""
    return next_stall_action(
        observation=_observation(**observation),
        events=events or [],
        state=state,
        policy=policy,
        now=_NOW,
    )


def _event(event_type: str, *, after_seconds: int) -> dict[str, Any]:
    """Return a transcript event offset from the recovery stamp."""
    return {"type": event_type, "ts": (_NOW + timedelta(seconds=after_seconds)).isoformat()}


def test_a_turn_within_the_window_needs_nothing() -> None:
    assert _decide(StallState(), silent_for=60).action is StallAction.NONE


@pytest.mark.parametrize(
    ("label", "observation"),
    [
        ("a running tool emits nothing while it works", {"tool_in_flight": True}),
        ("a permission prompt is waiting on a person", {"waiting_permission": True}),
    ],
)
def test_silence_that_is_not_a_stall_needs_nothing(label: str, observation: dict[str, Any]) -> None:
    assert _decide(StallState(), **observation).action is StallAction.NONE


def test_the_first_recovery_is_a_plain_nudge() -> None:
    assert _decide(StallState()).action is StallAction.NUDGE


def test_a_nudge_that_produced_nothing_escalates_to_an_interrupt() -> None:
    decision = _decide(StallState(strikes=1, recovered_at=_NOW - timedelta(seconds=600)))

    assert decision.action is StallAction.INTERRUPT
    assert decision.state.interrupted_at == _NOW


def test_an_interrupt_waits_for_the_turn_to_end_before_prompting() -> None:
    decision = _decide(StallState(strikes=1, interrupted_at=_NOW - timedelta(seconds=5)))

    assert decision.action is StallAction.AWAIT_INTERRUPT


def test_an_ended_turn_gets_the_nudge_the_interrupt_cleared_the_way_for() -> None:
    # Cancelling twice in a row would leave the prompt the interrupt exists to
    # deliver unsent.
    decision = _decide(
        StallState(strikes=1, interrupted_at=_NOW - timedelta(seconds=5)), finished=True
    )

    assert decision.action is StallAction.NUDGE
    assert decision.state.interrupted_at is None


def test_an_interrupt_that_never_lands_gives_up_waiting_and_nudges() -> None:
    decision = _decide(StallState(strikes=1, interrupted_at=_NOW - timedelta(seconds=61)))

    assert decision.action is StallAction.NUDGE


def test_the_budget_runs_out_after_the_configured_strikes() -> None:
    assert _decide(StallState(strikes=3)).action is StallAction.GIVE_UP


def test_a_longer_window_tolerates_the_same_silence() -> None:
    patient = StallPolicy(silence_seconds=20 * 60)

    assert _decide(StallState(), silent_for=600, policy=patient).action is StallAction.NONE


def test_agent_output_after_a_nudge_starts_the_ladder_over() -> None:
    state = StallState(strikes=2, recovered_at=_NOW - timedelta(seconds=600))

    decision = _decide(state, events=[_event("message_complete", after_seconds=-300)])

    assert decision.action is StallAction.NUDGE
    assert decision.state.strikes == 0


@pytest.mark.parametrize("event_type", ["user_input", "user_stop", "status_change", "error"])
def test_what_the_caller_itself_writes_does_not_start_the_ladder_over(event_type: str) -> None:
    # The nudge and the interrupt land in the same transcript as the agent's
    # own events. Reading either as progress loops the ladder forever.
    state = StallState(strikes=2, recovered_at=_NOW - timedelta(seconds=600))

    decision = _decide(state, events=[_event(event_type, after_seconds=-300)])

    assert decision.state.strikes == 2


def test_a_spent_nudge_stamps_later_than_its_own_transcript_entry() -> None:
    sent_at = _NOW + timedelta(seconds=1)

    state = record_nudge(StallState(strikes=1, interrupted_at=_NOW), sent_at)

    assert state == StallState(strikes=2, recovered_at=sent_at, interrupted_at=None)


def test_a_pending_interrupt_is_judged_before_silence() -> None:
    # Cancelling writes its own transcript entry, so the turn stops looking
    # silent the moment the interrupt lands. Gating the follow-up nudge on
    # silence meant waiting another whole window for it.
    decision = _decide(
        StallState(strikes=1, interrupted_at=_NOW - timedelta(seconds=5)),
        silent_for=1,
    )

    assert decision.action is StallAction.AWAIT_INTERRUPT


def test_an_ended_turn_is_nudged_even_though_the_interrupt_broke_the_silence() -> None:
    decision = _decide(
        StallState(strikes=1, interrupted_at=_NOW - timedelta(seconds=5)),
        silent_for=1,
        finished=True,
    )

    assert decision.action is StallAction.NUDGE
