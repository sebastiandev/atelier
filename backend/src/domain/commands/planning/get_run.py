"""Get one execution run for a planning artifact."""

from __future__ import annotations

from dataclasses import dataclass

from src.domain.loop.ports import LoopRunRepository
from src.domain.planning import actions
from src.domain.planning.dtos import PlanArtifactRun
from src.domain.planning.ports import PlanningFiles
from src.domain.planning.service import (
    PlanArtifactNotFound,
    PlanArtifactRunNotFound,
    PlanningNotStarted,
)
from src.domain.workstore.ports import WorkStore


class WorkNotFound(ValueError):
    """The work_slug doesn't exist."""


@dataclass(frozen=True)
class GetArtifactRunRequest:
    """Command input for reading one artifact run."""

    work_slug: str
    artifact_id: str
    run_id: str


def execute(
    workstore: WorkStore,
    files: PlanningFiles,
    loop_runs: LoopRunRepository,
    req: GetArtifactRunRequest,
) -> PlanArtifactRun:
    """Read one projected artifact run.

    Preconditions: Work, planning artifact, and run exist.
    Postconditions: no planning files are changed.
    """
    if workstore.get_work(req.work_slug) is None:
        raise WorkNotFound(f"work not found: {req.work_slug}")
    detail = actions.detail_or_raise(files, loop_runs, req.work_slug, req.artifact_id)
    for run in detail.artifact.runs:
        if run.id == req.run_id:
            return run
    raise PlanArtifactRunNotFound(f"plan artifact run not found: {req.run_id}")


__all__ = [
    "GetArtifactRunRequest",
    "PlanArtifactNotFound",
    "PlanArtifactRunNotFound",
    "PlanningNotStarted",
    "WorkNotFound",
    "execute",
]
