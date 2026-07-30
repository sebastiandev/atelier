"""Restart a chat's provider runtime without resending input."""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING

from src.domain.chatstore import ChatStore

if TYPE_CHECKING:
    from src.domain.supervisor import AgentSupervisorService


@dataclass(frozen=True)
class ReconnectChatRequest:
    """Identify the chat runtime to restart."""

    chat_slug: str


class ChatNotFound(ValueError):
    """The chat slug does not resolve to a stored chat."""


async def execute(
    chatstore: ChatStore,
    supervisor: AgentSupervisorService,
    req: ReconnectChatRequest,
) -> None:
    """Stop the live runtime so its WebSocket reconnect rebuilds it."""
    if chatstore.get_chat(req.chat_slug) is None:
        raise ChatNotFound(f"chat not found: {req.chat_slug}")
    await supervisor.stop_agent(req.chat_slug)


__all__ = ["ChatNotFound", "ReconnectChatRequest", "execute"]
