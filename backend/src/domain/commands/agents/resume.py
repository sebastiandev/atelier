"""Re-register an existing agent with the supervisor.

Idempotent: if the supervisor already tracks the agent, this is a no-op
that returns the row. Otherwise it rebuilds the adapter (with the row's
persisted persona/role/provider/model/session_id), runs the detach
catch-up merge if the agent is in ``DETACHED`` state, and registers the
agent with the supervisor.

Race-tolerant: a concurrent caller (e.g. React StrictMode WS double-mount)
can win the registration race; ``register_agent`` raises ``RuntimeError``
in that case. We drop our adapter copy to avoid leaking an SDK process,
verify the agent IS now registered, and return — the caller subscribes
to the existing state.

Lazy spawn: registers the agent with ``lazy=True`` so the supervisor
does NOT start the events pump. The SDK process spawns lazily on the
first ``send_input`` instead, so a re-attach that's only there to
refresh the transcript view doesn't fork a new provider session.
"""

from __future__ import annotations

import asyncio
from contextlib import suppress
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import TYPE_CHECKING

from src.domain.agents import (
    SPECS,
    AgentStartContext,
    CommonAgentConfig,
    render_system_prompt,
)
from src.domain.models import Agent, AgentStatus
from src.domain.workstore.dtos import WorkRecord
from src.domain.workstore.ports import WorkStore
from src.domain.worktrees import WorktreeManager
from src.infrastructure.agents import build_adapter
from src.infrastructure.cli_transcript import merge_cli_transcript
from src.settings import Settings

if TYPE_CHECKING:
    from src.domain.supervisor import AgentSupervisorService


@dataclass(frozen=True)
class ResumeAgentRequest:
    work_slug: str
    agent_slug: str


class AgentNotFound(ValueError):
    """The (work_slug, agent_slug) pair doesn't resolve to a stored agent."""


async def execute(
    workstore: WorkStore,
    supervisor: AgentSupervisorService,
    worktree_manager: WorktreeManager,
    settings: Settings,
    req: ResumeAgentRequest,
) -> Agent:
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

    # Idempotent: already registered. Caller (typically connect) just
    # subscribes to the existing state.
    if supervisor.is_registered(req.agent_slug):
        return agent

    workdir = worktree_manager.ensure(
        work_slug=req.work_slug,
        agent_slug=req.agent_slug,
        source=agent.folder,
    )

    common = CommonAgentConfig(
        workdir=workdir,
        system_prompt=render_system_prompt(agent.persona, agent.role),
    )
    config = SPECS[agent.provider].build(common, agent.model, {})
    adapter = build_adapter(config, settings)
    context = AgentStartContext(
        workdir=common.workdir,
        model=agent.model,
        system_prompt=common.system_prompt,
        session_id=agent.session_id,
    )

    # Detach catch-up runs BEFORE registration so any new CLI events go
    # through ``append_transcript_event_with_seq`` (which seeds the seq
    # safely while the supervisor isn't tracking the agent).
    if agent.status == AgentStatus.DETACHED:
        await asyncio.to_thread(
            _catch_up_detached_agent,
            workstore,
            req.work_slug,
            req.agent_slug,
            agent,
            workdir,
        )

    try:
        await supervisor.register_agent(
            req.work_slug, req.agent_slug, adapter, context, lazy=True
        )
    except RuntimeError:
        # A concurrent caller registered first (StrictMode double-mount,
        # rapid clicks, two tabs). Drop our adapter copy to avoid leaking
        # an SDK process and verify the registration actually exists; if
        # it doesn't, the failure was something other than the race.
        with suppress(Exception):
            await adapter.close()
        if not supervisor.is_registered(req.agent_slug):
            raise

    return agent


def _catch_up_detached_agent(
    workstore: WorkStore,
    work_slug: str,
    agent_slug: str,
    agent: Agent,
    workdir,  # type: ignore[no-untyped-def]  # pathlib.Path; avoiding extra import
) -> None:
    """Read the SDK's transcript file(s), append new events to our NDJSON,
    then flip status back to IDLE.

    Walks ``parent_session_id`` (depth-1 — that's all the agent row stores)
    so an ancestor session that's never been ingested gets exported in
    full. Common case: a manual ``parent_session_id`` backfill on an
    agent whose original conversation lived in a now-orphaned thread.
    Steady-state re-attaches see the parent's ``session_established``
    already in NDJSON and skip the parent merge.
    """
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
    new_events = merge_cli_transcript(
        agent.provider, agent.session_id, workdir, cursor
    )
    for event in new_events:
        workstore.append_transcript_event_with_seq(work_slug, agent_slug, event)
    workstore.append_transcript_event_with_seq(
        work_slug,
        agent_slug,
        {
            "type": "user_reattached",
            "ts": datetime.now(UTC).isoformat(),
            "events_merged": len(new_events),
        },
    )
    workstore.set_agent_status(agent_slug, AgentStatus.IDLE)


__all__ = [
    "AgentNotFound",
    "ResumeAgentRequest",
    "execute",
]
