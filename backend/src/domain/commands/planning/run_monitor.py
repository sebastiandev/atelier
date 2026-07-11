"""Monitor and advance planning artifact loop runs."""

from __future__ import annotations

import asyncio
import shlex
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from typing import TYPE_CHECKING, Any, cast
from uuid import uuid4

from src.domain.agents.launch import AgentLaunchRequest, launch_agent
from src.domain.agents.ports import AgentAdapterFactory
from src.domain.commands.planning import _loop_persistence, _loop_runtime
from src.domain.connections import ConnectionStore
from src.domain.loop.agent_policy import apply_stage_agent_policy
from src.domain.loop.dtos import (
    LoopCheckRequest,
    LoopCheckResult,
    LoopOutcome,
    LoopPermission,
    LoopReportSource,
    LoopStageReport,
    LoopStatus,
    LoopStepKind,
    LoopStepStatus,
)
from src.domain.loop.ports import LoopCheckRunner, LoopRunRepository
from src.domain.loop.prompts import (
    ReviewStagePrompt,
    TaskStagePrompt,
    build_stage_prompt,
    stage_report_repair_prompt,
)
from src.domain.loop.reports import extract_latest_stage_report
from src.domain.loop.snapshots import definition_from_snapshot
from src.domain.loop.transitions import (
    LoopTransitionInvalid,
    stage_by_id,
    transition_destination,
)
from src.domain.models import Provider
from src.domain.planning import actions
from src.domain.planning.dtos import (
    PlanArtifactDetail,
    PlanArtifactRun,
    PlanRunStatus,
    SubmitPlanArtifactReportRequest,
)
from src.domain.planning.loop import PLANNING_ARTIFACT_LOOP_DEFINITION, continuation_prompt
from src.domain.planning.ports import PlanningFiles
from src.domain.planning.report_extract import extract_latest_report
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
    from src.domain.loop.dtos import LoopDefinition, LoopStepDefinition
    from src.domain.supervisor import AgentSupervisorService


class WorkNotFound(ValueError):
    """The Work does not exist."""


class AgentNotFound(ValueError):
    """The active stage agent does not belong to the Work."""


@dataclass(frozen=True)
class MonitorArtifactRunRequest:
    """Command input for monitoring one artifact run."""

    work_slug: str
    artifact_id: str
    run_id: str
    poll_interval_seconds: float = 0.5
    idle_timeout_seconds: float | None = None
    worker_id: str = field(default_factory=lambda: f"worker-{uuid4().hex}")


async def execute(
    workstore: WorkStore,
    files: PlanningFiles,
    supervisor: AgentSupervisorService,
    worktree_manager: WorktreeManager,
    connection_store: ConnectionStore,
    sharestore: SharedFolderStore,
    share_provisioner: ShareProvisioner,
    adapter_factory: AgentAdapterFactory,
    check_runner: LoopCheckRunner,
    loop_runs: LoopRunRepository,
    settings: Settings,
    req: MonitorArtifactRunRequest,
) -> PlanArtifactDetail:
    """Claim and monitor one persisted run until it reaches a pause state."""
    _manifest, detail, _run, loop = _load_active_run(workstore, files, req)
    run_key = actions.str_or_none(loop.get("loop_run_id"))
    leased = run_key is not None and loop_runs.get(req.work_slug, run_key) is not None
    if leased and run_key is not None and not loop_runs.claim(
        req.work_slug,
        run_key,
        req.worker_id,
        _lease_expiry(),
    ):
        return detail
    try:
        return await _execute_claimed(
            workstore,
            files,
            supervisor,
            worktree_manager,
            connection_store,
            sharestore,
            share_provisioner,
            adapter_factory,
            check_runner,
            loop_runs,
            settings,
            req,
            run_key if leased else None,
        )
    finally:
        if leased and run_key is not None:
            loop_runs.release(req.work_slug, run_key, req.worker_id)


