"""Read the current Planning materialization status for a Work."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any, Literal

from src.domain.chatstore.dtos import ChatRecord
from src.domain.chatstore.ports import ChatStore
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
_STALE_AFTER = timedelta(minutes=6)


class WorkNotFound(ValueError):
    """The work_slug doesn't exist."""


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


def execute(
    workstore: WorkStore,
    chatstore: ChatStore,
    files: PlanningFiles,
    work_slug: str,
) -> MaterializationStatus:
    """Return current materialization state for a Work.

    Preconditions: ``work_slug`` resolves to an existing Work.
    Postconditions: no state is mutated; status is derived from plan files and
    the internal ``Planning materializer`` chat transcript.
    """
    if workstore.get_work(work_slug) is None:
        raise WorkNotFound(f"work not found: {work_slug}")
    if PlanningService(files).get_plan(work_slug) is not None:
        return MaterializationStatus(
            state="complete",
            message="Source plan is indexed.",
        )
    record = _materializer_record(chatstore, work_slug)
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
    pending = _pending_permission(events)
    if pending is not None:
        return MaterializationStatus(
            state="waiting_permission",
            chat_slug=record.chat.slug,
            updated_at=_event_time(pending) or record.chat.updated_at.isoformat(),
            last_seq=_event_seq(events[-1]),
            last_event_type=_event_type(pending),
            last_event_summary=_event_summary(pending),
            message=pending.get("title") or "Waiting on a materializer permission.",
            tool_name=_str(pending.get("tool_name")),
        )
    last = events[-1]
    if _is_stale(last):
        return MaterializationStatus(
            state="stalled",
            chat_slug=record.chat.slug,
            updated_at=_event_time(last) or record.chat.updated_at.isoformat(),
            last_seq=_event_seq(last),
            last_event_type=_event_type(last),
            last_event_summary=_event_summary(last),
            message="No materializer activity after the turn timeout.",
            tool_name=_str(last.get("name") or last.get("tool_name")),
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
    )


def _materializer_record(
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


def _pending_permission(events: list[dict[str, Any]]) -> dict[str, Any] | None:
    decided: set[str] = set()
    completed_tools: set[str] = set()
    requests: list[dict[str, Any]] = []
    for event in events:
        event_type = event.get("type")
        request_id = event.get("request_id")
        tool_id = event.get("tool_id")
        if event_type == "permission_decision" and isinstance(request_id, str):
            decided.add(request_id)
        elif event_type == "tool_result" and isinstance(tool_id, str):
            completed_tools.add(tool_id)
        elif event_type == "permission_request":
            requests.append(event)
    for event in reversed(requests):
        request_id = event.get("request_id")
        tool_id = event.get("tool_id")
        if isinstance(request_id, str) and request_id in decided:
            continue
        if isinstance(tool_id, str) and tool_id in completed_tools:
            continue
        return event
    return None


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


def _is_stale(event: dict[str, Any]) -> bool:
    stamp = _event_datetime(event)
    if stamp is None:
        return False
    return datetime.now(UTC) - stamp > _STALE_AFTER


def _event_time(event: dict[str, Any]) -> str | None:
    stamp = event.get("ts") or event.get("created_at")
    return stamp if isinstance(stamp, str) else None


def _event_datetime(event: dict[str, Any]) -> datetime | None:
    raw = _event_time(event)
    if raw is None:
        return None
    try:
        parsed = datetime.fromisoformat(raw)
    except ValueError:
        return None
    if parsed.tzinfo is None:
        return parsed.replace(tzinfo=UTC)
    return parsed.astimezone(UTC)


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
