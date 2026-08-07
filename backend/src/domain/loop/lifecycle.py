"""Lifecycle actions shared by standalone and Planning-owned loop runs."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from src.domain.agents.launch import AgentLaunchRequest, launch_agent
from src.domain.agents.ports import AgentAdapterFactory
from src.domain.artifacts.models import PrArtifact
from src.domain.connections import ConnectionStore
from src.domain.loop import actions, briefs, pass_feedback, pr_lifecycle, runtime
from src.domain.loop.agent_policy import apply_retry_overrides
from src.domain.loop.dtos import (
    LoopContextKind,
    LoopFailureKind,
    LoopOutcome,
    LoopPermission,
    LoopRunStatus,
    LoopStatus,
    LoopStepDefinition,
    LoopStepKind,
    LoopStepStatus,
)
from src.domain.loop.models import LoopRunTarget
from src.domain.loop.prompts import (
    PrStagePrompt,
    ReviewStagePrompt,
    TaskStagePrompt,
    build_stage_prompt,
)
from src.domain.loop.snapshots import definition_from_snapshot
from src.domain.loop.transitions import stage_by_id
from src.domain.sharedfolders.ports import SharedFolderStore, ShareProvisioner
from src.domain.workstore.ports import WorkStore
from src.domain.worktrees import WorktreeManager

if TYPE_CHECKING:
    from src.domain.supervisor import AgentSupervisorService


class LoopAgentNotFound(ValueError):
    """The stage agent does not belong to the run's Work."""


RETRY_RESOLUTION_NOTE = (
    "Retry the failed stage in the existing workspace. A previous attempt may "
    "have left work in progress here — check the current state (e.g. `git "
    "status`/`git diff`) before starting, and continue from anything already "
    "done rather than redoing it from scratch. If nothing was changed yet, "
    "proceed normally."
)
"""Continuation hint every retry gets.

A retry replaces the stage's agent with a fresh transcript, so the new agent
has no memory of the attempt whose work is already in the workspace. Applied
here rather than by each caller so every retry path -- standalone Loop and
Planning alike -- carries it.
"""


class LoopRunNotResumable(ValueError):
    """The run cannot resume from its current state."""


class LoopRunNotChangeable(ValueError):
    """The run is not awaiting final approval."""


class LoopRunNotAcceptable(ValueError):
    """The run is not completed and ready for approval."""


class LoopRunNotCancellable(ValueError):
    """The run is already terminal and cannot be cancelled."""


class LoopStageNotStoppable(ValueError):
    """No stage is currently running, so there is nothing to stop."""