async def _execute_claimed(
    workstore: WorkStore,
    files: PlanningFiles,
    supervisor: AgentSupervisorService,
    worktree_manager: WorktreeManager,
    connection_store: ConnectionStore,
    sharestore: SharedFolderStore,
    share_provisioner: ShareProvisioner,
    adapter_factory: AgentAdapterFactory,
    check_runner: LoopCheckRunner,
    loop_runs: LoopRunRepository,
    settings: Settings,
    req: MonitorArtifactRunRequest,
    lease_run_key: str | None,
) -> PlanArtifactDetail:
    """Advance one run until it pauses, fails, or awaits approval.

    Preconditions: Work, artifact, run, and active stage agent exist.
    Postconditions: every consumed report is persisted before the next stage is
    launched; invalid reports receive bounded automatic repair prompts.
    """
    idle_for = 0.0
    while True:
        manifest, detail, run, loop = _load_active_run(workstore, files, req)
        if lease_run_key is not None and not loop_runs.claim(
            req.work_slug,
            lease_run_key,
            req.worker_id,
            _lease_expiry(),
        ):
            return detail
        if actions.run_status(run) != PlanRunStatus.RUNNING:
            return detail
        if not bool(loop.get("legacy", True)):
            definition = definition_from_snapshot(loop.get("definition_snapshot"))
            current_id = actions.str_or_empty(loop.get("current_stage_id"))
            current_stage = stage_by_id(definition, current_id)
            if current_stage.kind == LoopStepKind.DETERMINISTIC_CHECK:
                current_row = _stage_row(loop, current_id)
                if current_row is None:
                    raise LoopTransitionInvalid(
                        f"loop stage state not found: {current_id}"
                    )
                detail = await _run_check_stage(
                    workstore,
                    files,
                    supervisor,
                    worktree_manager,
                    connection_store,
                    sharestore,
                    share_provisioner,
                    adapter_factory,
                    check_runner,
                    settings,
                    req,
                    detail,
                    manifest,
                    run,
                    loop,
                    definition,
                    current_stage,
                    current_row,
                )
                _persist_current(loop_runs, files, req)
                projected = _projected_run(detail, req.run_id)
                if projected is None or projected.status != PlanRunStatus.RUNNING:
                    return detail
                continue
        agent_slug = _active_agent_slug(run, loop)
        if workstore.get_work_slug_for_agent(agent_slug) != req.work_slug:
            raise AgentNotFound(f"agent not found on work: {agent_slug}")
        cursor = actions.int_or_default(loop.get("last_checked_seq"), 0)
        events = list(
            workstore.read_transcript_from_cursor(req.work_slug, agent_slug, cursor)
        )
        latest_seq = _latest_seq(events, cursor)

        if bool(loop.get("legacy", True)):
            extracted = extract_latest_report(events)
            if extracted is None:
                _store_cursor_if_advanced(files, req, manifest, run, loop, latest_seq, cursor)
                if latest_seq > cursor:
                    _persist_current(loop_runs, files, req)
                if _idle_expired(req, idle_for):
                    return actions.detail_or_raise(files, req.work_slug, req.artifact_id)
                await asyncio.sleep(req.poll_interval_seconds)
                idle_for += req.poll_interval_seconds
                continue
            legacy_report, report_seq = extracted
            detail = await _apply_legacy_report(
                workstore,
                files,
                supervisor,
                worktree_manager,
                sharestore,
                share_provisioner,
                settings,
                req,
                detail,
                agent_slug,
                legacy_report,
                report_seq,
            )
        else:
            extracted_stage = extract_latest_stage_report(events)
            if extracted_stage is None:
                _store_cursor_if_advanced(files, req, manifest, run, loop, latest_seq, cursor)
                if latest_seq > cursor:
                    _persist_current(loop_runs, files, req)
                if _idle_expired(req, idle_for):
                    return actions.detail_or_raise(files, req.work_slug, req.artifact_id)
                await asyncio.sleep(req.poll_interval_seconds)
                idle_for += req.poll_interval_seconds
                continue
            stage_report, report_seq = extracted_stage
            detail = await _apply_stage_report(
                workstore,
                files,
                supervisor,
                worktree_manager,
                connection_store,
                sharestore,
                share_provisioner,
                adapter_factory,
                check_runner,
                settings,
                req,
                detail,
                agent_slug,
                stage_report,
                report_seq,
            )

        idle_for = 0.0
        _persist_current(loop_runs, files, req)
        projected = _projected_run(detail, req.run_id)
        if projected is None or projected.status != PlanRunStatus.RUNNING:
            return detail


def _load_active_run(
    workstore: WorkStore,
    files: PlanningFiles,
    req: MonitorArtifactRunRequest,
) -> tuple[dict[str, Any], PlanArtifactDetail, dict[str, Any], dict[str, Any]]:
    if workstore.get_work(req.work_slug) is None:
        raise WorkNotFound(f"work not found: {req.work_slug}")
    manifest = actions.manifest_or_raise(files, req.work_slug)
    detail = actions.detail_or_raise(files, req.work_slug, req.artifact_id)
    actions.require_executable(detail.artifact)
    run = actions.find_run_by_id(
        actions.artifact_runs_for_update(manifest, req.artifact_id), req.run_id
    )
    if run is None:
        raise PlanArtifactRunNotFound(f"plan artifact run not found: {req.run_id}")
    return manifest, detail, run, actions.dict_or_empty(run.get("loop"))


