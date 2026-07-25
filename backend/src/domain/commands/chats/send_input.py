"""Send visible user input to a chat runtime."""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING

from src.domain.chats import runtime
from src.domain.chatstore import ChatStore
from src.domain.planning.ports import PlanningFiles
from src.domain.workstore.ports import WorkStore

if TYPE_CHECKING:
    from src.domain.supervisor import AgentSupervisorService

ChatNotFound = runtime.ChatNotFound


class WorkNotActive(ValueError):
    """The chat's linked Work is archived and cannot accept input."""


@dataclass(frozen=True)
class SendChatInputRequest:
    """Visible user text for one chat turn."""

    chat_slug: str
    text: str


async def execute(
    chatstore: ChatStore,
    supervisor: AgentSupervisorService,
    planningfiles: PlanningFiles,
    workstore: WorkStore,
    req: SendChatInputRequest,
) -> None:
    """Send user input to a chat runtime.

    Preconditions: ``req.chat_slug`` resolves to a registered/open chat
    runtime.
    Postconditions: the transcript stores only ``req.text`` while the provider
    may receive backend-owned hidden context before that text.
    """
    record = chatstore.get_chat(req.chat_slug)
    if record is None:
        raise ChatNotFound(f"chat not found: {req.chat_slug}")
    linked_work_slug = record.chat.promoted_to_work_slug
    if linked_work_slug is None and record.chat.grounding_kind == "work":
        linked_work_slug = record.chat.grounding_ref
    linked_work = (
        workstore.get_work(linked_work_slug) if linked_work_slug is not None else None
    )
    if linked_work is not None and linked_work.work.status != "active":
        raise WorkNotActive(
            f"work {linked_work_slug} is {linked_work.work.status}; reopen it to continue"
        )
    provider_text = runtime.provider_input_for_chat(
        record.chat,
        planningfiles,
        req.text,
    )
    await supervisor.send_input(
        req.chat_slug,
        provider_text,
        transcript_text=req.text,
    )


__all__ = [
    "ChatNotFound",
    "SendChatInputRequest",
    "WorkNotActive",
    "execute",
]
