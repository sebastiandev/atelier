"""Read the current Planning materialization status for a Work."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any, Literal

from src.domain.agents.turn_monitor import observe_turn, unresolved_permission_events
from src.domain.chatstore.dtos import ChatRecord
from src.domain.chatstore.ports import ChatStore
from src.domain.loop.ports import LoopRunRepository
from src.domain.planning.ports import PlanningFiles
from src.domain.planning.service import PlanningService
from src.domain.workstore.ports import WorkStore

MaterializationState = Literal[
    "idle",
    "running",
    "waiting_permission",
    "stalled",
    "complete",
    "failed",
]
_STALE_AFTER = timedelta(minutes=1)


class WorkNotFound(ValueError):
    """The work_slug doesn't exist."""


@dataclass(frozen=True)
class MaterializationActivity:
    """One compact, user-readable materializer transcript event."""

    kind: str
    text: str
    ts: str | None = None


@dataclass(frozen=True)
class MaterializationPermission:
    """One unresolved provider tool permission."""

    request_id: str
    tool_name: str
    tool_input: dict[str, Any]
    title: str
    ts: str
    seq: int
    options: tuple[dict[str, str], ...] = ()


@dataclass(frozen=True)
class MaterializationStatus:
    """Read-side status for the internal materializer chat."""

    state: MaterializationState
    chat_slug: str | None = None
    updated_at: str | None = None
    last_seq: int | None = None
    last_event_type: str | None = None
    last_event_summary: str = ""
    message: str = ""
    tool_name: str | None = None
    recent_activity: tuple[MaterializationActivity, ...] = ()
    pending_permissions: tuple[MaterializationPermission, ...] = ()


def execute(
    workstore: WorkStore,
    chatstore: ChatStore,
    files: PlanningFiles,
    loop_runs: LoopRunRepository,
    work_slug: str,
) -> MaterializationStatus:
    """Return current materialization state for a Work.

    Preconditions: ``work_slug`` resolves to an existing Work.
    Postconditions: no state is mutated; status is derived from plan files and
    the internal ``Planning materializer`` chat transcript.
    """
    if workstore.get_work(work_slug) is None:
        raise WorkNotFound(f"work not found: {work_slug}")
    if PlanningService(files, loop_runs).get_plan(work_slug) is not None:
        return MaterializationStatus(
            state="complete",
            message="Source plan is indexed.",
        )
    record = materializer_record(chatstore, work_slug)
    if record is None or record.chat.slug is None:
        return MaterializationStatus(state="idle")

    events = list(chatstore.read_transcript_from_cursor(record.chat.slug, 0))
    if not events:
        return MaterializationStatus(
            state="running",
            chat_slug=record.chat.slug,
            updated_at=record.chat.updated_at.isoformat(),
            message="Materializer chat has started.",
        )
    recent_activity = _recent_activity(events)
    pending_permissions = unresolved_permissions(events)
    if pending_permissions:
        pending = pending_permissions[-1]
        return MaterializationStatus(
            state="waiting_permission",
            chat_slug=record.chat.slug,
            updated_at=pending.ts or record.chat.updated_at.isoformat(),
            last_seq=_event_seq(events[-1]),
            last_event_type="permission_request",
            last_event_summary=pending.title or pending.tool_name,
            message=pending.title or "Waiting on a materializer permission.",
            tool_name=pending.tool_name,
            recent_activity=recent_activity,
            pending_permissions=pending_permissions,
        )
    last = events[-1]
    observation = observe_turn(events, datetime.now(UTC))
    if observation.terminal_error is not None:
        return MaterializationStatus(
            state="failed",
            chat_slug=record.chat.slug,
            updated_at=_event_time(last) or record.chat.updated_at.isoformat(),
            last_seq=_event_seq(last),
            last_event_type="error",
            last_event_summary=_clip(observation.terminal_error),
            message=observation.terminal_error,
            recent_activity=recent_activity,
        )
    if observation.finished:
        return MaterializationStatus(
            state="stalled",
            chat_slug=record.chat.slug,
            updated_at=_event_time(last) or record.chat.updated_at.isoformat(),
            last_seq=_event_seq(last),
            last_event_type=_event_type(last),
            last_event_summary=_event_summary(last),
            message="Materializer is idle but no source-plan report exists.",
            tool_name=_str(last.get("name") or last.get("tool_name")),
            recent_activity=recent_activity,
        )
    if (
        observation.last_activity_at is not None
        and datetime.now(UTC) - observation.last_activity_at > _STALE_AFTER
    ):
        return MaterializationStatus(
            state="stalled",
            chat_slug=record.chat.slug,
            updated_at=_event_time(last) or record.chat.updated_at.isoformat(),
            last_seq=_event_seq(last),
            last_event_type=_event_type(last),
            last_event_summary=_event_summary(last),
            message="No recent materializer activity. It may still be working.",
            tool_name=_str(last.get("name") or last.get("tool_name")),
            recent_activity=recent_activity,
        )
    state, message = _state_from_last_event(last)
    return MaterializationStatus(
        state=state,
        chat_slug=record.chat.slug,
        updated_at=_event_time(last) or record.chat.updated_at.isoformat(),
        last_seq=_event_seq(last),
        last_event_type=_event_type(last),
        last_event_summary=_event_summary(last),
        message=message,
        tool_name=_str(last.get("name") or last.get("tool_name")),
        recent_activity=recent_activity,
    )


