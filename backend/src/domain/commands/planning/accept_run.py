"""Accept a completed planning artifact run."""

from __future__ import annotations

from dataclasses import dataclass

from src.domain.commands.planning import _loop_persistence
from src.domain.loop.dtos import LoopStatus, LoopStepStatus
from src.domain.loop.ports import LoopRunRepository
from src.domain.planning import actions
from src.domain.planning.dtos import (
    AcceptPlanArtifactRequest,
    PlanArtifactDetail,
    PlanRunStatus,
)
from src.domain.planning.ports import PlanningFiles
from src.domain.planning.service import (
    PlanArtifactNotExecutable,
    PlanArtifactNotFound,
    PlanArtifactRunNotFound,
    PlanningNotStarted,
)
from src.domain.workstore.ports import WorkStore


class WorkNotFound(ValueError):
    """The work_slug doesn't exist."""


class PlanArtifactRunNotAcceptable(ValueError):
    """The run is not completed and ready for review."""


@dataclass(frozen=True)
class AcceptArtifactRunRequest:
    """Command input for accepting one completed artifact run."""

    work_slug: str
    artifact_id: str
    run_id: str
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
    loop_runs: LoopRunRepository,
    req: AcceptArtifactRunRequest,
) -> PlanArtifactDetail:
    """Archive reviewer acceptance for one completed artifact run.

    Preconditions: Work exists and the selected run is completed pending review.
    Postconditions: summary file, accepted run state, and accepted artifact
    metadata are persisted.
    """
    if workstore.get_work(req.work_slug) is None:
        raise WorkNotFound(f"work not found: {req.work_slug}")
    manifest = actions.manifest_or_raise(files, req.work_slug)
    detail = actions.detail_or_raise(files, req.work_slug, req.artifact_id)
    actions.require_executable(detail.artifact)
    run = actions.find_run_by_id(
        actions.artifact_runs_for_update(manifest, req.artifact_id),
        req.run_id,
    )
    if run is None:
        raise PlanArtifactRunNotFound(f"plan artifact run not found: {req.run_id}")
    loop = actions.dict_or_empty(run.get("loop"))
    loop_status = actions.loop_status(loop.get("status"), actions.run_status(run))
    if actions.run_status(run) != PlanRunStatus.COMPLETED_PENDING_REVIEW or loop_status not in {
        LoopStatus.COMPLETED,
        LoopStatus.AWAITING_APPROVAL,
    }:
        raise PlanArtifactRunNotAcceptable(
            f"plan artifact run is not completed: {req.run_id}"
        )

    now = actions.now_iso()
    dto = AcceptPlanArtifactRequest(
        artifact_id=req.artifact_id,
        summary=req.summary or actions.str_or_empty(run.get("summary")),
        agent_slug=actions.str_or_empty(run.get("agent_slug")),
        divergences=req.divergences or actions.str_or_empty(run.get("divergences")),
        skipped_scope=req.skipped_scope or actions.str_or_empty(run.get("skipped_scope")),
        blockers=req.blockers or actions.str_or_empty(run.get("blockers")),
        decisions=req.decisions or actions.str_or_empty(run.get("decisions")),
        changes=req.changes or actions.str_or_empty(run.get("changes")),
        validation_evidence=req.validation_evidence
        or actions.str_or_empty(run.get("validation_evidence")),
    )
    summary_path = f"summaries/{req.artifact_id}-{req.run_id}.md"
    files.write_text(
        req.work_slug,
        summary_path,
        actions.summary_template(detail.artifact, dto, now),
    )
    actions.apply_report_fields(run, dto)
    run["status"] = PlanRunStatus.ACCEPTED.value
    run["completed_at"] = now
    run["report_path"] = summary_path
    loop["status"] = LoopStatus.ACCEPTED.value
    loop["status_reason"] = "The result was approved."
    current_stage = actions.str_or_empty(loop.get("current_stage_id"))
    raw_stages = loop.get("stages")
    if current_stage and isinstance(raw_stages, list):
        for stage in raw_stages:
            if isinstance(stage, dict) and stage.get("id") == current_stage:
                stage["status"] = LoopStepStatus.PASSED.value
                break
    run["loop"] = loop
    accepted = manifest.setdefault("accepted_artifacts", {})
    accepted[req.artifact_id] = {
        "accepted_at": now,
        "source_hash": detail.artifact.source_hash,
        "summary_path": summary_path,
    }
    manifest["updated_at"] = now
    manifest["source_hashes"] = actions.current_hashes(files, req.work_slug)
    files.write_manifest(req.work_slug, manifest)
    _loop_persistence.persist_artifact_run(
        loop_runs,
        work_slug=req.work_slug,
        artifact=detail.artifact,
        run=run,
    )
    return actions.detail_or_raise(files, req.work_slug, req.artifact_id)


__all__ = [
    "AcceptArtifactRunRequest",
    "PlanArtifactNotExecutable",
    "PlanArtifactNotFound",
    "PlanArtifactRunNotAcceptable",
    "PlanArtifactRunNotFound",
    "PlanningNotStarted",
    "WorkNotFound",
    "execute",
]
