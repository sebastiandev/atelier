"""Fetch the current source-backed Plan View or artifact detail."""

from src.domain.planning.dtos import PlanArtifactDetail, WorkPlanView
from src.domain.planning.ports import PlanningFiles
from src.domain.planning.service import PlanningService
from src.domain.workstore.ports import WorkStore


class WorkNotFound(ValueError):
    """The work_slug doesn't exist."""


class PlanNotFound(ValueError):
    """Planning has not been started for this work."""


class ArtifactNotFound(ValueError):
    """The plan artifact doesn't exist."""


def execute(
    workstore: WorkStore, files: PlanningFiles, work_slug: str
) -> WorkPlanView:
    if workstore.get_work(work_slug) is None:
        raise WorkNotFound(f"work not found: {work_slug}")
    plan = PlanningService(files).get_plan(work_slug)
    if plan is None:
        raise PlanNotFound(f"planning not started: {work_slug}")
    return plan


def artifact(
    workstore: WorkStore,
    files: PlanningFiles,
    work_slug: str,
    artifact_id: str,
) -> PlanArtifactDetail:
    if workstore.get_work(work_slug) is None:
        raise WorkNotFound(f"work not found: {work_slug}")
    detail = PlanningService(files).get_artifact(work_slug, artifact_id)
    if detail is None:
        raise ArtifactNotFound(f"plan artifact not found: {artifact_id}")
    return detail


__all__ = ["ArtifactNotFound", "PlanNotFound", "WorkNotFound", "artifact", "execute"]
