"""ChatStoreService — composes SQL chat rows with filesystem transcripts."""

from __future__ import annotations

from collections.abc import Callable, Iterator
from datetime import UTC, datetime
from threading import RLock
from typing import Any

from src.domain.chatstore._serde import (
    deserialize_message,
    serialize_chat,
    serialize_message,
)
from src.domain.chatstore.dtos import (
    AppendChatMessageRequest,
    ChatRecord,
    CreateChatRequest,
)
from src.domain.chatstore.ports import ChatFiles, ChatRepository
from src.domain.models import Chat, ChatMessage

Clock = Callable[[], datetime]


def _utc_now() -> datetime:
    return datetime.now(UTC)


class ChatStoreService:
    def __init__(
        self,
        repo: ChatRepository,
        files: ChatFiles,
        *,
        lock: RLock | None = None,
        clock: Clock = _utc_now,
    ) -> None:
        self._repo = repo
        self._files = files
        self._lock = lock if lock is not None else RLock()
        self._clock = clock

    def create_chat(self, req: CreateChatRequest) -> ChatRecord:
        first = req.first_message.strip()
        if not first:
            raise ValueError("first_message must be non-empty")
        now = self._clock()
        with self._lock:
            chat = Chat(
                title=(req.title or _derive_title(first)).strip(),
                provider=req.provider,
                model=req.model,
                grounding_kind=req.grounding.kind if req.grounding else None,
                grounding_ref=req.grounding.ref if req.grounding else None,
                working_directory=_clean_optional_path(req.working_directory),
                options=req.options or None,
                created_at=now,
                updated_at=now,
            )
            chat = self._repo.add_chat(chat)
            slug = _require_slug(chat)
            self._files.ensure_chat_dir(slug)
            self._files.write_chat_json(slug, serialize_chat(chat))
            first_message = ChatMessage(role="user", body=first, created_at=now)
            self._append_transcript_message(slug, first_message)
        return ChatRecord(chat=chat, transcript=[first_message])

    def get_chat(self, chat_slug: str) -> ChatRecord | None:
        with self._lock:
            chat = self._repo.get_chat_by_slug(chat_slug)
            if chat is None:
                return None
            return ChatRecord(chat=chat, transcript=self._read_transcript(chat_slug))

    def list_chats(self) -> list[ChatRecord]:
        with self._lock:
            chats = sorted(
                self._repo.list_chats(), key=lambda c: c.updated_at, reverse=True
            )
            return [
                ChatRecord(
                    chat=chat,
                    transcript=self._read_transcript(_require_slug(chat)),
                )
                for chat in chats
            ]

    def rename_chat(self, chat_slug: str, title: str) -> ChatRecord:
        next_title = title.strip()
        if not next_title:
            raise ValueError("title must be non-empty")
        with self._lock:
            chat = self._repo.get_chat_by_slug(chat_slug)
            if chat is None:
                raise ValueError(f"chat not found: {chat_slug}")
            chat.title = next_title
            chat.updated_at = self._clock()
            self._repo.update_chat(chat)
            self._files.write_chat_json(chat_slug, serialize_chat(chat))
            return ChatRecord(chat=chat, transcript=self._read_transcript(chat_slug))

    def set_chat_option(self, chat_slug: str, key: str, value: Any) -> None:
        with self._lock:
            chat = self._repo.get_chat_by_slug(chat_slug)
            if chat is None:
                raise ValueError(f"chat not found: {chat_slug}")
            options = dict(chat.options or {})
            options[key] = value
            chat.options = options
            chat.updated_at = self._clock()
            self._repo.update_chat(chat)
            self._files.write_chat_json(chat_slug, serialize_chat(chat))

    def delete_chat(self, chat_slug: str) -> None:
        with self._lock:
            if self._repo.get_chat_by_slug(chat_slug) is None:
                return
            self._files.remove_chat_dir(chat_slug)
            self._repo.delete_chat(chat_slug)

    def append_message(self, req: AppendChatMessageRequest) -> ChatRecord:
        body = req.body.strip()
        if not body:
            raise ValueError("message body must be non-empty")
        now = self._clock()
        with self._lock:
            chat = self._repo.get_chat_by_slug(req.chat_slug)
            if chat is None:
                raise ValueError(f"chat not found: {req.chat_slug}")
            message = ChatMessage(role=req.role, body=body, created_at=now)
            self._append_transcript_message(req.chat_slug, message)
            chat.updated_at = now
            self._repo.update_chat(chat)
            self._files.write_chat_json(req.chat_slug, serialize_chat(chat))
            transcript = self._read_transcript(req.chat_slug)
        return ChatRecord(chat=chat, transcript=transcript)

    def mark_promoted(self, chat_slug: str, work_slug: str) -> ChatRecord:
        with self._lock:
            chat = self._repo.get_chat_by_slug(chat_slug)
            if chat is None:
                raise ValueError(f"chat not found: {chat_slug}")
            chat.promoted_to_work_slug = work_slug
            chat.updated_at = self._clock()
            self._repo.update_chat(chat)
            self._files.write_chat_json(chat_slug, serialize_chat(chat))
            return ChatRecord(chat=chat, transcript=self._read_transcript(chat_slug))

    def set_chat_session_id(self, chat_slug: str, session_id: str) -> None:
        with self._lock:
            chat = self._repo.get_chat_by_slug(chat_slug)
            if chat is None:
                raise ValueError(f"chat not found: {chat_slug}")
            chat.session_id = session_id
            chat.updated_at = self._clock()
            self._repo.update_chat(chat)
            self._files.write_chat_json(chat_slug, serialize_chat(chat))

    def touch_chat(self, chat_slug: str) -> None:
        with self._lock:
            chat = self._repo.get_chat_by_slug(chat_slug)
            if chat is None:
                return
            chat.updated_at = self._clock()
            self._repo.update_chat(chat)
            self._files.write_chat_json(chat_slug, serialize_chat(chat))

    def claim_initial_prompt(self, chat_slug: str) -> str | None:
        """Atomically claim delivery of the first persisted user message.

        New-chat creation writes the modal's first message as durable
        transcript row before any websocket exists. The runtime should send
        that text to the provider exactly once when the stream is first
        opened. A marker row is enough to make reconnects/idempotent starts
        safe without adding another DB column or rewriting legacy rows.
        """

        with self._lock:
            chat = self._repo.get_chat_by_slug(chat_slug)
            if chat is None:
                raise ValueError(f"chat not found: {chat_slug}")
            events = list(self._files.read_transcript(chat_slug))
            if _initial_prompt_already_delivered(events):
                return None
            first = _first_user_text(events)
            if first is None:
                return None
            seq = self._files.last_seq(chat_slug) + 1
            self._files.append_transcript_event(
                chat_slug,
                {
                    "seq": seq,
                    "type": "chat_initial_prompt_delivered",
                    "ts": self._clock().isoformat(),
                },
            )
            return first

    def append_transcript_event_with_seq(
        self, chat_slug: str, payload: dict[str, Any]
    ) -> int:
        """Stamp the next chat transcript seq and append an event.

        Runtime turns use the supervisor hot path. This method is for
        out-of-band writes such as compaction markers after the supervisor
        has been stopped for the chat.
        """

        with self._lock:
            chat = self._repo.get_chat_by_slug(chat_slug)
            if chat is None:
                raise ValueError(f"chat not found: {chat_slug}")
            seq = self._files.last_seq(chat_slug) + 1
            self._files.append_transcript_event(chat_slug, {"seq": seq, **payload})
            return seq

    def read_transcript_from_cursor(
        self, chat_slug: str, cursor: int
    ) -> Iterator[dict[str, Any]]:
        with self._lock:
            if self._repo.get_chat_by_slug(chat_slug) is None:
                raise ValueError(f"chat not found: {chat_slug}")
            events = [
                event
                for event in self._files.read_transcript(chat_slug)
                if _event_seq(event) > cursor
            ]
        return iter(events)

    def write_chat_compaction_doc(
        self, chat_slug: str, filename: str, content: str
    ) -> str:
        with self._lock:
            if self._repo.get_chat_by_slug(chat_slug) is None:
                raise ValueError(f"chat not found: {chat_slug}")
            return self._files.write_chat_compaction_doc(
                chat_slug, filename, content
            )

    def read_chat_compaction_doc(
        self, chat_slug: str, filename: str
    ) -> tuple[str, str] | None:
        with self._lock:
            if self._repo.get_chat_by_slug(chat_slug) is None:
                raise ValueError(f"chat not found: {chat_slug}")
            return self._files.read_chat_compaction_doc(chat_slug, filename)

    def _append_transcript_message(
        self, chat_slug: str, message: ChatMessage
    ) -> None:
        seq = self._files.last_seq(chat_slug) + 1
        self._files.append_transcript_event(
            chat_slug,
            {"seq": seq, **serialize_message(message)},
        )

    def _read_transcript(self, chat_slug: str) -> list[ChatMessage]:
        out: list[ChatMessage] = []
        for event in self._files.read_transcript(chat_slug):
            message = deserialize_message(event)
            if message is not None:
                out.append(message)
        return out


def _derive_title(first_message: str) -> str:
    compact = " ".join(first_message.split())
    if len(compact) <= 72:
        return compact
    return compact[:69].rstrip() + "..."


def _clean_optional_path(path: str | None) -> str | None:
    if path is None:
        return None
    cleaned = path.strip()
    return cleaned or None


def _require_slug(chat: Chat) -> str:
    if chat.slug is None:
        raise RuntimeError("repository returned Chat without slug")
    return chat.slug


def _initial_prompt_already_delivered(events: list[dict[str, Any]]) -> bool:
    for event in events:
        if event.get("type") == "chat_initial_prompt_delivered":
            return True
        if event.get("type") in {"session_established", "message_complete"}:
            return True
        if event.get("role") == "assistant":
            return True
    return False


def _first_user_text(events: list[dict[str, Any]]) -> str | None:
    for event in events:
        if event.get("role") == "user" and isinstance(event.get("body"), str):
            return str(event["body"])
        if event.get("type") == "user_input" and isinstance(event.get("text"), str):
            return str(event["text"])
    return None


def _event_seq(event: dict[str, Any]) -> int:
    seq = event.get("seq")
    return seq if isinstance(seq, int) else 0


__all__ = ["ChatStoreService"]
