"""Runtime helpers for re-registering existing agents."""

from __future__ import annotations

import asyncio
from contextlib import suppress
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import TYPE_CHECKING

from src.domain.agents import (
    SPECS,
    AgentStartContext,
    CommonAgentConfig,
    detect_shared_envs,
    render_system_prompt,
)
from src.domain.agents.mounts import agent_writable_roots, mount_project_shares
from src.domain.models import Agent, AgentStatus
from src.domain.sharedfolders.ports import SharedFolderStore, ShareProvisioner
from src.domain.workstore.dtos import WorkRecord
from src.domain.workstore.ports import WorkStore
from src.domain.worktrees import WorktreeManager
from src.infrastructure.agents import build_adapter
from src.infrastructure.cli_transcript import merge_cli_transcript, sdk_cursor_at_detach
from src.settings import Settings

if TYPE_CHECKING:
    from src.domain.supervisor import AgentSupervisorService


@dataclass(frozen=True)
class ResumeAgentRequest:
    """Inputs for re-registering a stored agent runtime."""

    work_slug: str
    agent_slug: str


class AgentNotFound(ValueError):
    """The (work_slug, agent_slug) pair doesn't resolve to a stored agent."""


async def resume_agent(
    workstore: WorkStore,
    supervisor: AgentSupervisorService,
    worktree_manager: WorktreeManager,
    sharestore: SharedFolderStore,
    share_provisioner: ShareProvisioner,
    settings: Settings,
    req: ResumeAgentRequest,
) -> Agent:
    """Re-register an existing agent with the supervisor.

    Preconditions: ``req`` identifies a stored agent row.
    Postconditions: the supervisor has a lazy runtime for the agent and any
    detached CLI transcript events have been merged.
    """
    record: WorkRecord | None = workstore.get_work(req.work_slug)
    if record is None:
        raise AgentNotFound(f"work not found: {req.work_slug}")

    agent = next(
        (
            a
            for a in workstore.list_agents_for_work(req.work_slug)
            if a.slug == req.agent_slug
        ),
        None,
    )
    if agent is None:
        raise AgentNotFound(f"agent not found: {req.agent_slug}")

    if supervisor.is_registered(req.agent_slug):
        return agent

    workdir = worktree_manager.ensure(
        work_slug=req.work_slug,
        agent_slug=req.agent_slug,
        source=agent.folder,
    )

    mounted_shares = mount_project_shares(
        sharestore=sharestore,
        provisioner=share_provisioner,
        project_slug=record.work.project_slug,
        work_slug=req.work_slug,
        agent_slug=req.agent_slug,
    )

    common = CommonAgentConfig(
        workdir=workdir,
        writable_roots=agent_writable_roots(mounted_shares, worktree_manager, workdir),
        system_prompt=render_system_prompt(
            agent.persona,
            agent.role,
            workdir=workdir,
            shares=mounted_shares.summaries,
            is_detached_worktree=worktree_manager.is_detached(workdir),
            shared_envs=detect_shared_envs(workdir),
        ),
    )
    config = SPECS[agent.provider].build(common, agent.model, dict(agent.options or {}))
    adapter = build_adapter(config, settings)
    context = AgentStartContext(
        workdir=common.workdir,
        model=agent.model,
        system_prompt=common.system_prompt,
        session_id=agent.session_id,
    )

    if agent.status == AgentStatus.DETACHED:
        await asyncio.to_thread(
            _catch_up_detached_agent,
            workstore,
            req.work_slug,
            req.agent_slug,
            agent,
            workdir,
        )
    elif workstore.find_last_detach_cursor(req.work_slug, req.agent_slug) is not None:
        await asyncio.to_thread(
            _catch_up_detached_agent,
            workstore,
            req.work_slug,
            req.agent_slug,
            agent,
            workdir,
            emit_reattached_marker=False,
        )

    try:
        await supervisor.register_agent(
            req.work_slug, req.agent_slug, adapter, context, lazy=True
        )
    except RuntimeError:
        with suppress(Exception):
            await adapter.close()
        if not supervisor.is_registered(req.agent_slug):
            raise
        await supervisor.refresh_seq_from_disk(req.agent_slug)

    return agent


async def catch_up_cli_events(
    workstore: WorkStore,
    worktree_manager: WorktreeManager,
    *,
    work_slug: str,
    agent_slug: str,
) -> bool:
    """Merge any provider CLI transcript entries since the last CLI cursor.

    Preconditions: ``agent_slug`` belongs to ``work_slug``.
    Postconditions: returns true when catch-up ran and may have appended
    transcript events.
    """
    agent = next(
        (a for a in workstore.list_agents_for_work(work_slug) if a.slug == agent_slug),
        None,
    )
    if agent is None:
        raise AgentNotFound(f"agent not found: {agent_slug}")
    if agent.session_id is None:
        return False
    if workstore.find_last_detach_cursor(work_slug, agent_slug) is None:
        return False
    workdir = worktree_manager.ensure(
        work_slug=work_slug,
        agent_slug=agent_slug,
        source=agent.folder,
    )
    await asyncio.to_thread(
        _catch_up_detached_agent,
        workstore,
        work_slug,
        agent_slug,
        agent,
        workdir,
        emit_reattached_marker=False,
    )
    return True


def _catch_up_detached_agent(
    workstore: WorkStore,
    work_slug: str,
    agent_slug: str,
    agent: Agent,
    workdir: Path,
    *,
    emit_reattached_marker: bool = True,
) -> None:
    """Merge provider CLI transcript files into Atelier's transcript log."""
    if agent.session_id is None:
        workstore.set_agent_status(agent_slug, AgentStatus.IDLE)
        return

    if agent.parent_session_id and not workstore.is_session_ingested(
        work_slug, agent_slug, agent.parent_session_id
    ):
        parent_events = merge_cli_transcript(
            agent.provider, agent.parent_session_id, workdir, None
        )
        for event in parent_events:
            workstore.append_transcript_event_with_seq(work_slug, agent_slug, event)
        workstore.append_transcript_event_with_seq(
            work_slug,
            agent_slug,
            {
                "type": "sdk_session_merged",
                "ts": datetime.now(UTC).isoformat(),
                "session_id": agent.parent_session_id,
                "events_merged": len(parent_events),
            },
        )

    cursor = workstore.find_last_detach_cursor(work_slug, agent_slug)
    new_events = merge_cli_transcript(agent.provider, agent.session_id, workdir, cursor)
    for event in new_events:
        workstore.append_transcript_event_with_seq(work_slug, agent_slug, event)
    if emit_reattached_marker or new_events:
        workstore.append_transcript_event_with_seq(
            work_slug,
            agent_slug,
            {
                "type": "user_reattached",
                "ts": datetime.now(UTC).isoformat(),
                "events_merged": len(new_events),
                "sdk_cursor": sdk_cursor_at_detach(
                    agent.provider, agent.session_id, workdir
                ),
            },
        )
    workstore.set_agent_status(agent_slug, AgentStatus.IDLE)


__all__ = [
    "AgentNotFound",
    "ResumeAgentRequest",
    "catch_up_cli_events",
    "resume_agent",
]