async def resume(
    target: LoopRunTarget,
    workstore: WorkStore,
    supervisor: AgentSupervisorService,
    worktree_manager: WorktreeManager,
    connection_store: ConnectionStore,
    sharestore: SharedFolderStore,
    share_provisioner: ShareProvisioner,
    adapter_factory: AgentAdapterFactory,
    settings: Any,
    *,
    resolution_note: str = "",
    retry_failed: bool = False,
    retry_model: str | None = None,
    retry_effort: str | None = None,
    gate_decision: str | None = None,
    enforced_findings: tuple[int, ...] = (),
) -> None:
    """Resume the current stage in its existing workspace.

    Preconditions: the modern run is blocked, or failed/inactive when retry is
    requested, and its pinned stage is retryable. Postconditions: the stage and
    aggregate are running and the active agent has received its continuation
    prompt.
    """
    run = target.run
    loop = actions.dict_or_empty(run.get("loop"))
    current_stage_id = actions.str_or_empty(loop.get("current_stage_id"))
    current_stage_row = actions.stage_row(loop, current_stage_id)
    if current_stage_row is None:
        raise LoopRunNotResumable("paused loop has no current stage")
    current_status = actions.loop_status(loop.get("status"), actions.run_status(run))
    expected = (
        {LoopStatus.FAILED, LoopStatus.WAITING_REPORT}
        if retry_failed
        else {LoopStatus.BLOCKED_USER}
    )
    if current_status not in expected:
        state = "failed or inactive" if retry_failed else "blocked for user input"
        raise LoopRunNotResumable(f"loop run is not {state}: {target.run_id}")

    # A merged pull request ends this run's road: the PR stage may only update
    # the URL it stored, so anything produced from here cannot be pushed. Refuse
    # to restart rather than let the work run and strand it.
    if _pr_artifacts_show_merged(workstore, target.work_slug, loop):
        raise LoopRunNotResumable(
            f"the pull request for this run was merged; start a new run: {target.run_id}"
        )

    review_gate = loop.get("review_gate")
    if not retry_failed and isinstance(review_gate, dict):
        if gate_decision not in {"send_back", "approve_as_is"}:
            raise LoopRunNotResumable("review gate needs send_back or approve_as_is")
        finding_count = len(actions.str_list(review_gate.get("findings")))
        if any(index < 0 or index >= finding_count for index in enforced_findings):
            raise LoopRunNotResumable("review gate finding selection is invalid")
        loop["review_gate_decision"] = {
            "decision": gate_decision,
            "enforced_findings": sorted(set(enforced_findings)),
            "instruction": resolution_note.strip(),
        }
        loop["status"] = LoopStatus.NEEDS_AGENT.value
        loop["status_reason"] = "Applying the review gate decision."
        run["status"] = LoopRunStatus.RUNNING.value
        run["completed_at"] = None
        run["loop"] = loop
        return

    stage = stage_by_id(
        definition_from_snapshot(loop.get("definition_snapshot")),
        current_stage_id,
    )
    if (
        retry_failed
        and stage.kind
        not in {
            LoopStepKind.AGENT_TASK,
            LoopStepKind.AGENT_REVIEW,
            LoopStepKind.DETERMINISTIC_CHECK,
        }
        and stage.kind.value != "pr"
    ):
        raise LoopRunNotResumable(f"loop stage cannot be retried: {current_stage_id}")
    check_retry = retry_failed and stage.kind == LoopStepKind.DETERMINISTIC_CHECK
    agent_slug = actions.str_or_empty(current_stage_row.get("agent_slug"))
    if not check_retry and workstore.get_work_slug_for_agent(agent_slug) != target.work_slug:
        raise LoopAgentNotFound(f"agent not found on work: {agent_slug}")
    if retry_failed and not check_retry:
        agent_slug = await _launch_retry_agent(
            target,
            workstore,
            supervisor,
            worktree_manager,
            connection_store,
            sharestore,
            share_provisioner,
            adapter_factory,
            stage,
            current_stage_row,
            loop,
            agent_slug,
            retry_model,
            retry_effort,
        )

    cursor = (
        actions.int_or_default(loop.get("last_checked_seq"), 0)
        if check_retry
        else runtime.last_transcript_seq(
            workstore,
            work_slug=target.work_slug,
            agent_slug=agent_slug,
        )
    )
    if not check_retry:
        try:
            await runtime.send_loop_prompt(
                workstore,
                supervisor,
                worktree_manager,
                sharestore,
                share_provisioner,
                settings,
                work_slug=target.work_slug,
                agent_slug=agent_slug,
                prompt=_resume_prompt(
                    target,
                    loop,
                    current_stage_row,
                    stage,
                    _retry_note(resolution_note) if retry_failed else resolution_note,
                    (
                        runtime.workspace_prompt_context(
                            workstore,
                            worktree_manager,
                            work_slug=target.work_slug,
                            agent_slug=agent_slug,
                        )
                        if any(
                            item.kind == LoopContextKind.WORKSPACE_DIFF for item in stage.context
                        )
                        else ""
                    ),
                ),
            )
        except runtime.AgentNotFound as exc:
            raise LoopAgentNotFound(str(exc)) from exc

    current_stage_row["status"] = LoopStepStatus.RUNNING.value
    if not check_retry:
        current_stage_row["attempt"] = (
            actions.int_or_default(current_stage_row.get("attempt"), 1) + 1
        )
        current_stage_row.pop("repair_attempt", None)
    run["status"] = LoopRunStatus.RUNNING.value
    run["completed_at"] = None
    loop["status"] = LoopStatus.RUNNING.value
    loop.pop("failure_kind", None)
    loop["status_reason"] = (
        f"Retrying {current_stage_row.get('name', current_stage_id)} in the kept workspace."
        if retry_failed
        else "The blocked stage resumed after user input."
    )
    loop["last_checked_seq"] = cursor
    loop["attempt"] = actions.int_or_default(loop.get("attempt"), 1) + 1
    run["loop"] = loop


