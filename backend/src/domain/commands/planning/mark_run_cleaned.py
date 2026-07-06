"""Mark an artifact agent run workspace as cleaned up."""

from dataclasses import dataclass

from src.domain.planning import actions
from src.domain.planning.dtos import PlanArtifactDetail
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
class MarkArtifactRunCleanedRequest:
    """Command input for recording cleanup of one artifact run."""

    work_slug: str
    artifact_id: str
    agent_slug: str


def execute(
    workstore: WorkStore,
    files: PlanningFiles,
    req: MarkArtifactRunCleanedRequest,
) -> PlanArtifactDetail:
    """Set cleanup metadata on a linked artifact run.

    Preconditions: Work, artifact, and linked run exist.
    Postconditions: the run has ``cleanup_at`` set in the manifest.
    """
    if workstore.get_work(req.work_slug) is None:
        raise WorkNotFound(f"work not found: {req.work_slug}")
    manifest = actions.manifest_or_raise(files, req.work_slug)
    actions.detail_or_raise(files, req.work_slug, req.artifact_id)
    run = actions.find_run(
        actions.artifact_runs_for_update(manifest, req.artifact_id),
        req.agent_slug,
    )
    if run is None:
        raise PlanArtifactRunNotFound(f"plan artifact run not found: {req.agent_slug}")
    now = actions.now_iso()
    run["cleanup_at"] = now
    manifest["updated_at"] = now
    files.write_manifest(req.work_slug, manifest)
    return actions.detail_or_raise(files, req.work_slug, req.artifact_id)


__all__ = [
    "MarkArtifactRunCleanedRequest",
    "PlanArtifactNotFound",
    "PlanArtifactRunNotFound",
    "PlanningNotStarted",
    "WorkNotFound",
    "execute",
]
