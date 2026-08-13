"""What to do about a provider turn that stopped producing output.

The decision is the same wherever an agent is driven unattended: nudge a turn
that merely stopped, interrupt one that is wedged so the nudge is read, and
give up once neither has worked. What differs is how long silence is allowed
to run and what the caller does with the verdict, so this returns an action
rather than performing one -- the loop writes stage rows and a run status, the
materializer just re-prompts its chat.
"""

from __future__ import annotations

from dataclasses import dataclass, replace
from datetime import UTC, datetime
from enum import StrEnum
from typing import Any

from src.domain.agents.turn_monitor import TurnObservation

# The agent doing work, as opposed to anything that merely happened around it.
# An allowlist, because the transcript also carries what the caller itself
# writes (a nudge, an interrupt) and what an interrupt provokes in reply
# (status changes, errors, a cancelled turn settling) -- read as progress, any
# of those reset the ladder that was escalating them.
AGENT_PROGRESS_EVENT_TYPES = frozenset(
    {
        "message_delta",
        "message_complete",
        "thinking_delta",
        "thinking_complete",
        "tool_call",
        "tool_call_update",
        "tool_result",
        "plan_update",
    }
)


class StallAction(StrEnum):
    """What the caller should do about the turn it is watching."""

    NONE = "none"
    NUDGE = "nudge"
    INTERRUPT = "interrupt"
    AWAIT_INTERRUPT = "await_interrupt"
    GIVE_UP = "give_up"


@dataclass(frozen=True)
class StallPolicy:
    """How patient to be, and how many recoveries to spend.

    ``silence_seconds`` is time without *any* transcript event. A turn waiting
    on a permission decision or running a tool is not silent in the sense that
    matters, which ``TurnObservation`` already distinguishes.
    """

    silence_seconds: float
    max_strikes: int = 3
    # The first recovery is a plain nudge, which is all a turn that merely
    # stopped needs. Later ones interrupt first: a wedged turn never reads its
    # queue, so a nudge alone would sit behind it.
    nudge_only_strikes: int = 1
    # An interrupt that never lands must not park the caller forever; after
    # this the nudge goes out regardless.
    interrupt_grace_seconds: float = 60.0


@dataclass(frozen=True)
class StallState:
    """What one stall episode has spent so far.

    Callers persist this however they already persist their own state: the
    loop keeps it on a stage row, the materializer keeps it in the task that
    is doing the polling.
    """

    strikes: int = 0
    recovered_at: datetime | None = None
    interrupted_at: datetime | None = None


@dataclass(frozen=True)
class StallDecision:
    """One verdict, with the state to persist alongside it."""

    action: StallAction
    state: StallState


def next_stall_action(
    *,
    observation: TurnObservation,
    events: list[dict[str, Any]],
    state: StallState,
    policy: StallPolicy,
    now: datetime,
) -> StallDecision:
    """Return what to do about this turn, and the state that goes with it.

    Preconditions: ``events`` are the watched agent's transcript entries in
    order, and ``now`` is timezone-aware.
    Postconditions: ``NUDGE`` means send the prompt and then call
    :func:`record_nudge` with the time the send returned; every other action
    carries a state the caller can persist as-is.
    """
    if agent_moved_since(state, events):
        # It worked and stopped again: a fresh stall, not a continuation, so
        # escalation starts from the top rather than counting a long healthy
        # stretch against the turn. Any pending interrupt belonged to the
        # stall it broke.
        state = StallState()
    if state.interrupted_at is not None:
        # Checked before silence, not after. Cancelling publishes its own
        # transcript entry, so the turn stops looking silent the moment the
        # interrupt lands -- gating this on silence meant waiting another full
        # window before sending the nudge the interrupt exists to deliver,
        # which is not "the same rung" in any useful sense.
        if not observation.finished and (
            (now - state.interrupted_at).total_seconds() < policy.interrupt_grace_seconds
        ):
            return StallDecision(StallAction.AWAIT_INTERRUPT, state)
        return StallDecision(StallAction.NUDGE, replace(state, interrupted_at=None))
    if not _is_silent(observation, policy, now):
        return StallDecision(StallAction.NONE, state)
    if state.strikes >= policy.max_strikes:
        return StallDecision(StallAction.GIVE_UP, state)
    if state.strikes >= policy.nudge_only_strikes:
        return StallDecision(StallAction.INTERRUPT, replace(state, interrupted_at=now))
    return StallDecision(StallAction.NUDGE, state)


def record_nudge(state: StallState, sent_at: datetime) -> StallState:
    """Return the state after a nudge went out.

    Preconditions: ``sent_at`` is taken once the send returned.
    Postconditions: the strike is spent and the stamp is later than the
    prompt's own transcript entry, so that entry cannot read as the agent
    moving.
    """
    return StallState(strikes=state.strikes + 1, recovered_at=sent_at, interrupted_at=None)


def agent_moved_since(state: StallState, events: list[dict[str, Any]]) -> bool:
    """Whether the agent itself produced work after the last recovery.

    Preconditions: ``events`` are that agent's transcript entries in order.
    Postconditions: true only for agent output newer than the recovery stamp.

    A stalled agent still emits things that are not work -- the caller's own
    prompt, the interrupt that answers it, the status change that follows.
    Only output means it came back.
    """
    if state.recovered_at is None:
        return False
    return any(
        event.get("type") in AGENT_PROGRESS_EVENT_TYPES
        and (timestamp := event_time(event)) is not None
        and timestamp > state.recovered_at
        for event in events
    )


def event_time(event: dict[str, Any]) -> datetime | None:
    """Return one transcript event's timestamp, when it carries a usable one."""
    return parse_stamp(event.get("ts") or event.get("created_at"))


def parse_stamp(raw: object) -> datetime | None:
    """Read a stored timestamp, normalising it to an aware value.

    Stored state may have been written by an older build without an offset,
    and comparing a naive value against an aware ``now`` raises inside the
    caller's polling loop.
    """
    if not isinstance(raw, str) or not raw:
        return None
    try:
        parsed = datetime.fromisoformat(raw.replace("Z", "+00:00"))
    except ValueError:
        return None
    return parsed.replace(tzinfo=UTC) if parsed.tzinfo is None else parsed.astimezone(UTC)


def _is_silent(observation: TurnObservation, policy: StallPolicy, now: datetime) -> bool:
    """Whether nothing has happened for long enough to count as stalled.

    A tool call that is still running emits nothing while it works -- a test
    suite or a sub-agent exploration can be silent for far longer than the
    window. Treating that as a stall restarts work that was never stuck.
    """
    if observation.waiting_permission or observation.tool_in_flight:
        return False
    if observation.last_activity_at is None:
        return False
    return (now - observation.last_activity_at).total_seconds() >= policy.silence_seconds


__all__ = [
    "AGENT_PROGRESS_EVENT_TYPES",
    "StallAction",
    "StallDecision",
    "StallPolicy",
    "StallState",
    "agent_moved_since",
    "event_time",
    "next_stall_action",
    "parse_stamp",
    "record_nudge",
]