async def _launch_retry_agent(
    target: LoopRunTarget,
    workstore: WorkStore,
    supervisor: AgentSupervisorService,
    worktree_manager: WorktreeManager,
    connection_store: ConnectionStore,
    sharestore: SharedFolderStore,
    share_provisioner: ShareProvisioner,
    adapter_factory: AgentAdapterFactory,
    stage: LoopStepDefinition,
    stage_row: dict[str, Any],
    loop: dict[str, Any],
    previous_slug: str,
    retry_model: str | None = None,
    retry_effort: str | None = None,
) -> str:
    """Launch a retry in a new transcript against the existing worktree.

    Preconditions: ``previous_slug`` owns the failed stage workspace.
    Postconditions: the stage points at a fresh agent and keeps its checkout.
    An optional model/effort override is applied on the same provider; an
    invalid one raises before the previous agent is replaced.
    """
    previous = next(
        (
            agent
            for agent in workstore.list_agents_for_work(target.work_slug)
            if agent.slug == previous_slug
        ),
        None,
    )
    if previous is None or previous.slug is None:
        raise LoopAgentNotFound(f"agent not found on work: {previous_slug}")
    provider, model, options = apply_retry_overrides(
        previous.provider,
        previous.model,
        dict(previous.options or {}),
        model_override=retry_model,
        effort_override=retry_effort,
    )
    await supervisor.stop_agent(previous.slug)
    definition = definition_from_snapshot(loop.get("definition_snapshot"))
    brief = briefs.optional_brief_from_snapshot(target.run.get("brief"))
    launched = await launch_agent(
        workstore,
        supervisor,
        worktree_manager,
        connection_store,
        sharestore,
        share_provisioner,
        adapter_factory,
        AgentLaunchRequest(
            work_slug=target.work_slug,
            name=previous.name,
            persona=previous.persona,
            role=previous.role,
            provider=provider,
            model=model,
            folder=previous.folder,
            contexts=tuple(workstore.get_agent_contexts(target.work_slug, previous.slug)),
            options=options,
            worktree_slug=previous.worktree_slug or previous.slug,
            approved_command_prefixes=tuple(
                dict.fromkeys(
                    (
                        *briefs.resolved_approved_command_prefixes(
                            definition,
                            brief,
                            stage,
                        ),
                        *actions.str_list(stage_row.get("approved_command_prefixes")),
                    )
                )
            ),
        ),
    )
    if launched.slug is None:
        raise RuntimeError("loop retry launch returned an agent without slug")
    stage_row["agent_slug"] = launched.slug
    owned = loop.setdefault("owned_agent_slugs", [])
    if isinstance(owned, list) and launched.slug not in owned:
        owned.append(launched.slug)
    if (
        stage.kind == LoopStepKind.AGENT_TASK
        and stage.agent is not None
        and stage.agent.permissions != LoopPermission.READ
    ):
        loop["source_agent_slug"] = launched.slug
    return launched.slug