def _active_agent_slug(run: dict[str, Any], loop: dict[str, Any]) -> str:
    current = actions.str_or_empty(loop.get("current_stage_id"))
    stage = _stage_row(loop, current) if current else None
    return actions.str_or_empty(stage.get("agent_slug")) if stage else actions.str_or_empty(
        run.get("agent_slug")
    )


async def _apply_stage_report(
    workstore: WorkStore,
    files: PlanningFiles,
    supervisor: AgentSupervisorService,
    worktree_manager: WorktreeManager,
    connection_store: ConnectionStore,
    sharestore: SharedFolderStore,
    share_provisioner: ShareProvisioner,
    adapter_factory: AgentAdapterFactory,
    check_runner: LoopCheckRunner,
    settings: Settings,
    req: MonitorArtifactRunRequest,
    detail: PlanArtifactDetail,
    agent_slug: str,
    report: LoopStageReport | None,
    report_seq: int,
) -> PlanArtifactDetail:
    manifest, _, run, loop = _load_active_run(workstore, files, req)
    definition = definition_from_snapshot(loop.get("definition_snapshot"))
    current_id = actions.str_or_empty(loop.get("current_stage_id"))
    stage = stage_by_id(definition, current_id)
    stage_row = _stage_row(loop, current_id)
    if stage_row is None:
        raise LoopTransitionInvalid(f"loop stage state not found: {current_id}")

    problem = _stage_report_problem(stage, report)
    if problem:
        return await _repair_stage_report(
            workstore,
            files,
            supervisor,
            worktree_manager,
            sharestore,
            share_provisioner,
            settings,
            req,
            run,
            loop,
            stage,
            stage_row,
            agent_slug,
            report_seq,
            problem,
        )
    assert report is not None
    _record_stage_report(stage_row, report, report_seq)
    loop["last_checked_seq"] = report_seq
    loop["findings"] = list(report.findings)
    _copy_report_to_run(run, report)
    return await _advance_after_stage_report(
        workstore,
        files,
        supervisor,
        worktree_manager,
        connection_store,
        sharestore,
        share_provisioner,
        adapter_factory,
        check_runner,
        settings,
        req,
        detail,
        manifest,
        run,
        loop,
        definition,
        current_id,
        stage_row,
        report,
    )


