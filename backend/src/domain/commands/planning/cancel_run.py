"""Cancel an active Planning artifact run."""

from __future__ import annotations

from dataclasses import dataclass

from src.domain.loop import actions as loop_actions
from src.domain.loop import lifecycle, runtime
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
from src.domain.supervisor import AgentSupervisorService
from src.domain.workstore.ports import WorkStore


class WorkNotFound(ValueError):
    """The requested Work does not exist."""


@dataclass(frozen=True)
class CancelArtifactRunRequest:
    """Command input for cancelling one Planning artifact run."""

    work_slug: str
    artifact_id: str
    run_id: str


async def execute(
    workstore: WorkStore,
    files: PlanningFiles,
    loop_runs: LoopRunRepository,
    supervisor: AgentSupervisorService,
    req: CancelArtifactRunRequest,
) -> PlanArtifactDetail:
    """Cancel a run while preserving its workspace and transcript.

    Preconditions: Work, artifact, and a non-terminal run exist.
    Postconditions: provider runtimes are released and the run is cancelled.
    """
    if workstore.get_work(req.work_slug) is None:
        raise WorkNotFound(f"work not found: {req.work_slug}")
    store = PlanningLoopRunStore(files, loop_runs, req.artifact_id)
    target = store.load(req.work_slug, req.run_id)
    if target is None:
        raise PlanArtifactRunNotFound(f"plan artifact run not found: {req.run_id}")
    lifecycle.cancel(target)
    loop = loop_actions.dict_or_empty(target.run.get("loop"))
    await runtime.release_run_agents(
        workstore,
        supervisor,
        work_slug=req.work_slug,
        run=target.run,
        loop=loop,
    )
    store.save(target)
    return actions.detail_or_raise(files, req.work_slug, req.artifact_id)


__all__ = [
    "CancelArtifactRunRequest",
    "PlanArtifactNotFound",
    "PlanArtifactRunNotFound",
    "PlanningNotStarted",
    "WorkNotFound",
    "execute",
]
