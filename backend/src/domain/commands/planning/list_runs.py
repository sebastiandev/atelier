"""List execution runs for one planning artifact."""

from __future__ import annotations

from dataclasses import dataclass

from src.domain.planning import actions
from src.domain.planning.dtos import PlanArtifactRun
from src.domain.planning.ports import PlanningFiles
from src.domain.planning.service import PlanArtifactNotFound, PlanningNotStarted
from src.domain.workstore.ports import WorkStore


class WorkNotFound(ValueError):
    """The work_slug doesn't exist."""


@dataclass(frozen=True)
class ListArtifactRunsRequest:
    """Command input for listing artifact runs."""

    work_slug: str
    artifact_id: str


def execute(
    workstore: WorkStore,
    files: PlanningFiles,
    req: ListArtifactRunsRequest,
) -> list[PlanArtifactRun]:
    """List projected runs for one artifact.

    Preconditions: Work and planning artifact exist.
    Postconditions: no planning files are changed.
    """
    if workstore.get_work(req.work_slug) is None:
        raise WorkNotFound(f"work not found: {req.work_slug}")
    detail = actions.detail_or_raise(files, req.work_slug, req.artifact_id)
    return detail.artifact.runs


__all__ = [
    "ListArtifactRunsRequest",
    "PlanArtifactNotFound",
    "PlanningNotStarted",
    "WorkNotFound",
    "execute",
]