async def _advance_after_stage_report(
    workstore: WorkStore,
    files: PlanningFiles,
    supervisor: AgentSupervisorService,
    worktree_manager: WorktreeManager,
    connection_store: ConnectionStore,
    sharestore: SharedFolderStore,
    share_provisioner: ShareProvisioner,
    adapter_factory: AgentAdapterFactory,
    check_runner: LoopCheckRunner,
    settings: Settings,
    req: MonitorArtifactRunRequest,
    detail: PlanArtifactDetail,
    manifest: dict[str, Any],
    run: dict[str, Any],
    loop: dict[str, Any],
    definition: LoopDefinition,
    current_id: str,
    stage_row: dict[str, Any],
    report: LoopStageReport,
) -> PlanArtifactDetail:
    """Persist one stage outcome and enter its configured destination."""
    if report.outcome == LoopOutcome.BLOCKED_USER:
        stage_row["status"] = LoopStepStatus.BLOCKED_USER.value
        run["status"] = PlanRunStatus.BLOCKED.value
        run["completed_at"] = actions.now_iso()
        loop["status"] = LoopStatus.BLOCKED_USER.value
        loop["status_reason"] = report.blocker
        run["loop"] = loop
        _write_run(files, req, manifest)
        return actions.detail_or_raise(files, req.work_slug, req.artifact_id)

    destination = transition_destination(definition, current_id, report.outcome)
    stage_row["status"] = (
        LoopStepStatus.CHANGES_REQUESTED.value
        if report.outcome == LoopOutcome.CHANGES_REQUESTED
        else LoopStepStatus.FAILED.value
        if report.outcome == LoopOutcome.FAILED
        else LoopStepStatus.PASSED.value
    )
    if destination == "pause":
        _fail_run(run, loop, "Loop paused without a user blocker.", [])
        _write_run(files, req, manifest)
        return actions.detail_or_raise(files, req.work_slug, req.artifact_id)
    if destination == "fail":
        _fail_run(run, loop, report.summary, list(report.findings))
        _write_run(files, req, manifest)
        return actions.detail_or_raise(files, req.work_slug, req.artifact_id)
    if destination == "complete":
        _await_approval(run, loop, current_id=None)
        _write_run(files, req, manifest)
        return actions.detail_or_raise(files, req.work_slug, req.artifact_id)

    next_stage = stage_by_id(definition, destination)
    next_row = _stage_row(loop, next_stage.step_id)
    if next_row is None:
        raise LoopTransitionInvalid(f"loop stage state not found: {destination}")
    if next_stage.kind == LoopStepKind.USER_APPROVAL:
        next_row["status"] = LoopStepStatus.PENDING.value
        _await_approval(run, loop, current_id=next_stage.step_id)
        _write_run(files, req, manifest)
        return actions.detail_or_raise(files, req.work_slug, req.artifact_id)
    if next_stage.kind == LoopStepKind.DETERMINISTIC_CHECK:
        return await _run_check_stage(
            workstore,
            files,
            supervisor,
            worktree_manager,
            connection_store,
            sharestore,
            share_provisioner,
            adapter_factory,
            check_runner,
            settings,
            req,
            detail,
            manifest,
            run,
            loop,
            definition,
            next_stage,
            next_row,
        )

    next_agent = await _launch_or_resume_stage_agent(
        workstore,
        supervisor,
        worktree_manager,
        connection_store,
        sharestore,
        share_provisioner,
        adapter_factory,
        req,
        definition,
        next_stage,
        next_row,
        loop,
    )
    cursor = _loop_runtime.last_transcript_seq(
        workstore, work_slug=req.work_slug, agent_slug=next_agent
    )
    await _send_stage_prompt(
        workstore,
        supervisor,
        worktree_manager,
        sharestore,
        share_provisioner,
        settings,
        req,
        detail,
        next_stage,
        next_row,
        next_agent,
        report,
    )
    next_row["status"] = LoopStepStatus.RUNNING.value
    next_row["attempt"] = actions.int_or_default(next_row.get("attempt"), 0) + 1
    next_row["agent_slug"] = next_agent
    _record_owned_agent(loop, next_agent)
    loop["current_stage_id"] = next_stage.step_id
    loop["last_checked_seq"] = cursor
    loop["status"] = LoopStatus.RUNNING.value
    loop["status_reason"] = f"{next_stage.name} is running."
    run["status"] = PlanRunStatus.RUNNING.value
    run["completed_at"] = None
    run["loop"] = loop
    _write_run(files, req, manifest)
    return actions.detail_or_raise(files, req.work_slug, req.artifact_id)


async def _run_check_stage(
    workstore: WorkStore,
    files: PlanningFiles,
    supervisor: AgentSupervisorService,
    worktree_manager: WorktreeManager,
    connection_store: ConnectionStore,
    sharestore: SharedFolderStore,
    share_provisioner: ShareProvisioner,
    adapter_factory: AgentAdapterFactory,
    check_runner: LoopCheckRunner,
    settings: Settings,
    req: MonitorArtifactRunRequest,
    detail: PlanArtifactDetail,
    manifest: dict[str, Any],
    run: dict[str, Any],
    loop: dict[str, Any],
    definition: LoopDefinition,
    stage: LoopStepDefinition,
    stage_row: dict[str, Any],
) -> PlanArtifactDetail:
    """Run one configured check and immediately advance from its result."""
    source_slug = _write_stage_agent_slug(definition, loop)
    source = next(
        (
            agent
            for agent in workstore.list_agents_for_work(req.work_slug)
            if agent.slug == source_slug
        ),
        None,
    )
    if source is None:
        raise AgentNotFound(f"source agent not found on work: {source_slug}")
    workdir = worktree_manager.ensure(req.work_slug, source_slug, source.folder)
    stage_row["status"] = LoopStepStatus.RUNNING.value
    stage_row["attempt"] = actions.int_or_default(stage_row.get("attempt"), 0) + 1
    loop["current_stage_id"] = stage.step_id
    loop["status"] = LoopStatus.RUNNING.value
    loop["status_reason"] = f"{stage.name} is running."
    run["loop"] = loop
    _write_run(files, req, manifest)
    try:
        result = await check_runner.run(
            LoopCheckRequest(
                workdir=workdir,
                argv=stage.check_command,
                timeout_seconds=float(stage.retry.timeout_minutes * 60),
            )
        )
    except (OSError, ValueError) as exc:
        result = LoopCheckResult(exit_code=None, stderr=str(exc))
    report = _check_report(stage, result)
    _record_stage_report(
        stage_row,
        report,
        actions.int_or_default(loop.get("last_checked_seq"), 0),
    )
    loop["findings"] = list(report.findings)
    _copy_report_to_run(run, report)
    return await _advance_after_stage_report(
        workstore,
        files,
        supervisor,
        worktree_manager,
        connection_store,
        sharestore,
        share_provisioner,
        adapter_factory,
        check_runner,
        settings,
        req,
        detail,
        manifest,
        run,
        loop,
        definition,
        stage.step_id,
        stage_row,
        report,
    )