async def request_changes(
    target: LoopRunTarget,
    workstore: WorkStore,
    supervisor: AgentSupervisorService,
    worktree_manager: WorktreeManager,
    sharestore: SharedFolderStore,
    share_provisioner: ShareProvisioner,
    settings: Any,
    *,
    note: str,
) -> None:
    """Return an awaiting-approval loop to its configured write stage.

    Preconditions: the modern run awaits approval and retains its write agent.
    Postconditions: approval is changes-requested and the write stage is running.
    """
    run = target.run
    loop = actions.dict_or_empty(run.get("loop"))
    if (
        actions.run_status(run) != LoopRunStatus.COMPLETED_PENDING_REVIEW
        or actions.loop_status(loop.get("status"), actions.run_status(run))
        != LoopStatus.AWAITING_APPROVAL
    ):
        raise LoopRunNotChangeable(f"loop run is not awaiting approval: {target.run_id}")
    definition = definition_from_snapshot(loop.get("definition_snapshot"))
    approval_id = actions.str_or_empty(loop.get("current_stage_id"))
    approval = stage_by_id(definition, approval_id)
    destination = approval.transitions.get(LoopOutcome.CHANGES_REQUESTED) or next(
        (
            stage.step_id
            for stage in definition.stages
            if stage.kind == LoopStepKind.AGENT_TASK
            and stage.agent is not None
            and stage.agent.permissions != LoopPermission.READ
        ),
        None,
    )
    if not destination:
        raise LoopRunNotChangeable("loop has no implementation stage for changes")
    stage = stage_by_id(definition, destination)
    stage_row = actions.stage_row(loop, destination)
    approval_row = actions.stage_row(loop, approval_id)
    if stage_row is None or approval_row is None:
        raise LoopRunNotChangeable("loop stage state is incomplete")
    agent_slug = actions.str_or_empty(stage_row.get("agent_slug"))
    if workstore.get_work_slug_for_agent(agent_slug) != target.work_slug:
        raise LoopAgentNotFound(f"agent not found on work: {agent_slug}")
    cursor = runtime.last_transcript_seq(
        workstore,
        work_slug=target.work_slug,
        agent_slug=agent_slug,
    )
    brief = briefs.optional_brief_from_snapshot(run.get("brief"))
    brief_note, brief_context = briefs.prompt_values(brief, stage.step_id)
    try:
        await runtime.send_loop_prompt(
            workstore,
            supervisor,
            worktree_manager,
            sharestore,
            share_provisioner,
            settings,
            work_slug=target.work_slug,
            agent_slug=agent_slug,
            prompt=build_stage_prompt(
                TaskStagePrompt(
                    run_id=target.run_id,
                    work_slug=target.work_slug,
                    artifact_id=target.target_id,
                    artifact_title=target.title,
                    source_ref=target.source_ref,
                    stage=stage,
                    previous_summary=actions.str_or_empty(run.get("summary")),
                    previous_findings=tuple(actions.str_list(loop.get("findings"))),
                    previous_changed_files=actions.latest_changed_file_prompt_lines(loop),
                    workspace_diff=(
                        runtime.workspace_prompt_context(
                            workstore,
                            worktree_manager,
                            work_slug=target.work_slug,
                            agent_slug=agent_slug,
                        )
                        if any(
                            item.kind == LoopContextKind.WORKSPACE_DIFF for item in stage.context
                        )
                        else ""
                    ),
                    resolution_note=note,
                    resolved_context=tuple(actions.str_list(stage_row.get("resolved_context"))),
                    context_warnings=tuple(actions.str_list(stage_row.get("context_warnings"))),
                    brief_note=brief_note,
                    brief_context=brief_context,
                )
            ),
        )
    except runtime.AgentNotFound as exc:
        raise LoopAgentNotFound(str(exc)) from exc
    pass_feedback.record(loop, note=note, pass_number=actions.start_next_pass(loop))
    approval_row["status"] = LoopStepStatus.CHANGES_REQUESTED.value
    stage_row["status"] = LoopStepStatus.RUNNING.value
    stage_row["attempt"] = actions.int_or_default(stage_row.get("attempt"), 1) + 1
    stage_row.pop("repair_attempt", None)
    loop["current_stage_id"] = destination
    if stage.agent is not None and stage.agent.permissions != LoopPermission.READ:
        loop["source_agent_slug"] = agent_slug
    loop["status"] = LoopStatus.RUNNING.value
    loop["status_reason"] = f"{stage.name} resumed with requested changes."
    loop["last_checked_seq"] = cursor
    loop["attempt"] = actions.int_or_default(loop.get("attempt"), 1) + 1
    run["status"] = LoopRunStatus.RUNNING.value
    run["completed_at"] = None
    run["loop"] = loop


