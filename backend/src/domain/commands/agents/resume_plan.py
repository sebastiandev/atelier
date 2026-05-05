"""Build a plan to re-launch an existing agent on the supervisor.

Sister command of ``start_plan``. The agent row already exists; this
re-uses the persisted persona/role/provider/model plus ``session_id``
so the provider SDK resumes the prior conversation. Used by the WS
handler when a client reconnects to an agent that is no longer running
(e.g. after a backend restart or an explicit "close to rail").

Provider options (Claude's permission_mode / thinking_effort, etc.) are
not persisted today, so resume falls back to spec defaults — identity
survives but non-default option choices are reset.
"""

from __future__ import annotations

from dataclasses import dataclass

from src.domain.agents import (
    SPECS,
    AgentAdapter,
    AgentStartContext,
    CommonAgentConfig,
    render_system_prompt,
)
from src.domain.models import Agent
from src.domain.workstore.dtos import WorkRecord
from src.domain.workstore.ports import WorkStore
from src.domain.worktrees import WorktreeManager
from src.infrastructure.agents import build_adapter
from src.settings import Settings


@dataclass(frozen=True)
class ResumeAgentRequest:
    work_slug: str
    agent_slug: str


@dataclass(frozen=True)
class ResumeAgentPlan:
    agent: Agent
    adapter: AgentAdapter
    context: AgentStartContext


class AgentNotFound(ValueError):
    """The (work_slug, agent_slug) pair doesn't resolve to a stored agent."""


def execute(
    workstore: WorkStore,
    worktree_manager: WorktreeManager,
    settings: Settings,
    req: ResumeAgentRequest,
) -> ResumeAgentPlan:
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
    return ResumeAgentPlan(agent=agent, adapter=adapter, context=context)
