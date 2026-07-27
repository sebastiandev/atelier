"""Resume a paused Planning artifact run."""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING

from src.domain.agents.ports import AgentAdapterFactory
from src.domain.connections import ConnectionStore
from src.domain.loop import lifecycle
from src.domain.loop.ports import LoopRunRepository
from src.domain.planning import actions
from src.domain.planning.dtos import PlanArtifactDetail
from src.domain.planning.loop_store import PlanningLoopRunStore
from src.domain.planning.ports import PlanningFiles
from src.domain.planning.service import (
    PlanArtifactNotExecutable,
    PlanArtifactNotFound,
    PlanArtifactRunNotFound,
    PlanningNotStarted,
)
from src.domain.sharedfolders.ports import SharedFolderStore, ShareProvisioner
from src.domain.workstore.ports import WorkStore
from src.domain.worktrees import WorktreeManager
from src.settings import Settings

if TYPE_CHECKING:
    from src.domain.supervisor import AgentSupervisorService


class WorkNotFound(ValueError):
    """The work_slug doesn't exist."""


class AgentNotFound(ValueError):
    """The run agent doesn't belong to the Work."""


class PlanArtifactRunNotResumable(ValueError):
    """The run cannot resume from its current state."""


@dataclass(frozen=True)
class ResumeArtifactRunRequest:
    """Command input for resuming one paused artifact run."""

    work_slug: str
    artifact_id: str
    run_id: str
    resolution_note: str = ""
    retry_failed: bool = False
    gate_decision: str | None = None
    enforced_findings: tuple[int, ...] = ()


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
    settings: Settings,
    req: ResumeArtifactRunRequest,
) -> PlanArtifactDetail:
    """Resume a Planning artifact through the shared Loop lifecycle.

    Preconditions: Work, artifact, and run exist in the requested paused state.
    Postconditions: the reused stage workspace is running and state is persisted.
    """
    if workstore.get_work(req.work_slug) is None:
        raise WorkNotFound(f"work not found: {req.work_slug}")
    manifest = actions.manifest_or_raise(files, req.work_slug)
    detail = actions.detail_or_raise(files, loop_runs, req.work_slug, req.artifact_id)
    actions.require_executable(detail.artifact)
    run = actions.find_run_by_id(
        actions.artifact_runs_for_update(manifest, req.artifact_id),
        req.run_id,
    )
    if run is None:
        raise PlanArtifactRunNotFound(f"plan artifact run not found: {req.run_id}")
    store = PlanningLoopRunStore(files, loop_runs, req.artifact_id)
    target = store.load(req.work_slug, req.run_id)
    if target is None:
        raise PlanArtifactRunNotFound(f"plan artifact run not found: {req.run_id}")
    try:
        await lifecycle.resume(
            target,
            workstore,
            supervisor,
            worktree_manager,
            connection_store,
            sharestore,
            share_provisioner,
            adapter_factory,
            settings,
            resolution_note=req.resolution_note,
            retry_failed=req.retry_failed,
            gate_decision=req.gate_decision,
            enforced_findings=req.enforced_findings,
        )
    except lifecycle.LoopAgentNotFound as exc:
        raise AgentNotFound(str(exc)) from exc
    except lifecycle.LoopRunNotResumable as exc:
        raise PlanArtifactRunNotResumable(str(exc)) from exc
    store.save(target)
    return actions.detail_or_raise(files, loop_runs, req.work_slug, req.artifact_id)


__all__ = [
    "AgentNotFound",
    "PlanArtifactNotExecutable",
    "PlanArtifactNotFound",
    "PlanArtifactRunNotFound",
    "PlanArtifactRunNotResumable",
    "PlanningNotStarted",
    "ResumeArtifactRunRequest",
    "WorkNotFound",
    "execute",
]
