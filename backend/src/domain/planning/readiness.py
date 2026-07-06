"""Planning chat readiness detection and serialization."""

from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Any

from src.domain.chatstore.dtos import ChatRecord
from src.domain.models import Chat, ChatMessage

PLANNING_READINESS_OPTION = "planning_readiness"
PLANNING_READY_MARKER = "atelier_planning_ready"


@dataclass(frozen=True)
class PlanningChatReadiness:
    """Stored readiness state for a Planning discovery chat."""

    ready: bool
    summary: str


def is_planning_chat(chat: Chat) -> bool:
    """Return whether the chat is the reserved Planning chat for a Work.

    Preconditions: ``chat`` is a stored chat entity.
    Postconditions: no state is mutated.
    """
    return (
        chat.title.strip().casefold() == "planning"
        and chat.grounding_kind == "work"
        and bool(chat.grounding_ref)
    )


def scan_text_for_planning_readiness(text: str) -> PlanningChatReadiness | None:
    """Extract a structured Planning readiness marker from assistant text.

    Preconditions: ``text`` is a provider response body.
    Postconditions: returns readiness only for an exact single-line marker.
    """
    for line in text.splitlines():
        marker = _read_marker_line(line)
        if marker is not None:
            return marker
    return None


def planning_readiness_from_record(
    record: ChatRecord,
) -> PlanningChatReadiness | None:
    """Read Planning readiness from chat metadata or transcript fallback.

    Preconditions: ``record`` is a stored chat plus transcript.
    Postconditions: no state is mutated.
    """
    if not is_planning_chat(record.chat):
        return None
    from_options = planning_readiness_from_options(record.chat.options)
    if from_options is not None:
        return from_options
    return _readiness_from_transcript(record.transcript)


def planning_readiness_from_options(
    options: dict[str, Any] | None,
) -> PlanningChatReadiness | None:
    """Decode persisted Planning readiness from chat options.

    Preconditions: ``options`` is the chat metadata dict or ``None``.
    Postconditions: malformed values are ignored.
    """
    raw = (options or {}).get(PLANNING_READINESS_OPTION)
    if not isinstance(raw, dict) or raw.get("ready") is not True:
        return None
    summary = raw.get("summary")
    return PlanningChatReadiness(
        ready=True,
        summary=summary.strip() if isinstance(summary, str) else "",
    )


def planning_readiness_payload(
    readiness: PlanningChatReadiness,
) -> dict[str, Any]:
    """Serialize Planning readiness for chat options and API payloads.

    Preconditions: ``readiness`` was accepted by the readiness scanner.
    Postconditions: no state is mutated.
    """
    return {"ready": readiness.ready, "summary": readiness.summary}


def _readiness_from_transcript(
    transcript: list[ChatMessage],
) -> PlanningChatReadiness | None:
    for message in reversed(transcript):
        if message.role != "assistant":
            continue
        marker = scan_text_for_planning_readiness(message.body)
        if marker is not None:
            return marker
    return None


def _read_marker_line(line: str) -> PlanningChatReadiness | None:
    text = line.strip()
    if not (text.startswith("{") and text.endswith("}")):
        return None
    try:
        payload = json.loads(text)
    except json.JSONDecodeError:
        return None
    marker = payload.get(PLANNING_READY_MARKER)
    if not isinstance(marker, dict) or marker.get("ready") is not True:
        return None
    summary = marker.get("summary")
    return PlanningChatReadiness(
        ready=True,
        summary=summary.strip() if isinstance(summary, str) else "",
    )


__all__ = [
    "PLANNING_READINESS_OPTION",
    "PLANNING_READY_MARKER",
    "PlanningChatReadiness",
    "is_planning_chat",
    "planning_readiness_from_options",
    "planning_readiness_from_record",
    "planning_readiness_payload",
    "scan_text_for_planning_readiness",
]
