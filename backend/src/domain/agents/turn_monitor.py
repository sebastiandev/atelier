"""Provider-turn state derived from canonical transcript events."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any


@dataclass(frozen=True)
class TurnObservation:
    """Current provider-turn facts shared by runtime orchestrators."""

    terminal_error: str | None
    finished: bool
    started_at: datetime | None
    last_activity_at: datetime | None
    elapsed_seconds: float | None
    waiting_permission: bool


def observe_turn(
    events: list[dict[str, Any]],
    now: datetime,
) -> TurnObservation:
    """Derive provider-turn health from ordered transcript events.

    Preconditions: events are in transcript order and ``now`` is timezone-aware.
    Postconditions: elapsed time excludes intervals awaiting permission decisions.
    """
    timed = [
        (event, timestamp)
        for event in events
        if (timestamp := _event_datetime(event)) is not None
    ]
    prompts = [timestamp for event, timestamp in timed if event.get("type") == "user_input"]
    started_at = max(prompts or [timestamp for _, timestamp in timed], default=None)
    elapsed_seconds, waiting_permission = _active_seconds(timed, started_at, now)
    return TurnObservation(
        terminal_error=_terminal_error(events),
        finished=_finished(events),
        started_at=started_at,
        last_activity_at=max((timestamp for _, timestamp in timed), default=None),
        elapsed_seconds=elapsed_seconds,
        waiting_permission=waiting_permission,
    )


def unresolved_permission_events(
    events: list[dict[str, Any]],
) -> tuple[dict[str, Any], ...]:
    """Return unresolved permission requests in transcript order.

    Preconditions: events use canonical permission and tool identifiers.
    Postconditions: decided requests and requests whose tools completed are omitted.
    """
    decided = {
        request_id
        for event in events
        if event.get("type") == "permission_decision"
        and isinstance((request_id := event.get("request_id")), str)
    }
    completed_tools = {
        tool_id
        for event in events
        if event.get("type") == "tool_result"
        and isinstance((tool_id := event.get("tool_id")), str)
    }
    return tuple(
        event
        for event in events
        if event.get("type") == "permission_request"
        and isinstance((request_id := event.get("request_id")), str)
        and request_id not in decided
        and (
            not isinstance((tool_id := event.get("tool_id")), str)
            or tool_id not in completed_tools
        )
    )


def _active_seconds(
    timed: list[tuple[dict[str, Any], datetime]],
    started_at: datetime | None,
    now: datetime,
) -> tuple[float | None, bool]:
    if started_at is None:
        return None, False
    pending: set[str] = set()
    paused_at: datetime | None = None
    paused_seconds = 0.0
    for event, timestamp in timed:
        if timestamp < started_at:
            continue
        event_type = event.get("type")
        request_id = event.get("request_id")
        if event_type == "permission_request" and isinstance(request_id, str):
            if not pending:
                paused_at = timestamp
            pending.add(request_id)
        elif event_type == "permission_decision" and request_id in pending:
            pending.remove(request_id)
            if not pending and paused_at is not None:
                paused_seconds += max(0.0, (timestamp - paused_at).total_seconds())
                paused_at = None
    if pending and paused_at is not None:
        paused_seconds += max(0.0, (now - paused_at).total_seconds())
    return max(0.0, (now - started_at).total_seconds() - paused_seconds), bool(pending)


def _terminal_error(events: list[dict[str, Any]]) -> str | None:
    for event in reversed(events):
        event_type = event.get("type")
        if event_type in {"message_complete", "user_input"}:
            return None
        if event_type == "error":
            if event.get("recoverable") is True:
                # Side-band failures (a rejected artifact marker, a
                # tracker hiccup) leave the turn itself healthy. Keep
                # scanning rather than reporting the run as dead.
                continue
            message = event.get("message")
            return message if isinstance(message, str) and message.strip() else "Unknown error"
    return None


def _finished(events: list[dict[str, Any]]) -> bool:
    last_input = max(
        (index for index, event in enumerate(events) if event.get("type") == "user_input"),
        default=-1,
    )
    last_idle = max(
        (
            index
            for index, event in enumerate(events)
            if event.get("type") == "status_change" and event.get("status") == "idle"
        ),
        default=-1,
    )
    return last_idle > last_input


def _event_datetime(event: dict[str, Any]) -> datetime | None:
    raw = event.get("ts") or event.get("created_at")
    if not isinstance(raw, str) or not raw:
        return None
    try:
        parsed = datetime.fromisoformat(raw.replace("Z", "+00:00"))
    except ValueError:
        return None
    return parsed.replace(tzinfo=UTC) if parsed.tzinfo is None else parsed.astimezone(UTC)


__all__ = ["TurnObservation", "observe_turn", "unresolved_permission_events"]
