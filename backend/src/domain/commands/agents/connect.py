"""Establish a streaming connection to an agent.

The single inward path used by the WS endpoint. Yields a
``Subscription`` that emits every transcript event with ``seq > cursor``
exactly once, in order: disk-replay first, then live events.

Flow:
  1. If the supervisor already tracks the agent, subscribe directly.
  2. Otherwise, ``resume.execute`` rebuilds the adapter, runs the
     detach catch-up merge (if needed), and registers the agent.
     Truly unknown slugs surface as ``AgentNotFound``.
  3. ``supervisor.subscribe(slug, cursor)`` yields the Subscription;
     this command yields the same value to the caller.

The WS handler shrinks to::

    async with connect.execute(deps, request) as sub:
        async for event in sub.stream():
            await websocket.send_json(event)

with a parallel task watching ``sub.kicked`` and processing inbound
input frames.
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from dataclasses import dataclass
from typing import TYPE_CHECKING

from src.domain.commands.agents import resume
from src.domain.sharedfolders.ports import SharedFolderStore, ShareProvisioner
from src.domain.workstore.ports import WorkStore
from src.domain.worktrees import WorktreeManager
from src.settings import Settings

if TYPE_CHECKING:
    from src.domain.supervisor import AgentSubscription, AgentSupervisorService


@dataclass(frozen=True)
class ConnectRequest:
    agent_slug: str
    cursor: int = 0
    replay_limit: int | None = None


class AgentNotFound(ValueError):
    """The agent slug doesn't resolve to a stored agent."""


@asynccontextmanager
async def execute(
    workstore: WorkStore,
    supervisor: AgentSupervisorService,
    worktree_manager: WorktreeManager,
    sharestore: SharedFolderStore,
    share_provisioner: ShareProvisioner,
    settings: Settings,
    req: ConnectRequest,
) -> AsyncIterator[AgentSubscription]:
    if not supervisor.is_registered(req.agent_slug):
        # Supervisor has no live state — backend restart, the agent was
        # closed-to-rail, or it was detached to CLI. Resume will resolve
        # the work_slug from the workstore, register the agent, and (if
        # detached) merge the SDK-side events first.
        history_work_slug = workstore.get_work_slug_for_agent(req.agent_slug)
        if history_work_slug is None:
            raise AgentNotFound(f"agent not found: {req.agent_slug}")
        try:
            await resume.execute(
                workstore,
                supervisor,
                worktree_manager,
                sharestore,
                share_provisioner,
                settings,
                resume.ResumeAgentRequest(
                    work_slug=history_work_slug, agent_slug=req.agent_slug
                ),
            )
        except resume.AgentNotFound as exc:
            raise AgentNotFound(str(exc)) from exc
    elif supervisor.is_lazy_registered(req.agent_slug):
        # A view-only reattach registers lazily. If the user keeps typing
        # in the external CLI after that, later opens must still import
        # provider transcript entries before computing the WS replay window.
        history_work_slug = workstore.get_work_slug_for_agent(req.agent_slug)
        if history_work_slug is None:
            raise AgentNotFound(f"agent not found: {req.agent_slug}")
        try:
            synced = await resume.catch_up_cli_events(
                workstore,
                worktree_manager,
                work_slug=history_work_slug,
                agent_slug=req.agent_slug,
            )
        except resume.AgentNotFound as exc:
            raise AgentNotFound(str(exc)) from exc
        if synced:
            await supervisor.refresh_seq_from_disk(req.agent_slug)

    async with supervisor.subscribe(
        req.agent_slug, cursor=req.cursor, replay_limit=req.replay_limit
    ) as sub:
        yield sub


__all__ = [
    "AgentNotFound",
    "ConnectRequest",
    "execute",
]