def _check_report(
    stage: LoopStepDefinition,
    result: LoopCheckResult,
) -> LoopStageReport:
    """Convert one process result into the common loop report contract."""
    passed = result.exit_code == 0 and not result.timed_out
    can_request_changes = bool(
        stage.transitions.get(LoopOutcome.CHANGES_REQUESTED)
    )
    outcome = (
        LoopOutcome.PASS
        if passed
        else LoopOutcome.CHANGES_REQUESTED
        if can_request_changes and not result.timed_out
        else LoopOutcome.FAILED
    )
    status = "timed out" if result.timed_out else f"exited {result.exit_code}"
    evidence = [f"$ {shlex.join(stage.check_command)}", status]
    if result.stdout.strip():
        evidence.append(result.stdout.strip())
    if result.stderr.strip():
        evidence.append(result.stderr.strip())
    return LoopStageReport(
        outcome=outcome,
        summary=f"{stage.name} {'passed' if passed else 'failed'} ({status}).",
        findings=() if passed else (f"{stage.name} {status}.",),
        changes="None.",
        validation_evidence="\n".join(evidence),
        divergences="None.",
        skipped_scope="None.",
        blocker="None.",
    )


def _stage_report_problem(
    stage: LoopStepDefinition, report: LoopStageReport | None
) -> str:
    if report is None:
        return "Missing or invalid atelier_loop_step_report."
    if report.outcome == LoopOutcome.BLOCKED_USER and _explicit_none(report.blocker):
        return "A blocked_user report needs a concrete blocker."
    if stage.kind == LoopStepKind.AGENT_TASK:
        if report.outcome == LoopOutcome.CHANGES_REQUESTED:
            return "Implementation stages cannot request changes from themselves."
        if report.outcome == LoopOutcome.PASS and not report.validation_evidence.strip():
            return "A passing implementation report needs validation evidence."
    if (
        stage.kind == LoopStepKind.AGENT_REVIEW
        and report.outcome == LoopOutcome.CHANGES_REQUESTED
        and not report.findings
    ):
        return "A changes-requested review needs actionable findings."
    return ""


async def _repair_stage_report(
    workstore: WorkStore,
    files: PlanningFiles,
    supervisor: AgentSupervisorService,
    worktree_manager: WorktreeManager,
    sharestore: SharedFolderStore,
    share_provisioner: ShareProvisioner,
    settings: Settings,
    req: MonitorArtifactRunRequest,
    run: dict[str, Any],
    loop: dict[str, Any],
    stage: LoopStepDefinition,
    stage_row: dict[str, Any],
    agent_slug: str,
    report_seq: int,
    problem: str,
) -> PlanArtifactDetail:
    attempts = actions.int_or_default(stage_row.get("attempt"), 1)
    manifest = actions.manifest_or_raise(files, req.work_slug)
    persisted_run = actions.find_run_by_id(
        actions.artifact_runs_for_update(manifest, req.artifact_id), req.run_id
    )
    if persisted_run is None:
        raise PlanArtifactRunNotFound(f"plan artifact run not found: {req.run_id}")
    persisted_loop = actions.dict_or_empty(persisted_run.get("loop"))
    persisted_row = _stage_row(persisted_loop, stage.step_id)
    if persisted_row is None:
        raise LoopTransitionInvalid(f"loop stage state not found: {stage.step_id}")
    if attempts >= stage.retry.max_attempts:
        persisted_row["status"] = LoopStepStatus.FAILED.value
        _fail_run(persisted_run, persisted_loop, problem, [problem])
        _write_run(files, req, manifest)
        return actions.detail_or_raise(files, req.work_slug, req.artifact_id)
    await _loop_runtime.send_loop_prompt(
        workstore,
        supervisor,
        worktree_manager,
        sharestore,
        share_provisioner,
        settings,
        work_slug=req.work_slug,
        agent_slug=agent_slug,
        prompt=stage_report_repair_prompt(stage),
    )
    persisted_row["attempt"] = attempts + 1
    persisted_loop["attempt"] = actions.int_or_default(persisted_loop.get("attempt"), 1) + 1
    persisted_loop["last_checked_seq"] = report_seq
    persisted_loop["status_reason"] = problem
    persisted_run["loop"] = persisted_loop
    _write_run(files, req, manifest)
    return actions.detail_or_raise(files, req.work_slug, req.artifact_id)


