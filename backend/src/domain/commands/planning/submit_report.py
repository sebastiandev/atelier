"""Submit an agent report for a plan artifact."""

from dataclasses import dataclass

from src.domain.planning import actions
from src.domain.planning.dtos import PlanArtifactDetail, SubmitPlanArtifactReportRequest
from src.domain.planning.ports import PlanningFiles
from src.domain.planning.service import (
    PlanArtifactNotExecutable,
    PlanArtifactNotFound,
    PlanningNotStarted,
)
from src.domain.workstore.ports import WorkStore


class WorkNotFound(ValueError):
    """The work_slug doesn't exist."""


@dataclass(frozen=True)
class SubmitArtifactReportRequest:
    """Command input for storing an artifact run report."""

    work_slug: str
    artifact_id: str
    agent_slug: str | None = None
    summary: str = ""
    divergences: str = ""
    skipped_scope: str = ""
    blockers: str = ""
    decisions: str = ""
    changes: str = ""
    validation_evidence: str = ""


def execute(
    workstore: WorkStore,
    files: PlanningFiles,
    req: SubmitArtifactReportRequest,
) -> PlanArtifactDetail:
    """Persist structured report fields and assess the artifact loop.

    Preconditions: Work and executable artifact exist.
    Postconditions: selected/latest run stores report fields and loop status.
    """
    if workstore.get_work(req.work_slug) is None:
        raise WorkNotFound(f"work not found: {req.work_slug}")
    dto = SubmitPlanArtifactReportRequest(
        artifact_id=req.artifact_id,
        agent_slug=req.agent_slug,
        summary=req.summary,
        divergences=req.divergences,
        skipped_scope=req.skipped_scope,
        blockers=req.blockers,
        decisions=req.decisions,
        changes=req.changes,
        validation_evidence=req.validation_evidence,
    )
    manifest = actions.manifest_or_raise(files, req.work_slug)
    detail = actions.detail_or_raise(files, req.work_slug, req.artifact_id)
    now = actions.now_iso()
    actions.apply_artifact_report(manifest, detail.artifact, dto, now=now)
    manifest["updated_at"] = now
    files.write_manifest(req.work_slug, manifest)
    return actions.detail_or_raise(files, req.work_slug, req.artifact_id)


__all__ = [
    "PlanArtifactNotExecutable",
    "PlanArtifactNotFound",
    "PlanningNotStarted",
    "SubmitArtifactReportRequest",
    "WorkNotFound",
    "execute",
]
