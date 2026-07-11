"""Return an awaiting-approval run to its configured write stage."""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING

from src.domain.commands.planning import _loop_persistence, _loop_runtime
from src.domain.loop.dtos import (
    LoopOutcome,
    LoopPermission,
    LoopStatus,
    LoopStepKind,
    LoopStepStatus,
)
from src.domain.loop.ports import LoopRunRepository
from src.domain.loop.prompts import TaskStagePrompt, build_stage_prompt
from src.domain.loop.snapshots import definition_from_snapshot
from src.domain.loop.transitions import stage_by_id
from src.domain.planning import actions
from src.domain.planning.dtos import PlanArtifactDetail, PlanRunStatus
from src.domain.planning.ports import PlanningFiles
from src.domain.planning.service import (
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
    """The Work does not exist."""


class AgentNotFound(ValueError):
    """The configured write-stage agent no longer exists."""


class PlanArtifactRunNotChangeable(ValueError):
    """The run is not awaiting final approval."""


@dataclass(frozen=True)
class RequestRunChangesRequest:
    """Command input for returning a result to implementation."""

    work_slug: str
    artifact_id: str
    run_id: str
    note: str


async def execute(
    workstore: WorkStore,
    files: PlanningFiles,
    loop_runs: LoopRunRepository,
    supervisor: AgentSupervisorService,
    worktree_manager: WorktreeManager,
    sharestore: SharedFolderStore,
    share_provisioner: ShareProvisioner,
    settings: Settings,
    req: RequestRunChangesRequest,
) -> PlanArtifactDetail:
    """Send a review note and resume the loop's write stage.

    Preconditions: the run awaits approval and has a reusable write agent.
    Postconditions: approval is marked changes-requested and the write stage is
    running under the existing monitor contract.
    """
    if workstore.get_work(req.work_slug) is None:
        raise WorkNotFound(f"work not found: {req.work_slug}")
    manifest = actions.manifest_or_raise(files, req.work_slug)
    detail = actions.detail_or_raise(files, req.work_slug, req.artifact_id)
    run = actions.find_run_by_id(
        actions.artifact_runs_for_update(manifest, req.artifact_id), req.run_id
    )
    if run is None:
        raise PlanArtifactRunNotFound(f"plan artifact run not found: {req.run_id}")
    loop = actions.dict_or_empty(run.get("loop"))
    if (
        actions.run_status(run) != PlanRunStatus.COMPLETED_PENDING_REVIEW
        or actions.loop_status(loop.get("status"), actions.run_status(run))
        != LoopStatus.AWAITING_APPROVAL
    ):
        raise PlanArtifactRunNotChangeable(
            f"plan artifact run is not awaiting approval: {req.run_id}"
        )
    definition = definition_from_snapshot(loop.get("definition_snapshot"))
    approval_id = actions.str_or_empty(loop.get("current_stage_id"))
    approval = stage_by_id(definition, approval_id)
    destination = approval.transitions.get(LoopOutcome.CHANGES_REQUESTED)
    if not destination:
        destination = next(
            (
                stage.step_id
                for stage in definition.stages
                if stage.kind == LoopStepKind.AGENT_TASK
                and stage.agent is not None
                and stage.agent.permissions == LoopPermission.WRITE
            ),
            None,
        )
    if not destination:
        raise PlanArtifactRunNotChangeable("loop has no write stage for changes")
    stage = stage_by_id(definition, destination)
    stage_row = _stage_row(loop, destination)
    approval_row = _stage_row(loop, approval_id)
    if stage_row is None or approval_row is None:
        raise PlanArtifactRunNotChangeable("loop stage state is incomplete")
    agent_slug = actions.str_or_empty(stage_row.get("agent_slug"))
    if workstore.get_work_slug_for_agent(agent_slug) != req.work_slug:
        raise AgentNotFound(f"agent not found on work: {agent_slug}")
    cursor = _loop_runtime.last_transcript_seq(
        workstore, work_slug=req.work_slug, agent_slug=agent_slug
    )
    prompt = build_stage_prompt(
        TaskStagePrompt(
            run_id=req.run_id,
            work_slug=req.work_slug,
            artifact_id=detail.artifact.id,
            artifact_title=detail.artifact.title,
            source_ref=detail.artifact.source_ref,
            stage=stage,
            previous_summary=actions.str_or_empty(run.get("summary")),
            previous_findings=tuple(actions.str_list(loop.get("findings"))),
            resolution_note=req.note,
            resolved_context=tuple(actions.str_list(stage_row.get("resolved_context"))),
            context_warnings=tuple(actions.str_list(stage_row.get("context_warnings"))),
        )
    )
    await _loop_runtime.send_loop_prompt(
        workstore,
        supervisor,
        worktree_manager,
        sharestore,
        share_provisioner,
        settings,
        work_slug=req.work_slug,
        agent_slug=agent_slug,
        prompt=prompt,
    )
    approval_row["status"] = LoopStepStatus.CHANGES_REQUESTED.value
    stage_row["status"] = LoopStepStatus.RUNNING.value
    stage_row["attempt"] = actions.int_or_default(stage_row.get("attempt"), 1) + 1
    loop["current_stage_id"] = destination
    loop["status"] = LoopStatus.RUNNING.value
    loop["status_reason"] = f"{stage.name} resumed with requested changes."
    loop["last_checked_seq"] = cursor
    loop["attempt"] = actions.int_or_default(loop.get("attempt"), 1) + 1
    run["status"] = PlanRunStatus.RUNNING.value
    run["completed_at"] = None
    run["loop"] = loop
    manifest["updated_at"] = actions.now_iso()
    files.write_manifest(req.work_slug, manifest)
    _loop_persistence.persist_artifact_run(
        loop_runs,
        work_slug=req.work_slug,
        artifact=detail.artifact,
        run=run,
    )
    return actions.detail_or_raise(files, req.work_slug, req.artifact_id)


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
    "PlanArtifactNotFound",
    "PlanArtifactRunNotChangeable",
    "PlanArtifactRunNotFound",
    "PlanningNotStarted",
    "RequestRunChangesRequest",
    "WorkNotFound",
    "execute",
]