def accept(target: LoopRunTarget) -> bool:
    """Apply approval and return whether it completed the loop run.

    Preconditions: the run is completed and either complete or awaiting approval.
    Postconditions: terminal runs are accepted; an approval stage with an explicit
    pass destination is marked for the shared monitor to advance.
    """
    run = target.run
    loop = actions.dict_or_empty(run.get("loop"))
    if actions.run_status(run) != LoopRunStatus.COMPLETED_PENDING_REVIEW or actions.loop_status(
        loop.get("status"), actions.run_status(run)
    ) not in {LoopStatus.COMPLETED, LoopStatus.AWAITING_APPROVAL}:
        raise LoopRunNotAcceptable(f"loop run is not completed: {target.run_id}")
    current_id = actions.str_or_empty(loop.get("current_stage_id"))
    current_stage = actions.stage_row(
        loop,
        current_id,
    )
    if current_stage is not None:
        current_stage["status"] = LoopStepStatus.PASSED.value

    definition = definition_from_snapshot(loop.get("definition_snapshot"))
    configured = next(
        (stage for stage in definition.stages if stage.step_id == current_id),
        None,
    )
    destination = (
        configured.transitions.get(LoopOutcome.PASS)
        if configured is not None and configured.kind == LoopStepKind.USER_APPROVAL
        else None
    )
    if destination and destination != "complete":
        run["status"] = LoopRunStatus.RUNNING.value
        run["completed_at"] = None
        loop["status"] = LoopStatus.NEEDS_AGENT.value
        loop["status_reason"] = "Approval recorded; starting the next stage."
        loop["approval_decision"] = {"summary": "Approved by user."}
        run["loop"] = loop
        return False

    now = actions.now_iso()
    run["status"] = LoopRunStatus.ACCEPTED.value
    run["completed_at"] = now
    loop["status"] = LoopStatus.ACCEPTED.value
    loop["status_reason"] = "The result was approved."
    # Approving is the answer to any outstanding request for changes.
    pass_feedback.clear(loop)
    run["loop"] = loop
    return True


def cancel(target: LoopRunTarget) -> None:
    """Cancel a non-terminal run while retaining its agents and workspace.

    Preconditions: the run is not accepted, cleaned, cancelled, or failed.
    Postconditions: pending stages and the aggregate are durably cancelled.
    """
    run = target.run
    loop = actions.dict_or_empty(run.get("loop"))
    status = actions.loop_status(loop.get("status"), actions.run_status(run))
    if status in {
        LoopStatus.ACCEPTED,
        LoopStatus.CLEANED,
        LoopStatus.CANCELLED,
        LoopStatus.FAILED,
    }:
        raise LoopRunNotCancellable(f"loop run cannot be cancelled: {target.run_id}")
    stages = loop.get("stages")
    if isinstance(stages, list):
        for stage in stages:
            if isinstance(stage, dict) and stage.get("status") in {
                LoopStepStatus.PENDING.value,
                LoopStepStatus.RUNNING.value,
                LoopStepStatus.BLOCKED_USER.value,
                LoopStepStatus.CHANGES_REQUESTED.value,
            }:
                stage["status"] = LoopStepStatus.CANCELLED.value
    now = actions.now_iso()
    loop["status"] = LoopStatus.CANCELLED.value
    loop["status_reason"] = "The run was cancelled."
    run["status"] = LoopRunStatus.NEEDS_ATTENTION.value
    run["completed_at"] = now
    run["cancelled_at"] = now
    run["loop"] = loop


def stop_stage(target: LoopRunTarget) -> None:
    """Stop the running stage and leave it retryable.

    The stage-level counterpart to ``cancel``: the run keeps its workspace
    and its history, and the failure lands in the same shape a timeout does,
    so the existing failure panel offers retry with its model/effort
    overrides. Only the reason differs.

    Preconditions: the run has a stage currently running.
    Postconditions: that stage is failed, the run is blocked, and the reason
    records that a person stopped it rather than a timeout expiring.
    """
    run = target.run
    loop = actions.dict_or_empty(run.get("loop"))
    status = actions.loop_status(loop.get("status"), actions.run_status(run))
    if status in {
        LoopStatus.ACCEPTED,
        LoopStatus.CLEANED,
        LoopStatus.CANCELLED,
        LoopStatus.FAILED,
    }:
        raise LoopStageNotStoppable(f"loop run is not active: {target.run_id}")
    current = actions.str_or_empty(loop.get("current_stage_id"))
    stage = next(
        (
            row
            for row in loop.get("stages", [])
            if isinstance(row, dict) and row.get("id") == current
        ),
        None,
    )
    if stage is None or stage.get("status") != LoopStepStatus.RUNNING.value:
        raise LoopStageNotStoppable(f"no stage is running: {target.run_id}")
    name = actions.str_or_empty(stage.get("name")) or current
    reason = f"{name} was stopped manually."
    stage["status"] = LoopStepStatus.FAILED.value
    loop["status"] = LoopStatus.FAILED.value
    loop["failure_kind"] = LoopFailureKind.STOPPED.value
    loop["status_reason"] = reason
    loop["findings"] = [reason]
    run["status"] = LoopRunStatus.BLOCKED.value
    run["completed_at"] = actions.now_iso()
    run["loop"] = loop


