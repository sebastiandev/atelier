"""Resume a blocked planning artifact run."""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING

from src.domain.commands.planning import _loop_persistence, _loop_runtime
from src.domain.loop.dtos import LoopStatus, LoopStepKind, LoopStepStatus
from src.domain.loop.ports import LoopRunRepository
from src.domain.loop.prompts import (
    ReviewStagePrompt,
    TaskStagePrompt,
    build_stage_prompt,
)
from src.domain.loop.snapshots import definition_from_snapshot
from src.domain.loop.transitions import stage_by_id
from src.domain.planning import actions
from src.domain.planning.dtos import PlanArtifactDetail, PlanRunStatus
from src.domain.planning.loop import blocker_resolved_prompt
from src.domain.planning.ports import PlanningFiles
from src.domain.planning.service import (
    PlanArtifactNotExecutable,
    PlanArtifactNotFound,
    PlanArtifactRunNotFound,
    PlanningNotStarted,
)
from src.domain.sharedfolders.ports import SharedFolderStore, ShareProvisioner
from src.domain.workstore.ports import WorkStore
from src.domain.worktrees import WorktreeManager
from src.settings import Settings

if TYPE_CHECKING:
    from src.domain.supervisor import AgentSupervisorService


class WorkNotFound(ValueError):
    """The work_slug doesn't exist."""


class AgentNotFound(ValueError):
    """The run agent doesn't belong to the work."""


class PlanArtifactRunNotResumable(ValueError):
    """The run is not blocked for user input."""


@dataclass(frozen=True)
class ResumeArtifactRunRequest:
    """Command input for resuming one blocked artifact run."""

    work_slug: str
    artifact_id: str
    run_id: str
    resolution_note: str = ""


async def execute(
    workstore: WorkStore,
    files: PlanningFiles,
    loop_runs: LoopRunRepository,
    supervisor: AgentSupervisorService,
    worktree_manager: WorktreeManager,
    sharestore: SharedFolderStore,
    share_provisioner: ShareProvisioner,
    settings: Settings,
    req: ResumeArtifactRunRequest,
) -> PlanArtifactDetail:
    """Resume a blocked-user artifact run.

    Preconditions: Work, artifact, run, and a ``blocked_user`` loop exist.
    Postconditions: the run agent receives the resolution prompt and the run
    is marked running again.
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
    current_stage_id = actions.str_or_empty(loop.get("current_stage_id"))
    current_stage_row = _stage_row(loop, current_stage_id)
    agent_slug = (
        actions.str_or_empty(current_stage_row.get("agent_slug"))
        if current_stage_row is not None
        else actions.str_or_empty(run.get("agent_slug"))
    )
    if workstore.get_work_slug_for_agent(agent_slug) != req.work_slug:
        raise AgentNotFound(f"agent not found on work: {agent_slug}")

    status = actions.loop_status(loop.get("status"), actions.run_status(run))
    if status != LoopStatus.BLOCKED_USER:
        raise PlanArtifactRunNotResumable(
            f"plan artifact run is not blocked for user input: {req.run_id}"
        )
    cursor = _loop_runtime.last_transcript_seq(
        workstore,
        work_slug=req.work_slug,
        agent_slug=agent_slug,
    )
    try:
        await _loop_runtime.send_loop_prompt(
            workstore,
            supervisor,
            worktree_manager,
            sharestore,
            share_provisioner,
            settings,
            work_slug=req.work_slug,
            agent_slug=agent_slug,
            prompt=(
                blocker_resolved_prompt(detail.artifact, loop, req.resolution_note)
                if bool(loop.get("legacy", True))
                else _stage_resume_prompt(
                    req,
                    detail,
                    loop,
                    current_stage_id,
                    current_stage_row,
                )
            ),
        )
    except _loop_runtime.AgentNotFound as exc:
        raise AgentNotFound(str(exc)) from exc
    if bool(loop.get("legacy", True)):
        actions.mark_run_running(
            manifest,
            artifact_id=req.artifact_id,
            run_id=req.run_id,
            existing_loop=loop,
            last_checked_seq=cursor,
        )
    else:
        if current_stage_row is None:
            raise PlanArtifactRunNotResumable("blocked loop has no current stage")
        current_stage_row["status"] = LoopStepStatus.RUNNING.value
        current_stage_row["attempt"] = (
            actions.int_or_default(current_stage_row.get("attempt"), 1) + 1
        )
        run["status"] = PlanRunStatus.RUNNING.value
        run["completed_at"] = None
        loop["status"] = LoopStatus.RUNNING.value
        loop["status_reason"] = "The blocked stage resumed after user input."
        loop["last_checked_seq"] = cursor
        loop["attempt"] = actions.int_or_default(loop.get("attempt"), 1) + 1
        run["loop"] = loop
        manifest["updated_at"] = actions.now_iso()
    files.write_manifest(req.work_slug, manifest)
    saved_run = actions.find_run_by_id(
        actions.artifact_runs_for_update(manifest, req.artifact_id), req.run_id
    )
    if saved_run is not None:
        _loop_persistence.persist_artifact_run(
            loop_runs,
            work_slug=req.work_slug,
            artifact=detail.artifact,
            run=saved_run,
        )
    return actions.detail_or_raise(files, req.work_slug, req.artifact_id)


def _stage_resume_prompt(
    req: ResumeArtifactRunRequest,
    detail: PlanArtifactDetail,
    loop: dict[str, object],
    stage_id: str,
    stage_row: dict[str, object] | None,
) -> str:
    definition = definition_from_snapshot(loop.get("definition_snapshot"))
    stage = stage_by_id(definition, stage_id)
    prompt_type = (
        ReviewStagePrompt
        if stage.kind == LoopStepKind.AGENT_REVIEW
        else TaskStagePrompt
    )
    findings = stage_row.get("findings", []) if stage_row else []
    return build_stage_prompt(
        prompt_type(
            run_id=req.run_id,
            work_slug=req.work_slug,
            artifact_id=detail.artifact.id,
            artifact_title=detail.artifact.title,
            source_ref=detail.artifact.source_ref,
            stage=stage,
            previous_summary=(
                actions.str_or_empty(stage_row.get("summary")) if stage_row else ""
            ),
            previous_findings=tuple(
                item for item in findings if isinstance(item, str)
            )
            if isinstance(findings, list)
            else (),
            resolution_note=req.resolution_note,
            resolved_context=tuple(
                actions.str_list(stage_row.get("resolved_context"))
                if stage_row
                else []
            ),
            context_warnings=tuple(
                actions.str_list(stage_row.get("context_warnings"))
                if stage_row
                else []
            ),
        )
    )


def _stage_row(
    loop: dict[str, object], stage_id: str
) -> dict[str, object] | None:
    rows = loop.get("stages")
    if not isinstance(rows, list):
        return None
    return next(
        (
            row
            for row in rows
            if isinstance(row, dict) and row.get("id") == stage_id
        ),
        None,
    )


__all__ = [
    "AgentNotFound",
    "PlanArtifactNotExecutable",
    "PlanArtifactNotFound",
    "PlanArtifactRunNotFound",
    "PlanArtifactRunNotResumable",
    "PlanningNotStarted",
    "ResumeArtifactRunRequest",
    "WorkNotFound",
    "execute",
]
