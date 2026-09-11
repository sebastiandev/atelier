"""Mark a story worked by agents as done."""

from __future__ import annotations

from dataclasses import dataclass

from src.domain.loop.ports import LoopRunRepository
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


class PlanArtifactNotAgentMode(ValueError):
    """Only agent-mode stories accept without a run; loops accept their run."""


@dataclass(frozen=True)
class AcceptArtifactRequest:
    """Command input for accepting a story worked by hand-launched agents."""

    work_slug: str
    artifact_id: str
    summary: str = ""
    changes: str = ""
    validation_evidence: str = ""


def execute(
    workstore: WorkStore,
    files: PlanningFiles,
    loop_runs: LoopRunRepository,
    req: AcceptArtifactRequest,
) -> PlanArtifactDetail:
    """Accept an agent-mode story the same way a loop run's acceptance does.

    Preconditions: the Work exists; the artifact is executable and in agent
    mode (``work_mode == "agents"``).
    Postconditions: ``summaries/<artifact>-agents.md`` holds the user's
    summary, the manifest's ``accepted_artifacts`` entry carries the current
    source hash, and the story reads ``accepted`` until its source changes.
    Agents are not stopped -- they are the user's to close.
    """
    if workstore.get_work(req.work_slug) is None:
        raise WorkNotFound(f"work not found: {req.work_slug}")
    detail = actions.detail_or_raise(files, loop_runs, req.work_slug, req.artifact_id)
    actions.require_executable(detail.artifact)
    if detail.artifact.work_mode != "agents":
        raise PlanArtifactNotAgentMode(
            f"plan artifact {req.artifact_id} is not worked by agents; accept its run instead"
        )
    now = actions.now_iso()
    dto = AcceptPlanArtifactRequest(
        artifact_id=req.artifact_id,
        summary=req.summary,
        changes=req.changes,
        validation_evidence=req.validation_evidence,
    )
    summary_path = f"summaries/{req.artifact_id}-agents.md"
    files.write_text(
        req.work_slug, summary_path, actions.summary_template(detail.artifact, dto, now)
    )
    manifest = actions.manifest_or_raise(files, req.work_slug)
    accepted = manifest.setdefault("accepted_artifacts", {})
    accepted[req.artifact_id] = {
        "accepted_at": now,
        "source_hash": detail.artifact.source_hash,
        "summary_path": summary_path,
    }
    manifest["updated_at"] = now
    manifest["source_hashes"] = actions.current_hashes(files, loop_runs, req.work_slug)
    files.write_manifest(req.work_slug, manifest)
    return actions.detail_or_raise(files, loop_runs, req.work_slug, req.artifact_id)


__all__ = [
    "AcceptArtifactRequest",
    "PlanArtifactNotAgentMode",
    "PlanArtifactNotExecutable",
    "PlanArtifactNotFound",
    "PlanningNotStarted",
    "WorkNotFound",
    "execute",
]
