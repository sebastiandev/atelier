"""Start a follow-up run from one finished story run."""

from __future__ import annotations

from dataclasses import dataclass

from src.domain.agents.ports import AgentAdapterFactory
from src.domain.connections.ports import ConnectionStore
from src.domain.loop import followups
from src.domain.loop.dtos import LoopRunKind
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
            run_kind=req.kind,
            follow_up_note=req.note,
            entry_stage_id=followups.entry_stage_id(definition, req.kind),
        ),
    )


__all__ = ["PlanArtifactRunNotFound", "RerunArtifactRunRequest", "execute"]
