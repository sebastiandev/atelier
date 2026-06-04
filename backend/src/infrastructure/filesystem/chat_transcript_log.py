"""TranscriptLog adapter for runtime-backed exploratory chats."""

from __future__ import annotations

from collections.abc import Callable, Iterator
from typing import Any

from src.domain.chatstore.ports import ChatFiles

TouchChatFn = Callable[[str], None]

_TOUCH_EVENT_TYPES = {"user_input", "message_complete", "error"}


class FsChatTranscriptLog:
    """Adapt chat transcript files to the supervisor's TranscriptLog port.

    The supervisor is keyed as ``(work_slug, agent_slug)`` because it was
    built for agents first. For chats, the second argument is the chat slug
    and the first is ignored.
    """

    def __init__(self, files: ChatFiles, touch_chat: TouchChatFn | None = None) -> None:
        self._files = files
        self._touch_chat = touch_chat

    def append(
        self, _work_slug: str, agent_slug: str, event: dict[str, Any]
    ) -> None:
        self._files.ensure_chat_dir(agent_slug)
        self._files.append_transcript_event(agent_slug, event)
        if self._touch_chat and event.get("type") in _TOUCH_EVENT_TYPES:
            self._touch_chat(agent_slug)

    def read_from_cursor(
        self, _work_slug: str, agent_slug: str, cursor: int
    ) -> Iterator[dict[str, Any]]:
        for event in self._files.read_transcript(agent_slug):
            seq = event.get("seq")
            if not isinstance(seq, int) or seq <= cursor:
                continue
            converted = _event_for_stream(event)
            if converted is not None:
                yield converted

    def last_seq(self, _work_slug: str, agent_slug: str) -> int:
        return self._files.last_seq(agent_slug)


def _event_for_stream(event: dict[str, Any]) -> dict[str, Any] | None:
    event_type = event.get("type")
    if event_type == "chat_initial_prompt_delivered":
        return None
    if isinstance(event_type, str):
        return event

    role = event.get("role")
    body = event.get("body")
    created_at = event.get("created_at")
    if role == "user" and isinstance(body, str) and isinstance(created_at, str):
        return {
            "seq": event["seq"],
            "type": "user_input",
            "ts": created_at,
            "text": body,
        }
    if role == "assistant" and isinstance(body, str) and isinstance(created_at, str):
        return {
            "seq": event["seq"],
            "type": "message_complete",
            "ts": created_at,
            "text": body,
        }
    return None


__all__ = ["FsChatTranscriptLog"]