async def _launch_or_resume_stage_agent(
    workstore: WorkStore,
    supervisor: AgentSupervisorService,
    worktree_manager: WorktreeManager,
    connection_store: ConnectionStore,
    sharestore: SharedFolderStore,
    share_provisioner: ShareProvisioner,
    adapter_factory: AgentAdapterFactory,
    req: MonitorArtifactRunRequest,
    definition: LoopDefinition,
    stage: LoopStepDefinition,
    stage_row: dict[str, Any],
    loop: dict[str, Any],
) -> str:
    if stage.agent is None:
        raise LoopTransitionInvalid(f"agent policy missing for stage: {stage.step_id}")
    existing = actions.str_or_none(stage_row.get("agent_slug"))
    if stage.agent.session.value == "reuse" and existing:
        return existing

    agents = {
        agent.slug: agent
        for agent in workstore.list_agents_for_work(req.work_slug)
        if agent.slug is not None
    }
    source_slug = _write_stage_agent_slug(definition, loop)
    source = agents.get(source_slug)
    if source is None:
        raise AgentNotFound(f"source agent not found on work: {source_slug}")
    provider = cast(Provider, stage.agent.provider or source.provider)
    model = stage.agent.model or source.model
    options = apply_stage_agent_policy(
        provider,
        dict(source.options or {}) if provider == source.provider else {},
        stage.agent,
    )
    launched = await launch_agent(
        workstore,
        supervisor,
        worktree_manager,
        connection_store,
        sharestore,
        share_provisioner,
        adapter_factory,
        AgentLaunchRequest(
            work_slug=req.work_slug,
            name=f"{stage.name} · {req.artifact_id}",
            persona="architect" if stage.kind == LoopStepKind.AGENT_REVIEW else "developer",
            role=stage.instructions,
            provider=provider,
            model=model,
            folder=source.folder,
            options=options,
            fork_from_agent=source_slug,
        ),
    )
    if launched.slug is None:
        raise RuntimeError("loop stage launch returned an agent without slug")
    return launched.slug


def _write_stage_agent_slug(definition: LoopDefinition, loop: dict[str, Any]) -> str:
    for stage in reversed(definition.stages):
        if stage.agent is None or stage.agent.permissions != LoopPermission.WRITE:
            continue
        row = _stage_row(loop, stage.step_id)
        slug = actions.str_or_none(row.get("agent_slug")) if row else None
        if slug:
            return slug
    return ""


