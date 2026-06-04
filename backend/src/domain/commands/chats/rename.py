"""Rename a chat title in SQL and chat.json."""

from __future__ import annotations

from dataclasses import dataclass

from src.domain.chatstore import ChatRecord, ChatStore


@dataclass(frozen=True)
class RenameChatRequest:
    chat_slug: str
    title: str


class ChatNotFound(ValueError):
    """The chat slug does not resolve to a stored chat."""


def execute(chatstore: ChatStore, req: RenameChatRequest) -> ChatRecord:
    try:
        return chatstore.rename_chat(req.chat_slug, req.title)
    except ValueError as exc:
        message = str(exc)
        if "not found" in message:
            raise ChatNotFound(message) from exc
        raise


__all__ = ["ChatNotFound", "RenameChatRequest", "execute"]
