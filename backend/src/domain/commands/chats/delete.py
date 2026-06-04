"""Delete a chat end-to-end: stop runtime and remove stored data."""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING

from src.domain.chatstore import ChatStore

if TYPE_CHECKING:
    from src.domain.supervisor import AgentSupervisorService


@dataclass(frozen=True)
class DeleteChatRequest:
    chat_slug: str


class ChatNotFound(ValueError):
    """The chat slug does not resolve to a stored chat."""


async def execute(
    chatstore: ChatStore,
    supervisor: AgentSupervisorService,
    req: DeleteChatRequest,
) -> None:
    if chatstore.get_chat(req.chat_slug) is None:
        raise ChatNotFound(f"chat not found: {req.chat_slug}")

    # Chat supervisors are keyed by chat slug. Stop first so no provider
    # process or websocket subscription races transcript/file removal.
    await supervisor.stop_agent(req.chat_slug)
    chatstore.delete_chat(req.chat_slug)


__all__ = ["ChatNotFound", "DeleteChatRequest", "execute"]
