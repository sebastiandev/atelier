"""Delete a Work permanently.

Completion preserves the audit trail; this command is the destructive
counterpart. It stops live agents/chats, removes per-agent worktrees, deletes
work-associated chats, and finally removes the WorkStore-owned work data.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING

from src.domain.chatstore import ChatStore
from src.domain.workstore.ports import WorkStore
from src.domain.worktrees import WorktreeManager

if TYPE_CHECKING:
    from src.domain.supervisor import AgentSupervisorService


@dataclass(frozen=True)
class DeleteWorkRequest:
    work_slug: str


@dataclass(frozen=True)
class DeleteWorkResult:
    work_slug: str
    agent_count: int
    chat_count: int


class WorkNotFound(ValueError):
    """The work slug doesn't resolve to a stored work."""


async def execute(
    workstore: WorkStore,
    chatstore: ChatStore,
    supervisor: AgentSupervisorService,
    chat_supervisor: AgentSupervisorService,
    worktree_manager: WorktreeManager,
    req: DeleteWorkRequest,
) -> DeleteWorkResult:
    record = workstore.get_work(req.work_slug)
    if record is None:
        raise WorkNotFound(f"work not found: {req.work_slug}")

    agents = workstore.list_agents_for_work(req.work_slug)
    agent_slugs = [a.slug for a in agents if a.slug is not None]

    chats = [
        r.chat
        for r in chatstore.list_chats()
        if (
            r.chat.grounding_kind == "work"
            and r.chat.grounding_ref == req.work_slug
        )
        or r.chat.promoted_to_work_slug == req.work_slug
    ]
    chat_slugs = [c.slug for c in chats if c.slug is not None]

    for slug in agent_slugs:
        await supervisor.stop_agent(slug)

    for slug in chat_slugs:
        await chat_supervisor.stop_agent(slug)

    for slug in agent_slugs:
        worktree_manager.remove(req.work_slug, slug)

    for slug in chat_slugs:
        chatstore.delete_chat(slug)

    workstore.delete_work(req.work_slug)

    return DeleteWorkResult(
        work_slug=req.work_slug,
        agent_count=len(agent_slugs),
        chat_count=len(chat_slugs),
    )


__all__ = [
    "DeleteWorkRequest",
    "DeleteWorkResult",
    "WorkNotFound",
    "execute",
]
