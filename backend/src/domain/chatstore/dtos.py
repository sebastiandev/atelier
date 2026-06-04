"""DTOs for the ChatStore boundary."""

from dataclasses import dataclass

from src.domain.models import (
    Chat,
    ChatGroundingKind,
    ChatMessage,
    ChatMessageRole,
    Provider,
)


@dataclass(frozen=True)
class ChatGrounding:
    kind: ChatGroundingKind
    ref: str


@dataclass(frozen=True)
class CreateChatRequest:
    provider: Provider
    model: str
    first_message: str
    title: str | None = None
    grounding: ChatGrounding | None = None
    working_directory: str | None = None


@dataclass(frozen=True)
class AppendChatMessageRequest:
    chat_slug: str
    role: ChatMessageRole
    body: str


@dataclass(frozen=True)
class ChatRecord:
    chat: Chat
    transcript: list[ChatMessage]


__all__ = [
    "AppendChatMessageRequest",
    "ChatGrounding",
    "ChatRecord",
    "CreateChatRequest",
]