async def _send_stage_prompt(
    workstore: WorkStore,
    supervisor: AgentSupervisorService,
    worktree_manager: WorktreeManager,
    sharestore: SharedFolderStore,
    share_provisioner: ShareProvisioner,
    settings: Settings,
    req: MonitorArtifactRunRequest,
    detail: PlanArtifactDetail,
    stage: LoopStepDefinition,
    stage_row: dict[str, Any],
    agent_slug: str,
    previous: LoopStageReport,
) -> None:
    prompt_type = ReviewStagePrompt if stage.kind == LoopStepKind.AGENT_REVIEW else TaskStagePrompt
    prompt = build_stage_prompt(
        prompt_type(
            run_id=req.run_id,
            work_slug=req.work_slug,
            artifact_id=detail.artifact.id,
            artifact_title=detail.artifact.title,
            source_ref=detail.artifact.source_ref,
            stage=stage,
            previous_summary=previous.summary,
            previous_findings=previous.findings,
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


def _record_stage_report(
    stage_row: dict[str, Any], report: LoopStageReport, report_seq: int
) -> None:
    value = {
        "outcome": report.outcome.value,
        "summary": report.summary,
        "findings": list(report.findings),
        "changes": report.changes,
        "validation_evidence": report.validation_evidence,
        "divergences": report.divergences,
        "skipped_scope": report.skipped_scope,
        "blocker": report.blocker,
        "artifact_refs": list(report.artifact_refs),
        "finding_details": [
            {
                "text": finding.text,
                "severity": finding.severity.value,
                "location": finding.location,
            }
            for finding in report.finding_details
        ],
        "criteria_coverage": [
            {"text": criterion.text, "met": criterion.met, "note": criterion.note}
            for criterion in report.criteria_coverage
        ],
        "changed_files": [
            {
                "path": changed.path,
                "additions": changed.additions,
                "deletions": changed.deletions,
            }
            for changed in report.changed_files
        ],
        "seq": report_seq,
        "recorded_at": actions.now_iso(),
    }
    reports = stage_row.setdefault("reports", [])
    if isinstance(reports, list):
        reports.append(value)
    stage_row["summary"] = report.summary
    stage_row["findings"] = list(report.findings)
    stage_row["changes"] = report.changes
    stage_row["validation_evidence"] = report.validation_evidence
    stage_row["divergences"] = report.divergences
    stage_row["skipped_scope"] = report.skipped_scope
    stage_row["blocker"] = report.blocker
    stage_row["artifact_refs"] = list(report.artifact_refs)
    stage_row["finding_details"] = value["finding_details"]
    stage_row["criteria_coverage"] = value["criteria_coverage"]
    stage_row["changed_files"] = value["changed_files"]


def _copy_report_to_run(run: dict[str, Any], report: LoopStageReport) -> None:
    run["summary"] = report.summary
    run["divergences"] = report.divergences
    run["skipped_scope"] = report.skipped_scope
    run["blockers"] = report.blocker
    run["changes"] = report.changes
    run["validation_evidence"] = report.validation_evidence


def _await_approval(
    run: dict[str, Any], loop: dict[str, Any], *, current_id: str | None
) -> None:
    now = actions.now_iso()
    run["status"] = PlanRunStatus.COMPLETED_PENDING_REVIEW.value
    run["completed_at"] = now
    loop["status"] = LoopStatus.AWAITING_APPROVAL.value
    loop["status_reason"] = "All automatic stages passed; result approval is required."
    loop["current_stage_id"] = current_id or ""
    run["loop"] = loop


def _fail_run(
    run: dict[str, Any], loop: dict[str, Any], reason: str, findings: list[str]
) -> None:
    run["status"] = PlanRunStatus.BLOCKED.value
    run["completed_at"] = actions.now_iso()
    loop["status"] = LoopStatus.FAILED.value
    loop["status_reason"] = reason
    loop["findings"] = findings
    run["loop"] = loop


def _stage_row(loop: dict[str, Any], step_id: str) -> dict[str, Any] | None:
    rows = loop.get("stages")
    if not isinstance(rows, list):
        return None
    return next(
        (
            row
            for row in rows
            if isinstance(row, dict) and row.get("id") == step_id
        ),
        None,
    )


def _record_owned_agent(loop: dict[str, Any], agent_slug: str) -> None:
    """Track a transient stage agent once for eventual run cleanup."""
    raw = loop.setdefault("owned_agent_slugs", [])
    if isinstance(raw, list) and agent_slug not in raw:
        raw.append(agent_slug)


def _explicit_none(value: str) -> bool:
    return value.strip().lower() in {"", "none", "none.", "n/a", "not applicable"}


def _write_run(
    files: PlanningFiles,
    req: MonitorArtifactRunRequest,
    manifest: dict[str, Any],
) -> None:
    manifest["updated_at"] = actions.now_iso()
    files.write_manifest(req.work_slug, manifest)


def _store_cursor_if_advanced(
    files: PlanningFiles,
    req: MonitorArtifactRunRequest,
    manifest: dict[str, Any],
    run: dict[str, Any],
    loop: dict[str, Any],
    latest_seq: int,
    cursor: int,
) -> None:
    if latest_seq <= cursor:
        return
    loop["last_checked_seq"] = latest_seq
    run["loop"] = loop
    _write_run(files, req, manifest)


def _idle_expired(req: MonitorArtifactRunRequest, idle_for: float) -> bool:
    return req.idle_timeout_seconds is not None and idle_for >= req.idle_timeout_seconds


def _lease_expiry() -> datetime:
    """Return the next persisted monitor lease deadline."""
    return datetime.now(UTC) + timedelta(seconds=60)


async def _apply_legacy_report(
    workstore: WorkStore,
    files: PlanningFiles,
    supervisor: AgentSupervisorService,
    worktree_manager: WorktreeManager,
    sharestore: SharedFolderStore,
    share_provisioner: ShareProvisioner,
    settings: Settings,
    req: MonitorArtifactRunRequest,
    detail: PlanArtifactDetail,
    agent_slug: str,
    report: dict[str, str],
    report_seq: int,
) -> PlanArtifactDetail:
    """Keep existing single-agent runs readable and resumable."""
    manifest = actions.manifest_or_raise(files, req.work_slug)
    run = actions.find_run_by_id(
        actions.artifact_runs_for_update(manifest, req.artifact_id), req.run_id
    )
    if run is None:
        raise PlanArtifactRunNotFound(f"plan artifact run not found: {req.run_id}")
    if actions.run_status(run) != PlanRunStatus.RUNNING:
        return actions.detail_or_raise(files, req.work_slug, req.artifact_id)
    actions.apply_artifact_report(
        manifest,
        detail.artifact,
        _legacy_report_request(req, agent_slug, report),
        now=actions.now_iso(),
    )
    run = actions.find_run_by_id(
        actions.artifact_runs_for_update(manifest, req.artifact_id), req.run_id
    )
    if run is None:
        raise PlanArtifactRunNotFound(f"plan artifact run not found: {req.run_id}")
    loop = actions.dict_or_empty(run.get("loop"))
    loop["last_checked_seq"] = report_seq
    run["loop"] = loop
    _write_run(files, req, manifest)
    detail = actions.detail_or_raise(files, req.work_slug, req.artifact_id)
    if report["proposed_source"]:
        detail = actions.create_artifact_proposal(
            files,
            req.work_slug,
            req.artifact_id,
            f"Proposed source update from {agent_slug}",
            report["proposed_source"],
        )
    projected = _projected_run(detail, req.run_id)
    if projected is None or projected.loop_status != LoopStatus.NEEDS_AGENT:
        return detail
    if projected.loop_attempt >= PLANNING_ARTIFACT_LOOP_DEFINITION.retry_limit:
        manifest = actions.manifest_or_raise(files, req.work_slug)
        actions.mark_run_failed(
            manifest,
            artifact_id=req.artifact_id,
            run_id=req.run_id,
            reason="Loop retry limit reached before a complete report.",
            findings=projected.loop_latest_assessment,
        )
        files.write_manifest(req.work_slug, manifest)
        return actions.detail_or_raise(files, req.work_slug, req.artifact_id)
    await _loop_runtime.send_loop_prompt(
        workstore,
        supervisor,
        worktree_manager,
        sharestore,
        share_provisioner,
        settings,
        work_slug=req.work_slug,
        agent_slug=agent_slug,
        prompt=continuation_prompt(detail.artifact, loop),
    )
    manifest = actions.manifest_or_raise(files, req.work_slug)
    actions.mark_run_running(
        manifest,
        artifact_id=req.artifact_id,
        run_id=req.run_id,
        existing_loop=loop,
        last_checked_seq=report_seq,
    )
    files.write_manifest(req.work_slug, manifest)
    return actions.detail_or_raise(files, req.work_slug, req.artifact_id)


def _legacy_report_request(
    req: MonitorArtifactRunRequest,
    agent_slug: str,
    report: dict[str, str],
) -> SubmitPlanArtifactReportRequest:
    return SubmitPlanArtifactReportRequest(
        artifact_id=req.artifact_id,
        run_id=req.run_id,
        agent_slug=agent_slug,
        summary=report["summary"],
        divergences=report["divergences"],
        skipped_scope=report["skipped_scope"],
        blockers=report["blockers"],
        decisions=report["decisions"],
        changes=report["changes"],
        validation_evidence=report["validation_evidence"],
        report_source=LoopReportSource.TRANSCRIPT_MARKDOWN,
    )


def _projected_run(detail: PlanArtifactDetail, run_id: str) -> PlanArtifactRun | None:
    return next((run for run in detail.artifact.runs if run.id == run_id), None)


def _persist_current(
    repository: LoopRunRepository,
    files: PlanningFiles,
    req: MonitorArtifactRunRequest,
) -> None:
    """Project the current manifest run into durable generic loop storage."""
    manifest = actions.manifest_or_raise(files, req.work_slug)
    run = actions.find_run_by_id(
        actions.artifact_runs_for_update(manifest, req.artifact_id),
        req.run_id,
    )
    if run is None:
        raise PlanArtifactRunNotFound(f"plan artifact run not found: {req.run_id}")
    detail = actions.detail_or_raise(files, req.work_slug, req.artifact_id)
    _loop_persistence.persist_artifact_run(
        repository,
        work_slug=req.work_slug,
        artifact=detail.artifact,
        run=run,
    )


def _latest_seq(events: list[dict[str, Any]], default: int) -> int:
    seqs = [seq for event in events if isinstance((seq := event.get("seq")), int)]
    return max(seqs, default=default)


__all__ = [
    "AgentNotFound",
    "MonitorArtifactRunRequest",
    "PlanArtifactNotExecutable",
    "PlanArtifactNotFound",
    "PlanArtifactRunNotFound",
    "PlanningNotStarted",
    "WorkNotFound",
    "execute",
]
