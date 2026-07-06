"""Archive reviewer acceptance for a plan artifact."""

from dataclasses import dataclass

from src.domain.planning import actions
from src.domain.planning.dtos import AcceptPlanArtifactRequest, PlanArtifactDetail
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
class AcceptArtifactRequest:
    """Command input for accepting executable artifact work."""

    work_slug: str
    artifact_id: str
    summary: str
    agent_slug: str | None = None
    divergences: str = ""
    skipped_scope: str = ""
    blockers: str = ""
    decisions: str = ""
    changes: str = ""
    validation_evidence: str = ""


def execute(
    workstore: WorkStore,
    files: PlanningFiles,
    req: AcceptArtifactRequest,
) -> PlanArtifactDetail:
    """Archive reviewer acceptance for one executable artifact.

    Preconditions: Work exists and artifact is executable.
    Postconditions: summary file and accepted artifact metadata are persisted.
    """
    if workstore.get_work(req.work_slug) is None:
        raise WorkNotFound(f"work not found: {req.work_slug}")
    dto = AcceptPlanArtifactRequest(
        artifact_id=req.artifact_id,
        summary=req.summary,
        agent_slug=req.agent_slug,
        divergences=req.divergences,
        skipped_scope=req.skipped_scope,
        blockers=req.blockers,
        decisions=req.decisions,
        changes=req.changes,
        validation_evidence=req.validation_evidence,
    )
    manifest = actions.manifest_or_raise(files, req.work_slug)
    detail = actions.detail_or_raise(files, req.work_slug, req.artifact_id)
    actions.require_executable(detail.artifact)

    now = actions.now_iso()
    summary_path = f"summaries/{req.artifact_id}.md"
    files.write_text(
        req.work_slug,
        summary_path,
        actions.summary_template(detail.artifact, dto, now),
    )
    run = actions.select_run(
        actions.artifact_runs_for_update(manifest, req.artifact_id),
        req.agent_slug,
    )
    if run is not None:
        actions.apply_report_fields(run, dto)
        run["status"] = "accepted"
        run["completed_at"] = now
        run["report_path"] = summary_path
    accepted = manifest.setdefault("accepted_artifacts", {})
    accepted[req.artifact_id] = {
        "accepted_at": now,
        "source_hash": detail.artifact.source_hash,
        "summary_path": summary_path,
    }
    manifest["updated_at"] = now
    manifest["source_hashes"] = actions.current_hashes(files, req.work_slug)
    files.write_manifest(req.work_slug, manifest)
    return actions.detail_or_raise(files, req.work_slug, req.artifact_id)


__all__ = [
    "AcceptArtifactRequest",
    "PlanArtifactNotExecutable",
    "PlanArtifactNotFound",
    "PlanningNotStarted",
    "WorkNotFound",
    "execute",
]
