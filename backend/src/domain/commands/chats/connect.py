"""Establish a streaming connection to a chat."""

from __future__ import annotations

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from dataclasses import dataclass

from src.domain.chats import runtime
from src.domain.chatstore import ChatStore
from src.domain.planning.ports import PlanningFiles
from src.domain.projectstore.ports import ProjectStore
from src.domain.supervisor import AgentSubscription, AgentSupervisorService
from src.domain.workstore.ports import WorkStore
from src.settings import Settings

ChatNotFound = runtime.ChatNotFound
build_chat_runtime_config = runtime.build_chat_runtime_config


@dataclass(frozen=True)
class ConnectChatRequest:
    """Inputs for subscribing to a chat stream."""

    chat_slug: str
    cursor: int = 0
    read_only: bool = False
    replay_limit: int | None = None


@asynccontextmanager
async def execute(
    chatstore: ChatStore,
    supervisor: AgentSupervisorService,
    workstore: WorkStore,
    projectstore: ProjectStore,
    planningfiles: PlanningFiles,
    settings: Settings,
    req: ConnectChatRequest,
) -> AsyncIterator[AgentSubscription]:
    """Connect a chat stream to the shared supervisor runtime.

    Preconditions: ``req.chat_slug`` identifies a stored chat.
    Postconditions: yields a subscription replaying transcript events after
    ``req.cursor`` and publishing live events.
    """
    async with runtime.connect_chat(
        chatstore,
        supervisor,
        workstore,
        projectstore,
        planningfiles,
        settings,
        runtime.ConnectChatRuntimeRequest(
            chat_slug=req.chat_slug,
            cursor=req.cursor,
            read_only=req.read_only,
            replay_limit=req.replay_limit,
        ),
    ) as sub:
        yield sub


__all__ = [
    "ChatNotFound",
    "ConnectChatRequest",
    "build_chat_runtime_config",
    "execute",
]
