"""Start a follow-up run from one finished story run."""

from __future__ import annotations

from dataclasses import dataclass, replace
from typing import Any

from src.domain.agents.ports import AgentAdapterFactory
from src.domain.connections.ports import ConnectionStore
from src.domain.loop import briefs, followups
from src.domain.loop.dtos import (
    LoopBrief,
    LoopBriefAgent,
    LoopRunKind,
    LoopStageBrief,
)
from src.domain.loop.ports import (
    LoopContextResolver,
    LoopDefinitionLocations,
    LoopDefinitionRepository,
    LoopRunRepository,
)
from src.domain.loop.snapshots import definition_from_snapshot
from src.domain.planning import actions
from src.domain.planning.dtos import PlanArtifactDetail
from src.domain.planning.loop_persistence import artifact_run_rows
from src.domain.planning.ports import PlanningFiles, PlanningSessionRepository
from src.domain.planning.service import PlanArtifactRunNotFound
from src.domain.sharedfolders.ports import SharedFolderStore, ShareProvisioner
from src.domain.supervisor import AgentSupervisorService
from src.domain.workstore.ports import WorkStore
from src.domain.worktrees.ports import WorktreeManager
from src.settings import Settings

from . import start_run


@dataclass(frozen=True)
class RerunArtifactRunRequest:
    """Command input for one story follow-up run."""

    work_slug: str
    artifact_id: str
    run_id: str
    kind: LoopRunKind
    note: str = ""


async def execute(
    workstore: WorkStore,
    files: PlanningFiles,
    planning_sessions: PlanningSessionRepository,
    loop_definitions: LoopDefinitionRepository,
    loop_locations: LoopDefinitionLocations,
    loop_runs: LoopRunRepository,
    context_resolver: LoopContextResolver,
    supervisor: AgentSupervisorService,
    worktree_manager: WorktreeManager,
    connection_store: ConnectionStore,
    sharestore: SharedFolderStore,
    share_provisioner: ShareProvisioner,
    adapter_factory: AgentAdapterFactory,
    settings: Settings,
    req: RerunArtifactRunRequest,
) -> PlanArtifactDetail:
    """Re-enter a finished run's loop on its existing branch.

    Preconditions: the named run exists and has reached a terminal state.
    Postconditions: a new run is running, pinned to the source run's loop
    revision and entering at the stage its kind implies. The story's stable
    worktree means the branch -- and any open pull request on it -- is
    carried forward rather than forked.
    """
    source = actions.find_run_by_id(
        artifact_run_rows(loop_runs, req.work_slug, req.artifact_id),
        req.run_id,
    )
    if source is None:
        raise PlanArtifactRunNotFound(f"plan artifact run not found: {req.run_id}")
    loop = actions.dict_or_empty(source.get("loop"))
    followups.require_reusable(
        actions.loop_status(loop.get("status"), actions.run_status(source)),
        req.run_id,
    )
    definition = definition_from_snapshot(loop.get("definition_snapshot"))
    # Carry the source run's pinned brief: a follow-up continues that run, so
    # it keeps its per-stage notes, context and -- crucially -- the providers
    # chosen for it. Without this the entry stage would have no provider,
    # because a story run has no parent agent to inherit one from.
    source_brief = briefs.optional_brief_from_snapshot(source.get("brief"))
    entry_id = followups.entry_stage_id(definition, req.kind)
    brief = _with_inherited_entry_agent(
        workstore,
        req.work_slug,
        source,
        source_brief or LoopBrief(goal=req.artifact_id),
        followups.resolve_entry(definition, entry_id).step_id,
    )
    return await start_run.execute(
        workstore,
        files,
        planning_sessions,
        loop_definitions,
        loop_locations,
        loop_runs,
        context_resolver,
        supervisor,
        worktree_manager,
        connection_store,
        sharestore,
        share_provisioner,
        adapter_factory,
        settings,
        start_run.StartArtifactRunRequest(
            work_slug=req.work_slug,
            artifact_id=req.artifact_id,
            # Pin the source run's revision: a follow-up continues that loop,
            # it does not adopt whatever the library has since become.
            loop_definition_id=actions.str_or_empty(loop.get("definition_id")) or None,
            loop_revision=actions.str_or_empty(loop.get("definition_revision")) or None,
            brief=brief,
            run_kind=req.kind,
            follow_up_note=req.note,
            entry_stage_id=entry_id,
        ),
    )


def _with_inherited_entry_agent(
    workstore: WorkStore,
    work_slug: str,
    source: dict[str, Any],
    brief: LoopBrief,
    entry_step_id: str,
) -> LoopBrief:
    """Pin the source run's provider on the entry stage when nothing else does.

    A follow-up's parent is the run it continues, so it should execute on the
    same provider. Without this, a VERIFY follow-up entering at a review stage
    that pins no provider would be rejected for having nothing to inherit --
    true of a fresh story run, but not of one continuing an existing run.
    """
    existing = next(
        (item for item in brief.stages if item.stage_id == entry_step_id), None
    )
    if existing is not None and existing.agent is not None and existing.agent.provider:
        return brief
    agent_slug = actions.str_or_empty(source.get("agent_slug"))
    agent = next(
        (
            item
            for item in workstore.list_agents_for_work(work_slug)
            if item.slug == agent_slug
        ),
        None,
    )
    if agent is None:
        return brief
    inherited = LoopBriefAgent(provider=agent.provider, model=agent.model)
    if existing is None:
        return replace(
            brief,
            stages=(*brief.stages, LoopStageBrief(stage_id=entry_step_id, agent=inherited)),
        )
    return replace(
        brief,
        stages=tuple(
            replace(item, agent=inherited) if item.stage_id == entry_step_id else item
            for item in brief.stages
        ),
    )


__all__ = ["PlanArtifactRunNotFound", "RerunArtifactRunRequest", "execute"]