def materializer_record(
    chatstore: ChatStore,
    work_slug: str,
) -> ChatRecord | None:
    rows = [
        record
        for record in chatstore.list_chats()
        if record.chat.title.strip().casefold() == "planning materializer"
        and record.chat.grounding_kind == "work"
        and record.chat.grounding_ref == work_slug
    ]
    return rows[0] if rows else None


def unresolved_permissions(
    events: list[dict[str, Any]],
) -> tuple[MaterializationPermission, ...]:
    """Return unresolved materializer permissions in transcript order."""
    pending: list[MaterializationPermission] = []
    for event in unresolved_permission_events(events):
        request_id = event.get("request_id")
        if not isinstance(request_id, str):
            continue
        pending.append(
            MaterializationPermission(
                request_id=request_id,
                tool_name=_str(event.get("tool_name")) or "(unknown)",
                tool_input=(
                    dict(event["tool_input"])
                    if isinstance(event.get("tool_input"), dict)
                    else {}
                ),
                title=_str(event.get("title")),
                ts=_event_time(event) or "",
                seq=_event_seq(event) or 0,
                options=tuple(
                    dict(option)
                    for option in event.get("options", [])
                    if isinstance(option, dict)
                    and all(
                        isinstance(key, str) and isinstance(value, str)
                        for key, value in option.items()
                    )
                ),
            )
        )
    return tuple(pending)


def _recent_activity(
    events: list[dict[str, Any]],
) -> tuple[MaterializationActivity, ...]:
    tool_titles: dict[str, str] = {}
    rows: list[MaterializationActivity] = []
    for event in events:
        event_type = _event_type(event)
        tool_id = event.get("tool_id")
        if event_type == "tool_call":
            text = _str(event.get("title")) or _str(event.get("name"))
            if isinstance(tool_id, str):
                tool_titles[tool_id] = text
        elif event_type == "tool_result":
            title = tool_titles.get(tool_id, "") if isinstance(tool_id, str) else ""
            text = f"Completed: {title}" if title else "Tool completed"
        elif event_type in {"thinking_complete", "message_complete", "error"}:
            text = _str(event.get("text")) or _str(event.get("message"))
        elif event_type == "permission_request":
            text = (
                _str(event.get("title"))
                or _str(event.get("tool_name"))
                or "Permission requested"
            )
        elif event_type == "permission_decision":
            text = f"Permission {_str(event.get('decision')) or 'answered'}"
        elif event_type == "user_input":
            text = "Asked the materializer to continue"
        else:
            continue
        if text:
            rows.append(
                MaterializationActivity(
                    kind=event_type or "activity",
                    text=_clip(text, 180),
                    ts=_event_time(event),
                )
            )
    return tuple(rows[-5:])


def _state_from_last_event(event: dict[str, Any]) -> tuple[MaterializationState, str]:
    event_type = event.get("type")
    if event_type == "error":
        return "failed", _str(event.get("message")) or "Materializer failed."
    if event_type == "status_change" and event.get("status") == "idle":
        return "stalled", "Materializer is idle but no source-plan report exists."
    if event_type == "message_complete":
        return "running", _str(event.get("text")) or "Materializer wrote a message."
    if event_type == "tool_call":
        return "running", _str(event.get("title")) or "Materializer is using a tool."
    if event_type == "tool_result":
        return "running", "Materializer received tool output."
    if event_type == "permission_decision":
        return "running", "Permission answered; waiting for materializer."
    if event_type == "user_input":
        return "running", "Materializer was asked to continue."
    return "running", "Materializer is running."


def _event_time(event: dict[str, Any]) -> str | None:
    stamp = event.get("ts") or event.get("created_at")
    return stamp if isinstance(stamp, str) else None


def _event_seq(event: dict[str, Any]) -> int | None:
    seq = event.get("seq")
    return seq if isinstance(seq, int) else None


def _event_type(event: dict[str, Any]) -> str | None:
    event_type = event.get("type")
    return event_type if isinstance(event_type, str) else None


def _event_summary(event: dict[str, Any]) -> str:
    event_type = event.get("type")
    if event_type in {"message_delta", "thinking_delta", "message_complete"}:
        return _clip(_str(event.get("text")))
    if event_type == "status_change":
        return _str(event.get("status"))
    if event_type == "tool_call":
        return _clip(_str(event.get("title")) or _str(event.get("name")))
    if event_type == "tool_result":
        return "tool output"
    if event_type == "permission_request":
        return _clip(
            _str(event.get("title"))
            or _str(event.get("tool_name"))
            or "permission requested"
        )
    if event_type == "permission_decision":
        return _str(event.get("decision")) or "permission answered"
    if event_type == "user_input":
        return "continue prompt sent"
    if event_type == "session_established":
        return "session established"
    if event_type == "session_config_options":
        return "session options loaded"
    if event_type == "session_commands":
        return "session commands loaded"
    if event_type == "mode_change":
        return _str(event.get("mode_id"))
    if event_type == "error":
        return _clip(_str(event.get("message")))
    return ""


def _clip(value: str, limit: int = 180) -> str:
    if len(value) <= limit:
        return value
    return value[: limit - 1].rstrip() + "..."


def _str(value: object) -> str:
    return value if isinstance(value, str) else ""


__all__ = [
    "MaterializationState",
    "MaterializationStatus",
    "WorkNotFound",
    "execute",
]
