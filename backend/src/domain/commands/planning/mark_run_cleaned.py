"""Mark an artifact agent run workspace as cleaned up."""

from __future__ import annotations

from dataclasses import dataclass

from src.domain.loop import runtime
from src.domain.loop.dtos import LoopStatus
from src.domain.loop.ports import LoopRunRepository
from src.domain.planning import actions
from src.domain.planning.dtos import PlanArtifactDetail, PlanRunStatus
from src.domain.planning.loop_persistence import persist_artifact_run
from src.domain.planning.ports import PlanningFiles
from src.domain.planning.service import (
    PlanArtifactNotFound,
    PlanArtifactRunNotFound,
    PlanningNotStarted,
)
from src.domain.supervisor import AgentSupervisorService
from src.domain.workstore.ports import WorkStore


class WorkNotFound(ValueError):
    """The work_slug doesn't exist."""


class PlanArtifactRunNotCleanable(ValueError):
    """The run has not been approved or accepted."""


@dataclass(frozen=True)
class MarkArtifactRunCleanedRequest:
    """Command input for recording cleanup of one artifact run."""

    work_slug: str
    artifact_id: str
    run_id: str


async def execute(
    workstore: WorkStore,
    files: PlanningFiles,
    loop_runs: LoopRunRepository,
    supervisor: AgentSupervisorService,
    req: MarkArtifactRunCleanedRequest,
) -> PlanArtifactDetail:
    """Release an accepted run's providers and record legacy cleanup state.

    Preconditions: Work, artifact, and linked run exist and the run is accepted.
    Postconditions: provider runtimes are stopped and cleanup time is recorded;
    agents, transcripts, and workspace remain.
    """
    if workstore.get_work(req.work_slug) is None:
        raise WorkNotFound(f"work not found: {req.work_slug}")
    manifest = actions.manifest_or_raise(files, req.work_slug)
    detail = actions.detail_or_raise(files, req.work_slug, req.artifact_id)
    run = actions.find_run_by_id(
        actions.artifact_runs_for_update(manifest, req.artifact_id),
        req.run_id,
    )
    if run is None:
        raise PlanArtifactRunNotFound(f"plan artifact run not found: {req.run_id}")
    loop = actions.dict_or_empty(run.get("loop"))
    loop_status = actions.loop_status(loop.get("status"), actions.run_status(run))
    if actions.run_status(run) != PlanRunStatus.ACCEPTED or loop_status not in {
        LoopStatus.ACCEPTED,
        LoopStatus.CLEANED,
    }:
        raise PlanArtifactRunNotCleanable(f"plan artifact run is not accepted: {req.run_id}")

    await runtime.release_run_agents(
        workstore,
        supervisor,
        work_slug=req.work_slug,
        run=run,
        loop=loop,
    )

    now = actions.now_iso()
    run["cleanup_at"] = now
    loop["status"] = LoopStatus.CLEANED.value
    loop["status_reason"] = (
        "Provider runtimes were released; transcripts and workspace were kept."
    )
    run["loop"] = loop
    manifest["updated_at"] = now
    files.write_manifest(req.work_slug, manifest)
    persist_artifact_run(
        loop_runs,
        work_slug=req.work_slug,
        artifact=detail.artifact,
        run=run,
    )
    return actions.detail_or_raise(files, req.work_slug, req.artifact_id)


__all__ = [
    "MarkArtifactRunCleanedRequest",
    "PlanArtifactNotFound",
    "PlanArtifactRunNotCleanable",
    "PlanArtifactRunNotFound",
    "PlanningNotStarted",
    "WorkNotFound",
    "execute",
]
