"""Launch an agent scoped to one planning story."""

from __future__ import annotations

from dataclasses import replace
from typing import TYPE_CHECKING

from src.domain.agents.launch import AgentLaunchRequest, launch_agent
from src.domain.agents.ports import AgentAdapterFactory
from src.domain.connections import ConnectionStore
from src.domain.loop.ports import LoopRunRepository
from src.domain.models import Agent, Context
from src.domain.planning import actions
from src.domain.planning.plan_index import render_plan_index
from src.domain.planning.ports import PlanningFiles
from src.domain.sharedfolders.ports import SharedFolderStore, ShareProvisioner
from src.domain.workstore.ports import WorkStore
from src.domain.worktrees import WorktreeManager

if TYPE_CHECKING:
    from src.domain.supervisor import AgentSupervisorService


async def execute(
    workstore: WorkStore,
    files: PlanningFiles,
    loop_runs: LoopRunRepository,
    supervisor: AgentSupervisorService,
    worktree_manager: WorktreeManager,
    connection_store: ConnectionStore,
    sharestore: SharedFolderStore,
    share_provisioner: ShareProvisioner,
    adapter_factory: AgentAdapterFactory,
    req: AgentLaunchRequest,
) -> Agent:
    """Launch one agent on a story with the story and plan index attached.

    Preconditions: ``req.artifact_id`` names an executable artifact of the
    Work's plan; the Work exists and is active.
    Postconditions: the agent row carries ``artifact_id``; its contexts hold
    the story source and a plan index ahead of the caller's contexts; the
    story is marked ``agents`` in the manifest (one-way).
    """
    if req.artifact_id is None:
        raise ValueError("artifact_id is required")
    manifest = actions.manifest_or_raise(files, req.work_slug)
    plan = actions.get_plan_or_raise(files, loop_runs, req.work_slug)
    detail = actions.detail_or_raise(files, loop_runs, req.work_slug, req.artifact_id)
    actions.require_executable(detail.artifact)
    story = detail.artifact

    story_context = Context(type="file", value=story.source_ref)
    index_context = Context(type="text", value=render_plan_index(plan, story))
    rest = tuple(c for c in req.contexts if not (c.type == "file" and c.value == story.source_ref))
    agent = await launch_agent(
        workstore,
        supervisor,
        worktree_manager,
        connection_store,
        sharestore,
        share_provisioner,
        adapter_factory,
        replace(req, contexts=(story_context, index_context, *rest)),
    )
    actions.mark_worked_by_agents(manifest, story.id)
    files.write_manifest(req.work_slug, manifest)
    return agent


__all__ = ["execute"]
