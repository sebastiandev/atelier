"""Accept a completed Planning artifact run."""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING

from src.domain.loop import actions as loop_actions
from src.domain.loop import lifecycle, runtime
from src.domain.loop.ports import LoopRunRepository
from src.domain.planning import actions
from src.domain.planning.dtos import AcceptPlanArtifactRequest, PlanArtifactDetail
from src.domain.planning.loop_store import PlanningLoopRunStore
from src.domain.planning.ports import PlanningFiles
from src.domain.planning.service import (
    PlanArtifactNotExecutable,
    PlanArtifactNotFound,
    PlanArtifactRunNotFound,
    PlanningNotStarted,
)
from src.domain.workstore.ports import WorkStore

if TYPE_CHECKING:
    from src.domain.supervisor import AgentSupervisorService

PlanArtifactRunNotAcceptable = lifecycle.LoopRunNotAcceptable


class WorkNotFound(ValueError):
    """The work_slug doesn't exist."""


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


async def execute(
    workstore: WorkStore,
    files: PlanningFiles,
    loop_runs: LoopRunRepository,
    supervisor: AgentSupervisorService,
    req: AcceptArtifactRunRequest,
) -> PlanArtifactDetail:
    """Accept a Planning run and release its provider runtimes.

    Preconditions: Work, executable artifact, and completed run exist.
    Postconditions: the loop and Planning metadata are accepted, provider
    runtimes are stopped, and agents, transcripts, and workspace remain.
    """
    if workstore.get_work(req.work_slug) is None:
        raise WorkNotFound(f"work not found: {req.work_slug}")
    detail = actions.detail_or_raise(files, loop_runs, req.work_slug, req.artifact_id)
    actions.require_executable(detail.artifact)
    store = PlanningLoopRunStore(files, loop_runs, req.artifact_id)
    target = store.load(req.work_slug, req.run_id)
    if target is None:
        raise PlanArtifactRunNotFound(f"plan artifact run not found: {req.run_id}")

    dto = AcceptPlanArtifactRequest(
        artifact_id=req.artifact_id,
        summary=req.summary or loop_actions.str_or_empty(target.run.get("summary")),
        agent_slug=loop_actions.str_or_empty(target.run.get("agent_slug")),
        divergences=req.divergences or loop_actions.str_or_empty(target.run.get("divergences")),
        skipped_scope=req.skipped_scope
        or loop_actions.str_or_empty(target.run.get("skipped_scope")),
        blockers=req.blockers or loop_actions.str_or_empty(target.run.get("blockers")),
        decisions=req.decisions or loop_actions.str_or_empty(target.run.get("decisions")),
        changes=req.changes or loop_actions.str_or_empty(target.run.get("changes")),
        validation_evidence=req.validation_evidence
        or loop_actions.str_or_empty(target.run.get("validation_evidence")),
    )
    actions.apply_report_fields(target.run, dto)
    completed = lifecycle.accept(target)
    if not completed:
        store.save(target)
        return actions.detail_or_raise(files, loop_runs, req.work_slug, req.artifact_id)
    now = loop_actions.str_or_empty(target.run.get("completed_at"))
    summary_path = f"summaries/{req.artifact_id}-{req.run_id}.md"
    target.run["report_path"] = summary_path
    files.write_text(
        req.work_slug,
        summary_path,
        actions.summary_template(detail.artifact, dto, now),
    )
    store.save(target)

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
    await runtime.release_run_agents(
        workstore,
        supervisor,
        work_slug=req.work_slug,
        run=target.run,
        loop=loop_actions.dict_or_empty(target.run.get("loop")),
    )
    return actions.detail_or_raise(files, loop_runs, req.work_slug, req.artifact_id)


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