def _retry_note(resolution_note: str) -> str:
    """Lead a retry's resolution with the pick-up-existing-work hint."""
    return "\n\n".join(
        value for value in (RETRY_RESOLUTION_NOTE, resolution_note.strip()) if value
    )


def _resume_prompt(
    target: LoopRunTarget,
    loop: dict[str, Any],
    stage_row: dict[str, Any],
    stage: LoopStepDefinition,
    resolution_note: str,
    workspace_diff: str,
) -> str:
    """Build the typed continuation prompt for one paused stage."""
    prompt_type = (
        ReviewStagePrompt
        if stage.kind == LoopStepKind.AGENT_REVIEW
        else PrStagePrompt
        if stage.kind == LoopStepKind.PR
        else TaskStagePrompt
    )
    feedback_context = pr_lifecycle.pending_feedback_context(target.run)
    # Only the monitor's forward advance is handed a corrective note; a retry
    # arrives carrying just its own boilerplate, so rebuild the note from the
    # stage's stored copy rather than restarting the agent with nothing to act
    # on. Skipped when the caller already supplied it, so it renders once.
    corrective = actions.str_or_empty(stage_row.get("corrective_note"))
    if corrective and corrective in resolution_note:
        corrective = ""
    resolution = "\n\n".join(
        value
        for value in (
            pass_feedback.context(target.run),
            feedback_context,
            corrective,
            resolution_note.strip(),
        )
        if value
    )
    brief = briefs.optional_brief_from_snapshot(target.run.get("brief"))
    brief_note, brief_context = briefs.prompt_values(brief, stage.step_id)
    previous_row = actions.declared_previous_row(loop, stage) or stage_row
    return build_stage_prompt(
        prompt_type(
            run_id=target.run_id,
            work_slug=target.work_slug,
            artifact_id=target.target_id,
            artifact_title=target.title,
            source_ref=target.source_ref,
            stage=stage,
            previous_summary=actions.str_or_empty(previous_row.get("summary")),
            previous_findings=tuple(actions.str_list(previous_row.get("findings"))),
            previous_validation_evidence=actions.str_or_empty(
                previous_row.get("validation_evidence")
            ),
            previous_changed_files=(
                actions.changed_file_prompt_lines(previous_row.get("changed_files"))
                or actions.latest_changed_file_prompt_lines(loop)
            ),
            workspace_diff=workspace_diff,
            resolution_note=resolution,
            resolved_context=tuple(actions.str_list(stage_row.get("resolved_context"))),
            context_warnings=tuple(actions.str_list(stage_row.get("context_warnings"))),
            brief_note=brief_note,
            brief_context=brief_context,
        )
    )


__all__ = [
    "LoopAgentNotFound",
    "LoopRunNotAcceptable",
    "LoopRunNotCancellable",
    "LoopRunNotChangeable",
    "LoopRunNotResumable",
    "LoopStageNotStoppable",
    "accept",
    "cancel",
    "request_changes",
    "resume",
    "stop_stage",
]


def _pr_artifacts_show_merged(
    workstore: WorkStore,
    work_slug: str,
    loop: dict[str, Any],
) -> bool:
    """Whether the poller has seen this run's pull request merged."""
    if not pr_lifecycle.has_pull_request(loop):
        return False
    return (
        pr_lifecycle.live_pr_status(
            loop,
            [
                artifact
                for artifact in workstore.list_artifacts_for_work(work_slug)
                if isinstance(artifact, PrArtifact)
            ],
        )
        == "merged"
    )
