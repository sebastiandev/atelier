"""Return an awaiting-approval Planning run to its configured write stage."""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING

from src.domain.loop import lifecycle
from src.domain.loop.ports import LoopRunRepository
from src.domain.planning import actions
from src.domain.planning.dtos import PlanArtifactDetail
from src.domain.planning.loop_store import PlanningLoopRunStore
from src.domain.planning.ports import PlanningFiles
from src.domain.planning.service import (
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

AgentNotFound = lifecycle.LoopAgentNotFound
PlanArtifactRunNotChangeable = lifecycle.LoopRunNotChangeable


class WorkNotFound(ValueError):
    """The Work does not exist."""


@dataclass(frozen=True)
class RequestRunChangesRequest:
    """Command input for returning a result to implementation."""

    work_slug: str
    artifact_id: str
    run_id: str
    note: str


async def execute(
    workstore: WorkStore,
    files: PlanningFiles,
    loop_runs: LoopRunRepository,
    supervisor: AgentSupervisorService,
    worktree_manager: WorktreeManager,
    sharestore: SharedFolderStore,
    share_provisioner: ShareProvisioner,
    settings: Settings,
    req: RequestRunChangesRequest,
) -> PlanArtifactDetail:
    """Adapt a Planning artifact and review note to the shared loop action.

    Preconditions: the Work and Planning artifact run exist. Postconditions: the
    shared action state is persisted back to the Planning manifest and run store.
    """
    if workstore.get_work(req.work_slug) is None:
        raise WorkNotFound(f"work not found: {req.work_slug}")
    actions.detail_or_raise(files, loop_runs, req.work_slug, req.artifact_id)
    store = PlanningLoopRunStore(files, loop_runs, req.artifact_id)
    target = store.load(req.work_slug, req.run_id)
    if target is None:
        raise PlanArtifactRunNotFound(f"plan artifact run not found: {req.run_id}")
    await lifecycle.request_changes(
        target,
        workstore,
        supervisor,
        worktree_manager,
        sharestore,
        share_provisioner,
        settings,
        note=req.note,
    )
    store.save(target)
    return actions.detail_or_raise(files, loop_runs, req.work_slug, req.artifact_id)


__all__ = [
    "AgentNotFound",
    "PlanArtifactNotFound",
    "PlanArtifactRunNotChangeable",
    "PlanArtifactRunNotFound",
    "PlanningNotStarted",
    "RequestRunChangesRequest",
    "WorkNotFound",
    "execute",
]
