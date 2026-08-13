"""Monitor and advance a modern multi-stage loop run."""

from __future__ import annotations

import asyncio
import shlex
from dataclasses import dataclass, field, replace
from datetime import UTC, datetime, timedelta
from functools import singledispatch
from typing import TYPE_CHECKING, Any
from uuid import uuid4

from src.domain.agents.launch import AgentLaunchRequest, launch_agent
from src.domain.agents.ports import AgentAdapterFactory
from src.domain.agents.turn_monitor import observe_turn
from src.domain.artifacts.models import PrArtifact
from src.domain.artifacts.pr_status import is_terminal_pr_status
from src.domain.connections import ConnectionStore
from src.domain.loop import actions, briefs, feedback, history, pr_lifecycle
from src.domain.loop import runtime as _loop_runtime
from src.domain.loop.agent_policy import resolve_stage_agent_config
from src.domain.loop.dtos import (
    AgentStage,
    ApprovalStage,
    CheckStage,
    LoopCheckRequest,
    LoopCheckResult,
    LoopContextKind,
    LoopFailureKind,
    LoopOutcome,
    LoopReviewGateMode,
    LoopRunStatus,
    LoopStageReport,
    LoopStatus,
    LoopStepStatus,
    PrStage,
    ReviewStage,
    TaskStage,
)
from src.domain.loop.models import LoopRunTarget
from src.domain.loop.ports import (
    LoopCheckRunner,
    LoopRunRepository,
    LoopRunStateStore,
)
from src.domain.loop.prompts import (
    build_follow_up_prompt,
    build_report_blocks,
    build_stage_prompt,
    prompt_for,
    stage_inactivity_recovery_prompt,
    stage_report_repair_prompt,
)
from src.domain.loop.reports import extract_latest_stage_report
from src.domain.loop.snapshots import definition_from_snapshot
from src.domain.loop.transitions import (
    LoopTransitionInvalid,
    stage_by_id,
    transition_destination,
)
from src.domain.sharedfolders.ports import SharedFolderStore, ShareProvisioner
from src.domain.workstore.ports import WorkStore
from src.domain.worktrees import WorktreeManager

if TYPE_CHECKING:
    from src.domain.loop.dtos import LoopDefinition, LoopStepDefinition
    from src.domain.supervisor import AgentSupervisorService


class AgentNotFound(ValueError):
    """The active stage agent does not belong to the Work."""


class WorkNotFound(ValueError):
    """The Work owning the loop run does not exist."""


_INACTIVITY_WARNING_SECONDS = 5 * 60
# Three recoveries, then the stage is stalled. The first is a plain nudge,
# which is all an agent that simply stopped needs. The rest interrupt the turn
# first: a wedged turn never reads its queue, so a nudge alone would sit behind
# it forever.
_MAX_RECOVERY_STRIKES = 3
_NUDGE_ONLY_STRIKE = 1
# An interrupt that never lands must not park the stage until its clock runs
# out; after this the nudge goes out regardless.
_INTERRUPT_GRACE_SECONDS = 60
# Asking to cancel is a local call; waiting on it is not worth stalling the
# monitor that enforces every other deadline.
_INTERRUPT_REQUEST_SECONDS = 10
_RUNTIME_RELEASE_STATUSES = {
    LoopStatus.ACCEPTED.value,
    LoopStatus.AWAITING_APPROVAL.value,
    LoopStatus.BLOCKED_USER.value,
    LoopStatus.FAILED.value,
}


def _recovery_strikes(stage_row: dict[str, Any]) -> int:
    """Return how many recoveries this stall episode has already spent.

    Preconditions: ``stage_row`` is a persisted stage snapshot.
    Postconditions: a row written before recovery counted strikes reports one
    if it consumed its single recovery, so a run mid-flight across that change
    escalates from where it left off instead of starting over.
    """
    strikes = stage_row.get("recovery_strikes")
    if isinstance(strikes, int) and not isinstance(strikes, bool):
        return max(0, strikes)
    legacy = actions.int_or_default(stage_row.get("recovered_attempt"), 0)
    attempt = actions.int_or_default(stage_row.get("attempt"), 1)
    return 1 if legacy and legacy == attempt else 0


def _stall_reason(stage: LoopStepDefinition, strike: int) -> str:
    """Say where the ladder is, so a watching person can judge whether to act.

    Preconditions: ``strike`` is the recovery about to be spent, counting from
    one. Postconditions: names what was tried and what is left.
    """
    left = max(0, _MAX_RECOVERY_STRIKES - strike)
    remaining = (
        f"{left} more {'attempt' if left == 1 else 'attempts'} before the stage is stalled"
        if left
        else "no attempts left"
    )
    action = (
        "nudged to continue"
        if strike <= _NUDGE_ONLY_STRIKE
        else "interrupted and nudged to continue"
    )
    return (
        f"No agent activity for 5 minutes. {stage.name} was {action} "
        f"(attempt {strike} of {_MAX_RECOVERY_STRIKES}) — {remaining}."
    )


def _interrupt_stamp(stage_row: dict[str, Any]) -> datetime | None:
    """Return when this stage's turn was interrupted, if one is pending."""
    return _stage_row_time(stage_row, "interrupted_at")


async def _interrupt_stalled_turn(
    supervisor: AgentSupervisorService, agent_slug: str
) -> bool:
    """End a wedged turn so the next prompt is read instead of queued.

    Preconditions: ``agent_slug`` is the stage's active agent.
    Postconditions: true when a live runtime was asked to cancel its turn.

    A runtime that is not registered has no turn to interrupt -- resuming it
    starts one -- so that case reports false and the caller nudges instead.
    """
    if not supervisor.is_registered(agent_slug) or supervisor.is_lazy_registered(agent_slug):
        return False
    try:
        # Bounded: `stop_turn` waits for the runtime to report ready, and a
        # runtime that never does would park this monitor past every timeout
        # it is supposed to enforce.
        await asyncio.wait_for(
            supervisor.stop_turn(agent_slug), timeout=_INTERRUPT_REQUEST_SECONDS
        )
    except Exception:
        # Interrupting is best effort: the nudge still goes out, and a runtime
        # too broken to cancel fails loudly on the send that follows.
        return False
    return True


# The agent doing work, as opposed to anything that merely happened around
# it. An allowlist, because the transcript also carries what the loop itself
# writes (a nudge, an interrupt) and what an interrupt provokes in reply
# (status changes, errors, a cancelled turn settling) -- read as progress,
# any of those reset the ladder that was escalating them.
_AGENT_PROGRESS_EVENT_TYPES = frozenset(
    {
        "message_delta",
        "message_complete",
        "thinking_delta",
        "thinking_complete",
        "tool_call",
        "tool_call_update",
        "tool_result",
        "plan_update",
    }
)


def _agent_moved_since_recovery(
    stage_row: dict[str, Any], events: list[dict[str, Any]]
) -> bool:
    """Whether the agent itself produced work after the last recovery.

    Preconditions: ``events`` are this agent's transcript entries in order.
    Postconditions: true only for agent output newer than the recovery stamp.

    A stalled agent still emits things that are not work -- the loop's own
    prompt, the interrupt that answers it, the status change that follows.
    Only output means it came back.
    """
    recovered_at = _stage_row_time(stage_row, "recovered_at")
    if recovered_at is None:
        return False
    return any(
        event.get("type") in _AGENT_PROGRESS_EVENT_TYPES
        and (timestamp := _event_time(event)) is not None
        and timestamp > recovered_at
        for event in events
    )


def _stage_row_time(stage_row: dict[str, Any], key: str) -> datetime | None:
    """Return one stage-row timestamp, normalised the way events are.

    A row is on-disk state an older build or a hand edit may have written
    without an offset; comparing a naive value against an aware ``now`` raises
    inside the poll loop, which would take the run's monitor down with it.
    """
    return _event_time({"ts": stage_row.get(key)})


def _event_time(event: dict[str, Any]) -> datetime | None:
    """Return one transcript event's timestamp, when it carries a usable one."""
    raw = event.get("ts") or event.get("created_at")
    if not isinstance(raw, str) or not raw:
        return None
    try:
        parsed = datetime.fromisoformat(raw.replace("Z", "+00:00"))
    except ValueError:
        return None
    return parsed.replace(tzinfo=UTC) if parsed.tzinfo is None else parsed.astimezone(UTC)


class LoopRunNotFound(ValueError):
    """The requested loop run target does not exist."""


@dataclass(frozen=True)
class MonitorLoopRunRequest:
    """Action input for monitoring one loop run."""

    work_slug: str
    run_id: str
    poll_interval_seconds: float = 0.5
    idle_timeout_seconds: float | None = None
    worker_id: str = field(default_factory=lambda: f"worker-{uuid4().hex}")


async def execute(
    workstore: WorkStore,
    store: LoopRunStateStore,
    supervisor: AgentSupervisorService,
    worktree_manager: WorktreeManager,
    connection_store: ConnectionStore,
    sharestore: SharedFolderStore,
    share_provisioner: ShareProvisioner,
    adapter_factory: AgentAdapterFactory,
    check_runner: LoopCheckRunner,
    loop_runs: LoopRunRepository,
    settings: Any,
    req: MonitorLoopRunRequest,
) -> LoopRunTarget:
    """Claim and monitor one persisted run until it reaches a pause state.

    Preconditions: Work, target, pinned definition, and active stage agent exist.
    Postconditions: every consumed report is saved before the next stage starts.
    """
    target, _run, loop = _load_active_run(workstore, store, req)
    run_key = actions.str_or_none(loop.get("loop_run_id"))
    leased = run_key is not None and loop_runs.get(req.work_slug, run_key) is not None
    while (
        leased
        and run_key is not None
        and not loop_runs.claim(req.work_slug, run_key, req.worker_id, _lease_expiry())
    ):
        await asyncio.sleep(req.poll_interval_seconds)
        target, run, _loop = _load_active_run(workstore, store, req)
        if actions.run_status(run) != LoopRunStatus.RUNNING:
            return target
    try:
        result = await _execute_claimed(
            workstore,
            store,
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
        result_loop = actions.dict_or_empty(result.run.get("loop"))
        if result_loop.get("status") in _RUNTIME_RELEASE_STATUSES:
            await _loop_runtime.release_run_agents(
                workstore,
                supervisor,
                work_slug=req.work_slug,
                run=result.run,
                loop=result_loop,
            )
        return result
    finally:
        if leased and run_key is not None:
            loop_runs.release(req.work_slug, run_key, req.worker_id)


async def _execute_claimed(
    workstore: WorkStore,
    store: LoopRunStateStore,
    supervisor: AgentSupervisorService,
    worktree_manager: WorktreeManager,
    connection_store: ConnectionStore,
    sharestore: SharedFolderStore,
    share_provisioner: ShareProvisioner,
    adapter_factory: AgentAdapterFactory,
    check_runner: LoopCheckRunner,
    loop_runs: LoopRunRepository,
    settings: Any,
    req: MonitorLoopRunRequest,
    lease_run_key: str | None,
) -> LoopRunTarget:
    """Advance one run until it pauses, fails, or awaits approval.

    Preconditions: Work, artifact, run, and active stage agent exist.
    Postconditions: every consumed report is persisted before the next stage is
    launched; invalid reports receive bounded automatic repair prompts.
    """
    idle_for = 0.0
    while True:
        target, run, loop = _load_active_run(workstore, store, req)
        if lease_run_key is not None and not loop_runs.claim(
            req.work_slug,
            lease_run_key,
            req.worker_id,
            _lease_expiry(),
        ):
            return target
        if actions.run_status(run) != LoopRunStatus.RUNNING:
            return target
        definition = definition_from_snapshot(loop.get("definition_snapshot"))
        if _react_to_terminal_pr(workstore, req.work_slug, run, loop):
            _write_run(store, target)
            if actions.run_status(run) != LoopRunStatus.RUNNING:
                return target
            continue
        if isinstance(loop.get("approval_decision"), dict):
            target = await _apply_approval_decision(
                workstore,
                store,
                supervisor,
                worktree_manager,
                connection_store,
                sharestore,
                share_provisioner,
                adapter_factory,
                check_runner,
                settings,
                req,
                target,
                run,
                loop,
                definition,
            )
            if actions.run_status(target.run) != LoopRunStatus.RUNNING:
                return target
            continue
        if isinstance(loop.get("pr_feedback_decision"), dict):
            target = await _apply_pr_feedback_decision(
                workstore,
                store,
                supervisor,
                worktree_manager,
                connection_store,
                sharestore,
                share_provisioner,
                adapter_factory,
                check_runner,
                settings,
                req,
                target,
                run,
                loop,
                definition,
            )
            if actions.run_status(target.run) != LoopRunStatus.RUNNING:
                return target
            continue
        if isinstance(loop.get("review_gate_decision"), dict):
            target = await _apply_review_gate_decision(
                workstore,
                store,
                supervisor,
                worktree_manager,
                connection_store,
                sharestore,
                share_provisioner,
                adapter_factory,
                check_runner,
                settings,
                req,
                target,
                run,
                loop,
                definition,
            )
            if actions.run_status(target.run) != LoopRunStatus.RUNNING:
                return target
            continue
        current_id = actions.str_or_empty(loop.get("current_stage_id"))
        current_stage = stage_by_id(definition, current_id)
        if isinstance(current_stage, CheckStage):
            current_row = _stage_row(loop, current_id)
            if current_row is None:
                raise LoopTransitionInvalid(f"loop stage state not found: {current_id}")
            target = await _run_check_stage(
                workstore,
                store,
                supervisor,
                worktree_manager,
                connection_store,
                sharestore,
                share_provisioner,
                adapter_factory,
                check_runner,
                settings,
                req,
                target,
                run,
                loop,
                definition,
                current_stage,
                current_row,
            )
            if actions.run_status(target.run) != LoopRunStatus.RUNNING:
                return target
            continue
        agent_slug = _active_agent_slug(run, loop)
        if workstore.get_work_slug_for_agent(agent_slug) != req.work_slug:
            raise AgentNotFound(f"agent not found on work: {agent_slug}")
        cursor = actions.int_or_default(loop.get("last_checked_seq"), 0)
        events = list(workstore.read_transcript_from_cursor(req.work_slug, agent_slug, cursor))
        observation = observe_turn(events, datetime.now(UTC))
        latest_seq = _latest_seq(events, cursor)
        current_row = _stage_row(loop, current_stage.step_id)
        brief = briefs.optional_brief_from_snapshot(target.run.get("brief"))
        approved_prefixes = briefs.resolved_approved_command_prefixes(
            definition,
            brief,
            current_stage,
        )
        if current_row is not None:
            learned_prefixes = tuple(actions.str_list(current_row.get("approved_command_prefixes")))
            approved_prefixes = tuple(dict.fromkeys((*approved_prefixes, *learned_prefixes)))
            if _capture_user_approved_prefixes(events, current_row):
                run["loop"] = loop
                _write_run(store, target)
        if current_row is not None and await _auto_approve_permissions(
            supervisor,
            agent_slug,
            events,
            approved_prefixes,
            current_row,
        ):
            run["loop"] = loop
            _write_run(store, target)
        runtime_error = observation.terminal_error
        if runtime_error is not None:
            current = actions.str_or_empty(loop.get("current_stage_id"))
            current_row = _stage_row(loop, current) if current else None
            if (
                current_stage is not None
                and current_row is not None
                and actions.claim_connection_recovery(current_row, runtime_error)
            ):
                try:
                    await _send_recovery_prompt(
                        workstore,
                        supervisor,
                        worktree_manager,
                        sharestore,
                        share_provisioner,
                        settings,
                        req,
                        target,
                        current_stage,
                        current_row,
                        agent_slug,
                        actions.str_or_empty(loop.get("previous_stage_id")),
                    )
                except Exception as exc:
                    current_row["status"] = LoopStepStatus.FAILED.value
                    reason = f"{current_stage.name} could not reconnect: {exc}"
                    _fail_run(
                        run,
                        loop,
                        reason,
                        [reason],
                        failure_kind=LoopFailureKind.PROVIDER_RUNTIME,
                    )
                    _write_run(store, target)
                    await supervisor.stop_agent(agent_slug)
                    return target
                loop["last_checked_seq"] = latest_seq
                loop["status"] = LoopStatus.RUNNING.value
                loop["status_reason"] = (
                    f"{current_stage.name} continued after the provider connection closed."
                )
                run["loop"] = loop
                _write_run(store, target)
                continue
            if current_row is not None:
                current_row["status"] = LoopStepStatus.FAILED.value
            loop["last_checked_seq"] = latest_seq
            reason = f"Agent runtime failed: {runtime_error}"
            _fail_run(
                run,
                loop,
                reason,
                [reason],
                failure_kind=LoopFailureKind.PROVIDER_RUNTIME,
            )
            _write_run(store, target)
            return target

        if current_stage is not None:
            current_row = _stage_row(loop, current_stage.step_id)
            if current_row is not None and actions.claim_stale_permission_recovery(
                current_row, events
            ):
                try:
                    await _send_recovery_prompt(
                        workstore,
                        supervisor,
                        worktree_manager,
                        sharestore,
                        share_provisioner,
                        settings,
                        req,
                        target,
                        current_stage,
                        current_row,
                        agent_slug,
                        actions.str_or_empty(loop.get("previous_stage_id")),
                    )
                except Exception as exc:
                    current_row["status"] = LoopStepStatus.FAILED.value
                    reason = f"{current_stage.name} could not reconnect: {exc}"
                    _fail_run(
                        run,
                        loop,
                        reason,
                        [reason],
                        failure_kind=LoopFailureKind.PROVIDER_RUNTIME,
                    )
                    _write_run(store, target)
                    await supervisor.stop_agent(agent_slug)
                    return target
                loop["last_checked_seq"] = latest_seq
                loop["status"] = LoopStatus.RUNNING.value
                loop["status_reason"] = (
                    f"{current_stage.name} continued after its permission request "
                    "expired during reconnect."
                )
                run["loop"] = loop
                _write_run(store, target)
                continue

            last_activity_at = observation.last_activity_at
            now = datetime.now(UTC)
            elapsed_seconds = observation.elapsed_seconds
            waiting_permission = observation.waiting_permission
            timeout_seconds = current_stage.retry.timeout_minutes * 60
            if elapsed_seconds is not None and elapsed_seconds >= timeout_seconds:
                current_row = _stage_row(loop, current_stage.step_id)
                if current_row is not None:
                    current_row["status"] = LoopStepStatus.FAILED.value
                reason = (
                    f"{current_stage.name} timed out after "
                    f"{current_stage.retry.timeout_minutes} minutes without completing."
                )
                loop["last_checked_seq"] = latest_seq
                _fail_run(
                    run,
                    loop,
                    reason,
                    [reason],
                    failure_kind=LoopFailureKind.TIMEOUT,
                )
                _write_run(store, target)
                await supervisor.stop_agent(agent_slug)
                return target

            # A tool call that is still running emits nothing while it works —
            # a test suite or a sub-agent exploration can be silent for far
            # longer than the warning window. Treating that as a stall
            # restarts a stage that was never stuck, throwing away its
            # progress and the work the tool was in the middle of.
            inactive = (
                not waiting_permission
                and not observation.tool_in_flight
                and last_activity_at is not None
                and (now - last_activity_at).total_seconds() >= _INACTIVITY_WARNING_SECONDS
            )
            if inactive:
                current_row = _stage_row(loop, current_stage.step_id)
                if current_row is not None and _agent_moved_since_recovery(
                    current_row, events
                ):
                    # It worked and stopped again: a fresh stall, not a
                    # continuation, so escalation starts from the top rather
                    # than counting a long healthy run against the stage. Any
                    # interrupt still pending belonged to the stall it broke.
                    actions.clear_stall_recovery(current_row)
                    _write_run(store, target)
                strikes = _recovery_strikes(current_row) if current_row else 0
                interrupted_at = _interrupt_stamp(current_row) if current_row else None
                # An interrupt is the first half of a strike, not a strike of
                # its own: it clears the queue so the nudge below is read. Once
                # the turn ends, this falls through to that nudge rather than
                # cancelling again.
                if current_row is not None and interrupted_at is not None:
                    if not observation.finished and (
                        (now - interrupted_at).total_seconds() < _INTERRUPT_GRACE_SECONDS
                    ):
                        run["loop"] = loop
                        _write_run(store, target)
                        if _idle_expired(req, idle_for):
                            return target
                        await asyncio.sleep(req.poll_interval_seconds)
                        idle_for += req.poll_interval_seconds
                        continue
                    current_row.pop("interrupted_at", None)
                elif current_row is not None and strikes >= _MAX_RECOVERY_STRIKES:
                    current_row["status"] = LoopStepStatus.FAILED.value
                    reason = (
                        f"{current_stage.name} stopped responding and did not "
                        f"restart after {_MAX_RECOVERY_STRIKES} attempts."
                    )
                    _fail_run(
                        run,
                        loop,
                        reason,
                        [reason],
                        failure_kind=LoopFailureKind.STALLED,
                    )
                    loop["last_checked_seq"] = latest_seq
                    _write_run(store, target)
                    await supervisor.stop_agent(agent_slug)
                    return target
                elif current_row is not None and strikes >= _NUDGE_ONLY_STRIKE:
                    # A plain nudge already failed, so the turn is wedged rather
                    # than merely finished early: end it before prompting again.
                    if await _interrupt_stalled_turn(supervisor, agent_slug):
                        current_row["interrupted_at"] = now.isoformat()
                        # Stays WAITING_REPORT while the ladder works: the run
                        # is not healthy, and reporting it as running hides the
                        # one window where a person can still step in.
                        loop["status"] = LoopStatus.WAITING_REPORT.value
                        loop["status_reason"] = _stall_reason(current_stage, strikes + 1)
                        run["loop"] = loop
                        _write_run(store, target)
                        if _idle_expired(req, idle_for):
                            return target
                        await asyncio.sleep(req.poll_interval_seconds)
                        idle_for += req.poll_interval_seconds
                        continue
                if current_row is not None:
                    try:
                        # Always resumes. A nudge exists to unstick the session
                        # that holds the stage's work; restarting it throws that
                        # away. A missing runtime resumes from the persisted
                        # session id just the same.
                        await _send_recovery_prompt(
                            workstore,
                            supervisor,
                            worktree_manager,
                            sharestore,
                            share_provisioner,
                            settings,
                            req,
                            target,
                            current_stage,
                            current_row,
                            agent_slug,
                            actions.str_or_empty(loop.get("previous_stage_id")),
                        )
                    except Exception as exc:
                        current_row["status"] = LoopStepStatus.FAILED.value
                        reason = f"{current_stage.name} could not reconnect: {exc}"
                        _fail_run(
                            run,
                            loop,
                            reason,
                            [reason],
                            failure_kind=LoopFailureKind.PROVIDER_RUNTIME,
                        )
                        _write_run(store, target)
                        await supervisor.stop_agent(agent_slug)
                        return target
                    current_row["recovery_strikes"] = strikes + 1
                    # Stamped after the send so the prompt's own transcript
                    # entry does not read as the agent moving.
                    current_row["recovered_at"] = datetime.now(UTC).isoformat()
                    current_row.pop("recovered_attempt", None)
                    # The nudge is deliberately bare -- the session already
                    # holds the report contract -- so the repair path is what
                    # restates it if the agent answers in prose. A stage that
                    # spent its repair budget before stalling would otherwise
                    # fail INVALID_REPORT on its first post-nudge word, having
                    # never been asked to correct itself after the stall.
                    current_row.pop("repair_attempt", None)
                    # Not RUNNING: the nudge is a symptom, not a recovery. The
                    # stage is only healthy again once the agent answers it,
                    # and the branch below flips it back when that happens.
                    # Reporting it as running here is what made a stall look
                    # exactly like a long stage until the ladder gave up.
                    loop["status"] = LoopStatus.WAITING_REPORT.value
                    loop["status_reason"] = _stall_reason(current_stage, strikes + 1)
                    run["loop"] = loop
                    _write_run(store, target)
                    continue
            elif loop.get("status") == LoopStatus.WAITING_REPORT.value:
                # Only the agent answering clears the warning. A nudge lands in
                # the same transcript, so the plain "activity resumed" test
                # cleared it on the very next poll and the stall stayed
                # invisible however long the ladder ran.
                settled_row = _stage_row(loop, current_stage.step_id)
                # No stamp means no recovery is pending -- including a run that
                # was parked in this status before the ladder existed, which
                # would otherwise never leave it.
                if (
                    settled_row is None
                    or not settled_row.get("recovered_at")
                    or _agent_moved_since_recovery(settled_row, events)
                ):
                    if settled_row is not None:
                        actions.clear_stall_recovery(settled_row)
                    loop["status"] = LoopStatus.RUNNING.value
                    loop["status_reason"] = f"{current_stage.name} is running."
                    run["loop"] = loop
                    _write_run(store, target)

        if not observation.finished:
            if _idle_expired(req, idle_for):
                return target
            await asyncio.sleep(req.poll_interval_seconds)
            idle_for += req.poll_interval_seconds
            continue

        extracted_stage = extract_latest_stage_report(events)
        if extracted_stage is None:
            # The turn ran to completion, so the agent answered whatever last
            # reached it. Its events leave the window when the cursor moves
            # past them, which would otherwise leave the stall ladder counting
            # strikes against an agent that demonstrably came back.
            if current_stage is not None and (
                settled_row := _stage_row(loop, current_stage.step_id)
            ) is not None:
                actions.clear_stall_recovery(settled_row)
                run["loop"] = loop
            _store_cursor_if_advanced(store, target, run, loop, latest_seq, cursor)
            if _idle_expired(req, idle_for):
                return target
            await asyncio.sleep(req.poll_interval_seconds)
            idle_for += req.poll_interval_seconds
            continue
        stage_report, report_seq = extracted_stage
        target = await _apply_stage_report(
            workstore,
            store,
            supervisor,
            worktree_manager,
            connection_store,
            sharestore,
            share_provisioner,
            adapter_factory,
            check_runner,
            settings,
            req,
            target,
            agent_slug,
            stage_report,
            report_seq,
        )

        idle_for = 0.0
        if actions.run_status(target.run) != LoopRunStatus.RUNNING:
            return target


def _load_active_run(
    workstore: WorkStore,
    store: LoopRunStateStore,
    req: MonitorLoopRunRequest,
) -> tuple[LoopRunTarget, dict[str, Any], dict[str, Any]]:
    if workstore.get_work(req.work_slug) is None:
        raise WorkNotFound(f"work not found: {req.work_slug}")
    target = store.load(req.work_slug, req.run_id)
    if target is None:
        raise LoopRunNotFound(f"loop run not found: {req.run_id}")
    return target, target.run, actions.dict_or_empty(target.run.get("loop"))


def _active_agent_slug(run: dict[str, Any], loop: dict[str, Any]) -> str:
    current = actions.str_or_empty(loop.get("current_stage_id"))
    stage = _stage_row(loop, current) if current else None
    return (
        actions.str_or_empty(stage.get("agent_slug"))
        if stage
        else actions.str_or_empty(run.get("agent_slug"))
    )


async def _auto_approve_permissions(
    supervisor: AgentSupervisorService,
    agent_slug: str,
    events: list[dict[str, Any]],
    approved_prefixes: tuple[str, ...],
    stage_row: dict[str, Any],
) -> bool:
    """Resolve configured command permissions observed by the loop monitor.

    Preconditions: events belong to the active stage agent and prefixes are the
    pinned effective policy. Postconditions: each matching pending request is
    allowed once; unmatched and non-command permissions remain user-controlled.
    """
    if not approved_prefixes or not supervisor.is_registered(agent_slug):
        return False
    decided = {
        request_id
        for event in events
        if event.get("type") == "permission_decision"
        and isinstance((request_id := event.get("request_id")), str)
    }
    handled = set(actions.str_list(stage_row.get("auto_approved_permission_ids")))
    changed = False
    for event in events:
        request_id = event.get("request_id")
        if (
            not isinstance(request_id, str)
            or request_id in handled
            or request_id in decided
            or not actions.permission_request_matches_approved_prefix(event, approved_prefixes)
        ):
            continue
        await supervisor.resolve_permission(agent_slug, request_id, "allow")
        handled.add(request_id)
        changed = True
    if changed:
        stage_row["auto_approved_permission_ids"] = sorted(handled)
    return changed


def _capture_user_approved_prefixes(
    events: list[dict[str, Any]],
    stage_row: dict[str, Any],
) -> bool:
    """Persist command prefixes explicitly approved for the active run stage.

    Preconditions: events belong to one stage occurrence. Postconditions:
    every safe ``allow_always`` command prefix is available to fresh sessions
    of this stage; existing configured prefixes are preserved.
    """
    requests = {
        request_id: event
        for event in events
        if event.get("type") == "permission_request"
        and isinstance((request_id := event.get("request_id")), str)
    }
    prefixes = actions.str_list(stage_row.get("approved_command_prefixes"))
    changed = False
    for event in events:
        request_id = event.get("request_id")
        if event.get("type") != "permission_decision" or event.get("decision") != "allow_always":
            continue
        request = requests.get(request_id) if isinstance(request_id, str) else None
        prefix = actions.approved_command_prefix_from_request(request or {})
        if prefix and prefix not in prefixes:
            prefixes.append(prefix)
            changed = True
    if changed:
        stage_row["approved_command_prefixes"] = prefixes
    return changed


async def _apply_stage_report(
    workstore: WorkStore,
    store: LoopRunStateStore,
    supervisor: AgentSupervisorService,
    worktree_manager: WorktreeManager,
    connection_store: ConnectionStore,
    sharestore: SharedFolderStore,
    share_provisioner: ShareProvisioner,
    adapter_factory: AgentAdapterFactory,
    check_runner: LoopCheckRunner,
    settings: Any,
    req: MonitorLoopRunRequest,
    target: LoopRunTarget,
    agent_slug: str,
    report: LoopStageReport | None,
    report_seq: int,
) -> LoopRunTarget:
    target, run, loop = _load_active_run(workstore, store, req)
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
            store,
            supervisor,
            worktree_manager,
            sharestore,
            share_provisioner,
            settings,
            req,
            target,
            run,
            loop,
            stage,
            stage_row,
            agent_slug,
            report_seq,
            problem,
        )
    assert report is not None
    if isinstance(stage, PrStage) and report.outcome == LoopOutcome.PASS:
        try:
            pr_lifecycle.capture_completion(
                workstore,
                target,
                stage_row,
                report.artifact_refs,
            )
        except pr_lifecycle.PrStageInvalid as exc:
            return await _repair_stage_report(
                workstore,
                store,
                supervisor,
                worktree_manager,
                sharestore,
                share_provisioner,
                settings,
                req,
                target,
                run,
                loop,
                stage,
                stage_row,
                agent_slug,
                report_seq,
                str(exc),
            )
    _record_stage_report(loop, stage_row, report, report_seq)
    feedback.close_attempt(loop, current_id)
    loop["last_checked_seq"] = report_seq
    loop["findings"] = list(report.findings)
    _copy_report_to_run(run, report)
    return await _advance_after_stage_report(
        workstore,
        store,
        supervisor,
        worktree_manager,
        connection_store,
        sharestore,
        share_provisioner,
        adapter_factory,
        check_runner,
        settings,
        req,
        target,
        run,
        loop,
        definition,
        current_id,
        stage_row,
        report,
    )


async def _advance_after_stage_report(
    workstore: WorkStore,
    store: LoopRunStateStore,
    supervisor: AgentSupervisorService,
    worktree_manager: WorktreeManager,
    connection_store: ConnectionStore,
    sharestore: SharedFolderStore,
    share_provisioner: ShareProvisioner,
    adapter_factory: AgentAdapterFactory,
    check_runner: LoopCheckRunner,
    settings: Any,
    req: MonitorLoopRunRequest,
    target: LoopRunTarget,
    run: dict[str, Any],
    loop: dict[str, Any],
    definition: LoopDefinition,
    current_id: str,
    stage_row: dict[str, Any],
    report: LoopStageReport,
    *,
    bypass_review_gate: bool = False,
    pass_already_started: bool = False,
    resolution_note: str = "",
    agent_reported: bool = True,
) -> LoopRunTarget:
    """Persist one stage outcome and enter its configured destination."""
    if agent_reported:
        report = _without_a_pr_send_back(stage_by_id(definition, current_id), report)
    if report.outcome == LoopOutcome.BLOCKED_USER:
        stage_row["status"] = LoopStepStatus.BLOCKED_USER.value
        run["status"] = LoopRunStatus.BLOCKED.value
        run["completed_at"] = actions.now_iso()
        loop["status"] = LoopStatus.BLOCKED_USER.value
        loop["status_reason"] = report.blocker
        run["loop"] = loop
        _write_run(store, target)
        return target

    if (
        not bypass_review_gate
        and report.outcome == LoopOutcome.CHANGES_REQUESTED
        and _hold_at_review_gate(store, target, run, loop, definition, stage_row, report)
    ):
        return target

    # A request is answered when the stage that would verify it passes -- not
    # when the run is accepted. Anything else deadlocks: PR feedback used to
    # clear only once the PR stage passed, and the PR stage would not pass
    # while the feedback was open.
    if report.outcome == LoopOutcome.PASS:
        feedback.answer(loop, current_id)

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
        _write_run(store, target)
        return target
    if destination == "fail":
        _fail_run(run, loop, report.summary, list(report.findings))
        _write_run(store, target)
        return target
    if destination == "complete":
        if isinstance(stage_by_id(definition, current_id), PrStage):
            _complete_pr(run, loop, current_id)
        else:
            _await_approval(run, loop, current_id=None)
        _write_run(store, target)
        return target

    next_stage = stage_by_id(definition, destination)
    next_row = _stage_row(loop, next_stage.step_id)
    if next_row is None:
        raise LoopTransitionInvalid(f"loop stage state not found: {destination}")
    stage_ids = [stage.step_id for stage in definition.stages]
    if not pass_already_started and stage_ids.index(destination) <= stage_ids.index(current_id):
        actions.start_next_pass(loop)
    if isinstance(next_stage, ApprovalStage):
        next_row["status"] = LoopStepStatus.PENDING.value
        approval_pass = next_stage.transitions.get(LoopOutcome.PASS)
        _await_approval(
            run,
            loop,
            current_id=next_stage.step_id,
            continues=bool(approval_pass) and approval_pass != "complete",
        )
        _write_run(store, target)
        return target
    if isinstance(next_stage, CheckStage):
        return await _run_check_stage(
            workstore,
            store,
            supervisor,
            worktree_manager,
            connection_store,
            sharestore,
            share_provisioner,
            adapter_factory,
            check_runner,
            settings,
            req,
            target,
            run,
            loop,
            definition,
            next_stage,
            next_row,
        )

    if not isinstance(next_stage, AgentStage):
        raise LoopTransitionInvalid(f"loop stage cannot run an agent: {next_stage.step_id}")
    next_agent, session_continues = await _launch_or_resume_stage_agent(
        workstore,
        supervisor,
        worktree_manager,
        connection_store,
        sharestore,
        share_provisioner,
        adapter_factory,
        req,
        target,
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
        target,
        next_stage,
        next_row,
        next_agent,
        current_id,
        resolution_note=resolution_note,
        session_continues=session_continues,
    )
    next_row["status"] = LoopStepStatus.RUNNING.value
    next_row["attempt"] = actions.int_or_default(next_row.get("attempt"), 0) + 1
    next_row.pop("repair_attempt", None)
    next_row["agent_slug"] = next_agent
    _record_owned_agent(loop, next_agent)
    if next_stage.supplies_source_agent:
        loop["source_agent_slug"] = next_agent
    loop["current_stage_id"] = next_stage.step_id
    # What `previous` resolves to. A retry builds its prompt long after this
    # transition and cannot re-derive it from stage order, because the stage
    # that reported into this one is not always the one before it.
    loop["previous_stage_id"] = current_id
    loop["last_checked_seq"] = cursor
    loop["status"] = LoopStatus.RUNNING.value
    loop["status_reason"] = f"{next_stage.name} is running."
    run["status"] = LoopRunStatus.RUNNING.value
    run["completed_at"] = None
    run["loop"] = loop
    _write_run(store, target)
    return target


async def _run_check_stage(
    workstore: WorkStore,
    store: LoopRunStateStore,
    supervisor: AgentSupervisorService,
    worktree_manager: WorktreeManager,
    connection_store: ConnectionStore,
    sharestore: SharedFolderStore,
    share_provisioner: ShareProvisioner,
    adapter_factory: AgentAdapterFactory,
    check_runner: LoopCheckRunner,
    settings: Any,
    req: MonitorLoopRunRequest,
    target: LoopRunTarget,
    run: dict[str, Any],
    loop: dict[str, Any],
    definition: LoopDefinition,
    stage: CheckStage,
    stage_row: dict[str, Any],
) -> LoopRunTarget:
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
    workdir = worktree_manager.ensure(
        req.work_slug,
        source.worktree_slug or source_slug,
        source.folder,
    )
    stage_row["status"] = LoopStepStatus.RUNNING.value
    stage_row["attempt"] = actions.int_or_default(stage_row.get("attempt"), 0) + 1
    stage_row.pop("repair_attempt", None)
    loop["current_stage_id"] = stage.step_id
    loop["status"] = LoopStatus.RUNNING.value
    loop["status_reason"] = f"{stage.name} is running."
    run["loop"] = loop
    _write_run(store, target)
    try:
        workspace = worktree_manager.describe_state(workdir)
        result = await check_runner.run(
            LoopCheckRequest(
                workdir=workdir,
                argv=stage.check_command,
                timeout_seconds=float(stage.retry.timeout_minutes * 60),
                changed_files=(
                    *workspace.changed_files,
                    *workspace.untracked_files,
                ),
            )
        )
    except (OSError, ValueError) as exc:
        result = LoopCheckResult(exit_code=None, stderr=str(exc))
    report = _check_report(stage, result)
    _record_stage_report(
        loop,
        stage_row,
        report,
        actions.int_or_default(loop.get("last_checked_seq"), 0),
    )
    loop["findings"] = list(report.findings)
    _copy_report_to_run(run, report)
    return await _advance_after_stage_report(
        workstore,
        store,
        supervisor,
        worktree_manager,
        connection_store,
        sharestore,
        share_provisioner,
        adapter_factory,
        check_runner,
        settings,
        req,
        target,
        run,
        loop,
        definition,
        stage.step_id,
        stage_row,
        report,
    )


def _check_report(
    stage: CheckStage,
    result: LoopCheckResult,
) -> LoopStageReport:
    """Convert one process result into the common loop report contract."""
    passed = result.exit_code == 0 and not result.timed_out
    can_request_changes = bool(stage.transitions.get(LoopOutcome.CHANGES_REQUESTED))
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


@singledispatch
def _report_shape_problem(stage: LoopStepDefinition, report: LoopStageReport) -> str:
    """Return why a report does not fit the shape its stage owes, or "".

    Dispatches on the stage type. These were four `stage.kind` branches keyed
    through a `report_contract` field that never held anything but the value
    its kind implied.
    """
    return ""


@_report_shape_problem.register
def _task_report_problem(stage: TaskStage, report: LoopStageReport) -> str:
    if report.outcome == LoopOutcome.CHANGES_REQUESTED:
        return "Implementation stages cannot request changes from themselves."
    return _needs_evidence_on_pass(report)


@_report_shape_problem.register
def _review_report_problem(stage: ReviewStage, report: LoopStageReport) -> str:
    if report.outcome == LoopOutcome.CHANGES_REQUESTED and not report.findings:
        return "A changes-requested review needs actionable findings."
    return ""


@_report_shape_problem.register
def _pr_report_problem(stage: PrStage, report: LoopStageReport) -> str:
    if report.outcome == LoopOutcome.CHANGES_REQUESTED:
        if not report.findings:
            return "Create PR changes_requested needs actionable findings."
        if not report.validation_evidence.strip():
            return "Create PR changes_requested needs failing-check evidence."
    return _needs_evidence_on_pass(report)


def _needs_evidence_on_pass(report: LoopStageReport) -> str:
    """A stage that writes must show what proved the change works."""
    if report.outcome == LoopOutcome.PASS and not report.validation_evidence.strip():
        return "A passing implementation report needs validation evidence."
    return ""


def _stage_report_problem(stage: LoopStepDefinition, report: LoopStageReport | None) -> str:
    if report is None:
        return "Missing or invalid atelier_loop_step_report."
    if report.outcome == LoopOutcome.BLOCKED_USER and _explicit_none(report.blocker):
        return "A blocked_user report needs a concrete blocker."
    return _report_shape_problem(stage, report)


async def _repair_stage_report(
    workstore: WorkStore,
    store: LoopRunStateStore,
    supervisor: AgentSupervisorService,
    worktree_manager: WorktreeManager,
    sharestore: SharedFolderStore,
    share_provisioner: ShareProvisioner,
    settings: Any,
    req: MonitorLoopRunRequest,
    target: LoopRunTarget,
    run: dict[str, Any],
    loop: dict[str, Any],
    stage: LoopStepDefinition,
    stage_row: dict[str, Any],
    agent_slug: str,
    report_seq: int,
    problem: str,
) -> LoopRunTarget:
    # Its own budget, not `attempt`. `attempt` counts every launch of the
    # stage — the normal advance, a resume, and each retry a person asks for
    # — so sharing it meant a stage retried a few times arrived here with the
    # budget already spent and failed on its first malformed report without
    # ever being asked to repair it. This counter resets whenever the stage
    # launches, so the allowance is "repairs within one attempt".
    attempts = actions.int_or_default(stage_row.get("repair_attempt"), 0)
    if attempts >= stage.retry.max_attempts:
        stage_row["status"] = LoopStepStatus.FAILED.value
        _fail_run(
            run,
            loop,
            problem,
            [problem],
            failure_kind=LoopFailureKind.INVALID_REPORT,
        )
        _write_run(store, target)
        return target
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
    stage_row["repair_attempt"] = attempts + 1
    loop["attempt"] = actions.int_or_default(loop.get("attempt"), 1) + 1
    loop["last_checked_seq"] = report_seq
    loop["status_reason"] = problem
    run["loop"] = loop
    _write_run(store, target)
    return target


async def _launch_or_resume_stage_agent(
    workstore: WorkStore,
    supervisor: AgentSupervisorService,
    worktree_manager: WorktreeManager,
    connection_store: ConnectionStore,
    sharestore: SharedFolderStore,
    share_provisioner: ShareProvisioner,
    adapter_factory: AgentAdapterFactory,
    req: MonitorLoopRunRequest,
    target: LoopRunTarget,
    definition: LoopDefinition,
    stage: AgentStage,
    stage_row: dict[str, Any],
    loop: dict[str, Any],
) -> tuple[str, bool]:
    """Return the stage's agent and whether it keeps the session it already had.

    The caller needs both: an agent whose session continues has the seed in its
    transcript and must not be sent another one.
    """
    existing = actions.str_or_none(stage_row.get("agent_slug"))
    if stage.agent.session.value == "reuse" and existing:
        return existing, True

    agents = {
        agent.slug: agent
        for agent in workstore.list_agents_for_work(req.work_slug)
        if agent.slug is not None
    }
    source_slug = _write_stage_agent_slug(definition, loop)
    source = agents.get(source_slug)
    if source is None:
        raise AgentNotFound(f"source agent not found on work: {source_slug}")
    brief = briefs.optional_brief_from_snapshot(target.run.get("brief"))
    stage_input = briefs.stage_brief(brief, stage.step_id)
    provider, model, options = resolve_stage_agent_config(
        stage.agent,
        parent_provider=source.provider,
        parent_model=source.model,
        parent_options=dict(source.options or {}),
        override=stage_input.agent if stage_input is not None else None,
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
            name=f"{stage.name} · {target.target_id}",
            persona=stage.persona,
            role=stage.instructions,
            provider=provider,
            model=model,
            folder=source.folder,
            options=options,
            worktree_slug=source.worktree_slug or source_slug,
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
        raise RuntimeError("loop stage launch returned an agent without slug")
    return launched.slug, False


def _write_stage_agent_slug(definition: LoopDefinition, loop: dict[str, Any]) -> str:
    source = actions.str_or_none(loop.get("source_agent_slug"))
    if source:
        return source
    for stage in reversed(definition.stages):
        if not stage.supplies_source_agent:
            continue
        row = _stage_row(loop, stage.step_id)
        slug = actions.str_or_none(row.get("agent_slug")) if row else None
        if slug:
            return slug
    return ""


async def _send_recovery_prompt(
    workstore: WorkStore,
    supervisor: AgentSupervisorService,
    worktree_manager: WorktreeManager,
    sharestore: SharedFolderStore,
    share_provisioner: ShareProvisioner,
    settings: Any,
    req: MonitorLoopRunRequest,
    target: LoopRunTarget,
    stage: LoopStepDefinition,
    stage_row: dict[str, Any],
    agent_slug: str,
    previous_stage_id: str,
) -> None:
    """Restart a stalled or disconnected stage in whatever session it has left.

    Preconditions: the stage is the run's current one and its agent belongs to
    the Work. Postconditions: the agent has been prompted to continue.

    A bare nudge only works while the session that ran the stage is still there
    to resume. When the agent never persisted one, resuming starts an empty
    session instead, and "continue" would be the only thing in it -- so that
    case gets the full seed rather than a word.
    """
    if _loop_runtime.holds_a_session(
        workstore, work_slug=req.work_slug, agent_slug=agent_slug
    ):
        await _loop_runtime.send_loop_prompt(
            workstore,
            supervisor,
            worktree_manager,
            sharestore,
            share_provisioner,
            settings,
            work_slug=req.work_slug,
            agent_slug=agent_slug,
            prompt=stage_inactivity_recovery_prompt(),
        )
        return
    await _send_stage_prompt(
        workstore,
        supervisor,
        worktree_manager,
        sharestore,
        share_provisioner,
        settings,
        req,
        target,
        stage,
        stage_row,
        agent_slug,
        previous_stage_id,
    )


async def _send_stage_prompt(
    workstore: WorkStore,
    supervisor: AgentSupervisorService,
    worktree_manager: WorktreeManager,
    sharestore: SharedFolderStore,
    share_provisioner: ShareProvisioner,
    settings: Any,
    req: MonitorLoopRunRequest,
    target: LoopRunTarget,
    stage: LoopStepDefinition,
    stage_row: dict[str, Any],
    agent_slug: str,
    previous_stage_id: str,
    *,
    resolution_note: str = "",
    session_continues: bool = False,
) -> None:
    prompt_type = prompt_for(stage)
    if isinstance(stage, PrStage):
        pr_lifecycle.snapshot_stage_config(target, stage)
        resolution_note = "\n\n".join(
            value
            for value in (resolution_note.strip(), pr_lifecycle.prompt_context(target.run))
            if value
        )
    # Only what the stage declares is rendered. A stage that wants the account
    # of the changes-requested report that sent it here declares that report,
    # and one that acts on the user's requests declares `feedback`; there is
    # nothing left to inject on any stage's behalf.
    resolution_note = "\n\n".join(
        value
        for value in (_declared_feedback(stage, target.run), resolution_note.strip())
        if value
    )
    brief = briefs.optional_brief_from_snapshot(target.run.get("brief"))
    brief_note, brief_context = briefs.prompt_values(brief, stage.step_id)
    loop = actions.dict_or_empty(target.run.get("loop"))
    report_rows = actions.declared_report_rows(loop, stage, previous_stage_id)
    changed_files = next(
        (
            lines
            for _, row in report_rows
            if (lines := actions.changed_file_prompt_lines(row.get("changed_files")))
        ),
        actions.latest_changed_file_prompt_lines(loop),
    )
    workspace_diff = (
        _loop_runtime.workspace_prompt_context(
            workstore,
            worktree_manager,
            work_slug=req.work_slug,
            agent_slug=agent_slug,
        )
        if any(item.kind == LoopContextKind.WORKSPACE_DIFF for item in stage.inputs)
        else ""
    )
    prompt_input = prompt_type(
        run_id=req.run_id,
        work_slug=req.work_slug,
        artifact_id=target.target_id,
        artifact_title=target.title,
        source_ref=target.source_ref,
        stage=stage,
        reports=build_report_blocks(report_rows),
        history=history.lines(loop, stage.history, frozenset(step for step, _ in report_rows)),
        previous_changed_files=changed_files,
        workspace_diff=workspace_diff,
        resolution_note=resolution_note,
        resolved_context=tuple(actions.str_list(stage_row.get("resolved_context"))),
        context_warnings=(
            *actions.str_list(stage_row.get("context_warnings")),
            *actions.unresolved_report_warnings(loop, stage, previous_stage_id),
        ),
        brief_note=brief_note,
        brief_context=brief_context,
        waived_findings=_declared_waived(stage, loop),
    )
    # A `reuse` stage re-entered on a later pass keeps the session that ran it
    # last time, so the seed is already in its transcript. Sending another is
    # the same re-seed this form exists to avoid.
    prompt = (
        build_follow_up_prompt(prompt_input)
        if session_continues
        else build_stage_prompt(prompt_input)
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


def _narrow_to_enforced(
    loop: dict[str, Any],
    stage_row: dict[str, Any],
    findings: list[str],
    enforced: set[int],
) -> None:
    """Reduce the run's live account of a review to the enforced findings.

    ``findings`` and ``finding_details`` are written in parallel and read in
    parallel -- the run view counts one and renders the other, and the gate's
    stored indexes point into both -- so narrowing one without the other makes
    them disagree. ``loop["findings"]`` is narrowed for the same reason: the
    approval paths build their report from it, and would otherwise hand back
    the very findings the user just waived.

    Preconditions: ``enforced`` holds valid indexes into ``findings``.
    Postconditions: every live copy carries the same enforced subset; the
    stage's report ledger is untouched and keeps what the reviewer said.
    """
    kept = sorted(enforced)
    stage_row["findings"] = [findings[index] for index in kept]
    details = stage_row.get("finding_details")
    if isinstance(details, list):
        stage_row["finding_details"] = [
            details[index] for index in kept if 0 <= index < len(details)
        ]
    loop["findings"] = list(stage_row["findings"])


def _declared_feedback(stage: LoopStepDefinition, run: dict[str, Any]) -> str:
    """Return the outstanding requests, when the stage declares that input.

    A publishing stage does not declare it. Handing one the open block made it
    report changes_requested, which reopened the pass that produced the request
    -- the run cycled and pushed every pass.
    """
    if not any(item.kind == LoopContextKind.FEEDBACK for item in stage.inputs):
        return ""
    return feedback.context(run)


def _declared_waived(stage: LoopStepDefinition, loop: dict[str, Any]) -> tuple[str, ...]:
    """Return the dismissed findings, when the stage declares that input."""
    if not any(item.kind == LoopContextKind.WAIVED_FINDINGS for item in stage.inputs):
        return ()
    return tuple(feedback.dismissed(loop))


def _hold_at_review_gate(
    store: LoopRunStateStore,
    target: LoopRunTarget,
    run: dict[str, Any],
    loop: dict[str, Any],
    definition: LoopDefinition,
    stage_row: dict[str, Any],
    report: LoopStageReport,
) -> bool:
    """Pause a review return edge when its resolved gate requires a human.

    Preconditions: a validated changes-requested report is being advanced.
    Postconditions: returns false for automatic routing with budget; otherwise
    persists a blocked run containing the complete human decision input.
    """
    stage = stage_by_id(definition, actions.str_or_empty(stage_row.get("id")))
    brief = briefs.optional_brief_from_snapshot(target.run.get("brief"))
    gate, source = briefs.resolved_review_gate(stage, brief)
    if gate is None:
        return False
    pass_counts = loop.setdefault("review_gate_passes", {})
    pass_limits = loop.setdefault("review_gate_limits", {})
    used = actions.int_or_default(
        pass_counts.get(stage.step_id) if isinstance(pass_counts, dict) else None,
        0,
    )
    limit = actions.int_or_default(
        pass_limits.get(stage.step_id) if isinstance(pass_limits, dict) else None,
        gate.max_passes,
    )
    if gate.mode == LoopReviewGateMode.AUTOMATIC and used < limit:
        if isinstance(pass_counts, dict):
            pass_counts[stage.step_id] = used + 1
        return False

    destination = transition_destination(
        definition,
        stage.step_id,
        LoopOutcome.CHANGES_REQUESTED,
    )
    stage_row["status"] = LoopStepStatus.CHANGES_REQUESTED.value
    loop["review_gate"] = {
        "stage_id": stage.step_id,
        "destination_id": destination,
        "mode": gate.mode.value,
        "source": source,
        "max_passes": limit,
        "passes_used": used,
        "passes_spent": gate.mode == LoopReviewGateMode.AUTOMATIC,
        "summary": report.summary,
        "findings": list(report.findings),
        "finding_details": [
            {
                "text": finding.text,
                "severity": finding.severity.value,
                "location": finding.location,
            }
            for finding in report.finding_details
        ],
    }
    run["status"] = LoopRunStatus.BLOCKED.value
    run["completed_at"] = actions.now_iso()
    loop["status"] = LoopStatus.BLOCKED_USER.value
    loop["status_reason"] = f"{stage.name} requested changes; the review gate is waiting for you."
    run["loop"] = loop
    _write_run(store, target)
    return True


async def _apply_approval_decision(
    workstore: WorkStore,
    store: LoopRunStateStore,
    supervisor: AgentSupervisorService,
    worktree_manager: WorktreeManager,
    connection_store: ConnectionStore,
    sharestore: SharedFolderStore,
    share_provisioner: ShareProvisioner,
    adapter_factory: AgentAdapterFactory,
    check_runner: LoopCheckRunner,
    settings: Any,
    req: MonitorLoopRunRequest,
    target: LoopRunTarget,
    run: dict[str, Any],
    loop: dict[str, Any],
    definition: LoopDefinition,
) -> LoopRunTarget:
    """Advance one persisted approval through the configured pass edge.

    Preconditions: lifecycle.accept stored an approval decision on a user stage.
    Postconditions: the decision is recorded once and normal stage routing resumes.
    """
    decision = actions.dict_or_empty(loop.pop("approval_decision", None))
    stage_id = actions.str_or_empty(loop.get("current_stage_id"))
    stage_row = _stage_row(loop, stage_id)
    if stage_row is None:
        raise LoopTransitionInvalid(f"loop stage state not found: {stage_id}")
    prior_summary = actions.str_or_empty(run.get("summary"))
    approval_summary = actions.str_or_empty(decision.get("summary")) or "Approved by user."
    report = LoopStageReport(
        outcome=LoopOutcome.PASS,
        summary=(f"{approval_summary}\n\n{prior_summary}" if prior_summary else approval_summary),
        findings=tuple(actions.str_list(loop.get("findings"))),
        changes=actions.str_or_empty(run.get("changes")),
        validation_evidence=actions.str_or_empty(run.get("validation_evidence")),
        divergences=actions.str_or_empty(run.get("divergences")),
        skipped_scope=actions.str_or_empty(run.get("skipped_scope")),
    )
    _record_stage_report(
        loop,
        stage_row,
        report,
        actions.int_or_default(loop.get("last_checked_seq"), 0),
    )
    return await _advance_after_stage_report(
        workstore,
        store,
        supervisor,
        worktree_manager,
        connection_store,
        sharestore,
        share_provisioner,
        adapter_factory,
        check_runner,
        settings,
        req,
        target,
        run,
        loop,
        definition,
        stage_id,
        stage_row,
        report,
        bypass_review_gate=True,
    )


async def _apply_pr_feedback_decision(
    workstore: WorkStore,
    store: LoopRunStateStore,
    supervisor: AgentSupervisorService,
    worktree_manager: WorktreeManager,
    connection_store: ConnectionStore,
    sharestore: SharedFolderStore,
    share_provisioner: ShareProvisioner,
    adapter_factory: AgentAdapterFactory,
    check_runner: LoopCheckRunner,
    settings: Any,
    req: MonitorLoopRunRequest,
    target: LoopRunTarget,
    run: dict[str, Any],
    loop: dict[str, Any],
    definition: LoopDefinition,
) -> LoopRunTarget:
    """Route one persisted PR feedback bundle back through implementation.

    Preconditions: the PR stage has a changes-requested edge and a non-empty
    feedback bundle. Postconditions: the decision is consumed once and the next
    pass starts through normal fresh-session stage routing.
    """
    decision = actions.dict_or_empty(loop.pop("pr_feedback_decision", None))
    stage_id = actions.str_or_empty(decision.get("stage_id"))
    stage_row = _stage_row(loop, stage_id)
    if stage_row is None:
        raise LoopTransitionInvalid(f"loop stage state not found: {stage_id}")
    summary = actions.str_or_empty(decision.get("summary"))
    # Summary only. Putting the same note in `findings` rendered it twice --
    # once as `Summary:` and again as a single `Findings:` bullet -- and a
    # finding is meant to be a discrete review point, not the whole note.
    report = LoopStageReport(
        outcome=LoopOutcome.CHANGES_REQUESTED,
        summary=summary,
    )
    # Recorded like the approval and review-gate decisions are. Routing it
    # without recording left the pass that the user's feedback opened with no
    # occurrence at all: absent from the stage's ledger, absent from the run
    # view, and absent from the history every later stage reads -- so the run's
    # account skipped the event that caused the pass.
    _record_stage_report(
        loop,
        stage_row,
        report,
        actions.int_or_default(loop.get("last_checked_seq"), 0),
    )
    # This occurrence is a routing decision, not a push. `_record_stage_report`
    # snapshots the row's push fields onto every occurrence, and the row still
    # carries the last successful push -- which the run view would render as
    # though this decision had pushed and answered those comments. The row
    # keeps them: `prepare_feedback` reads `push_at` to tell which comments are
    # newer than the last push.
    _disown_push(stage_row)
    # The user chose to send this PR feedback back for implementation, so the
    # stage's return edge is being used the way it was built to be used.
    return await _advance_after_stage_report(
        workstore,
        store,
        supervisor,
        worktree_manager,
        connection_store,
        sharestore,
        share_provisioner,
        adapter_factory,
        check_runner,
        settings,
        req,
        target,
        run,
        loop,
        definition,
        stage_id,
        stage_row,
        report,
        bypass_review_gate=True,
        agent_reported=False,
        pass_already_started=True,
        # No resolution note: `_send_stage_prompt` renders the open feedback
        # record for every stage in the pass, so passing it here would only add
        # a second copy of a block that grows with each selected comment.
    )


async def _apply_review_gate_decision(
    workstore: WorkStore,
    store: LoopRunStateStore,
    supervisor: AgentSupervisorService,
    worktree_manager: WorktreeManager,
    connection_store: ConnectionStore,
    sharestore: SharedFolderStore,
    share_provisioner: ShareProvisioner,
    adapter_factory: AgentAdapterFactory,
    check_runner: LoopCheckRunner,
    settings: Any,
    req: MonitorLoopRunRequest,
    target: LoopRunTarget,
    run: dict[str, Any],
    loop: dict[str, Any],
    definition: LoopDefinition,
) -> LoopRunTarget:
    """Apply one persisted human gate decision through normal stage routing.

    Preconditions: the run contains a gate and a validated pending decision.
    Postconditions: waived findings are recorded and routing continues once.
    """
    gate = actions.dict_or_empty(loop.get("review_gate"))
    decision = actions.dict_or_empty(loop.get("review_gate_decision"))
    stage_id = actions.str_or_empty(gate.get("stage_id"))
    stage_row = _stage_row(loop, stage_id)
    if stage_row is None:
        raise LoopTransitionInvalid(f"loop stage state not found: {stage_id}")
    findings = actions.str_list(gate.get("findings"))
    enforced = {
        index
        for index in decision.get("enforced_findings", [])
        if isinstance(index, int) and not isinstance(index, bool) and 0 <= index < len(findings)
    }
    choice = actions.str_or_empty(decision.get("decision"))
    if choice == "approve_as_is":
        enforced.clear()
    # Approving as-is dismisses everything the review reported, not only the
    # findings a partial send-back left out: the user has seen all of them and
    # chosen to let them stand, so a later reviewer is told not to raise any.
    feedback.waive(
        loop, [finding for index, finding in enumerate(findings) if index not in enforced]
    )
    reports = stage_row.get("reports")
    if isinstance(reports, list) and reports and isinstance(reports[-1], dict):
        reports[-1]["review_decision"] = {
            "decision": choice,
            "enforced_findings": sorted(enforced),
            "instruction": actions.str_or_empty(decision.get("instruction")),
        }
    # Only on a send-back, and only to what the user enforced: that is what the
    # stage this returns to reads as its report, and a waived finding must not
    # travel back as though the user had asked for it. The ledger entry above
    # keeps the full record either way. Approving as-is narrows nothing --
    # erasing the account would leave the run view showing a review that found
    # nothing, when what happened is that the user let its findings stand.
    if choice == "send_back":
        _narrow_to_enforced(loop, stage_row, findings, enforced)
    if choice == "send_back" and bool(gate.get("passes_spent")):
        next_limit = actions.int_or_default(gate.get("passes_used"), 0) + 1
        pass_counts = loop.setdefault("review_gate_passes", {})
        pass_limits = loop.setdefault("review_gate_limits", {})
        if isinstance(pass_counts, dict):
            pass_counts[stage_id] = next_limit
        if isinstance(pass_limits, dict):
            pass_limits[stage_id] = next_limit
    loop.pop("review_gate", None)
    loop.pop("review_gate_decision", None)
    instruction = actions.str_or_empty(decision.get("instruction"))
    # A send-back note is a request for changes like any other: it has to
    # outlive the one prompt that carried it, or a retry of the stage it was
    # sent to relaunches the agent with the findings but not the reason. Stored
    # rather than passed so the shared renderer renders it exactly once, and
    # answered by this review -- the stage that will verify the answer.
    durable_instruction = bool(instruction) and choice == "send_back"
    if durable_instruction:
        feedback.record(
            loop,
            source="review",
            note=instruction,
            pass_number=max(1, actions.int_or_default(loop.get("pass_number"), 1)),
            answered_by=stage_id,
        )
    report = LoopStageReport(
        outcome=(LoopOutcome.PASS if choice == "approve_as_is" else LoopOutcome.CHANGES_REQUESTED),
        summary=actions.str_or_empty(gate.get("summary")),
        findings=tuple(findings[index] for index in sorted(enforced)),
    )
    return await _advance_after_stage_report(
        workstore,
        store,
        supervisor,
        worktree_manager,
        connection_store,
        sharestore,
        share_provisioner,
        adapter_factory,
        check_runner,
        settings,
        req,
        target,
        run,
        loop,
        definition,
        stage_id,
        stage_row,
        report,
        bypass_review_gate=True,
        resolution_note="" if durable_instruction else instruction,
    )


def _record_stage_report(
    loop: dict[str, Any],
    stage_row: dict[str, Any],
    report: LoopStageReport,
    report_seq: int,
) -> None:
    reports = stage_row.setdefault("reports", [])
    pass_number = max(1, actions.int_or_default(loop.get("pass_number"), 1))
    value = {
        "outcome": report.outcome.value,
        "pass_number": pass_number,
        "agent_slug": actions.str_or_none(stage_row.get("agent_slug")),
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
        "push_at": actions.str_or_none(stage_row.get("push_at")),
        # No PR snapshot here. One remote resource, one copy: `loop["pr"]`.
        # A per-pass copy cannot stay true -- checks, review state and the
        # head commit all move after the report is written -- and the run
        # view was rendering this one, so it showed the PR as it looked when
        # the stage finished. `push_at` and `addressed_comments` stay because
        # what a pass pushed and answered really is per-pass.
        "addressed_comments": [
            dict(item) for item in stage_row.get("addressed_comments", []) if isinstance(item, dict)
        ]
        if isinstance(stage_row.get("addressed_comments"), list)
        else [],
        "feedback_instruction": actions.str_or_empty(stage_row.get("feedback_instruction")),
    }
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


def _disown_push(stage_row: dict[str, Any]) -> None:
    """Clear the push fields from the occurrence just recorded on a stage.

    Preconditions: the newest occurrence in ``stage_row["reports"]`` did not
    push. Postconditions: it claims no push time and no addressed comments;
    the stage row and every earlier occurrence are untouched.
    """
    reports = stage_row.get("reports")
    latest = reports[-1] if isinstance(reports, list) and reports else None
    if isinstance(latest, dict):
        latest["push_at"] = None
        latest["addressed_comments"] = []
        latest["feedback_instruction"] = ""


def _copy_report_to_run(run: dict[str, Any], report: LoopStageReport) -> None:
    run["summary"] = report.summary
    run["divergences"] = report.divergences
    run["skipped_scope"] = report.skipped_scope
    run["blockers"] = report.blocker
    run["changes"] = report.changes
    run["validation_evidence"] = report.validation_evidence


def _await_approval(
    run: dict[str, Any],
    loop: dict[str, Any],
    *,
    current_id: str | None,
    continues: bool = False,
) -> None:
    now = actions.now_iso()
    run["status"] = LoopRunStatus.COMPLETED_PENDING_REVIEW.value
    run["completed_at"] = now
    loop["status"] = LoopStatus.AWAITING_APPROVAL.value
    loop["status_reason"] = (
        "Paused for your check; approving starts the next stage."
        if continues
        else "All automatic stages passed; result approval is required."
    )
    loop["current_stage_id"] = current_id or ""
    run["loop"] = loop


def _complete_pr(run: dict[str, Any], loop: dict[str, Any], current_id: str) -> None:
    """Finish an already-approved loop after its PR stage passes."""
    now = actions.now_iso()
    run["status"] = LoopRunStatus.ACCEPTED.value
    run["completed_at"] = now
    run["accepted_at"] = now
    loop["status"] = LoopStatus.ACCEPTED.value
    loop["status_reason"] = "Pull request updated; the loop pass is complete."
    loop["current_stage_id"] = current_id
    # Reaching an accepted run answers whatever was still outstanding, the same
    # way an explicit approval does. Nothing is waived here: `loop["findings"]`
    # now holds this PR stage's own report, which no user ever passed judgement
    # on. What the user let stand was recorded when they approved.
    feedback.answer_all(loop)
    run["loop"] = loop


def _fail_run(
    run: dict[str, Any],
    loop: dict[str, Any],
    reason: str,
    findings: list[str],
    *,
    failure_kind: LoopFailureKind = LoopFailureKind.STAGE_OUTCOME,
) -> None:
    run["status"] = LoopRunStatus.BLOCKED.value
    run["completed_at"] = actions.now_iso()
    loop["status"] = LoopStatus.FAILED.value
    loop["failure_kind"] = failure_kind.value
    loop["status_reason"] = reason
    loop["findings"] = findings
    run["loop"] = loop


def _stage_row(loop: dict[str, Any], step_id: str) -> dict[str, Any] | None:
    rows = loop.get("stages")
    if not isinstance(rows, list):
        return None
    return next(
        (row for row in rows if isinstance(row, dict) and row.get("id") == step_id),
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
    store: LoopRunStateStore,
    target: LoopRunTarget,
) -> None:
    store.save(target)


def _store_cursor_if_advanced(
    store: LoopRunStateStore,
    target: LoopRunTarget,
    run: dict[str, Any],
    loop: dict[str, Any],
    latest_seq: int,
    cursor: int,
) -> None:
    if latest_seq <= cursor:
        return
    loop["last_checked_seq"] = latest_seq
    run["loop"] = loop
    _write_run(store, target)


def _idle_expired(req: MonitorLoopRunRequest, idle_for: float) -> bool:
    return req.idle_timeout_seconds is not None and idle_for >= req.idle_timeout_seconds


def _lease_expiry() -> datetime:
    """Return the next persisted monitor lease deadline."""
    return datetime.now(UTC) + timedelta(seconds=60)


def _latest_seq(events: list[dict[str, Any]], default: int) -> int:
    seqs = [seq for event in events if isinstance((seq := event.get("seq")), int)]
    return max(seqs, default=default)


__all__ = [
    "AgentNotFound",
    "LoopRunNotFound",
    "MonitorLoopRunRequest",
    "WorkNotFound",
    "execute",
]


def _react_to_terminal_pr(
    workstore: WorkStore,
    work_slug: str,
    run: dict[str, Any],
    loop: dict[str, Any],
) -> bool:
    """Handle a run whose pull request has left GitHub's open set.

    ``_resolve_pr_completion`` forces the PR stage to update the stored URL and
    refuses any other pull request, so a run pointing at a finished PR cannot
    complete -- and a run that keeps iterating against a merged one produces
    passes nobody asked for. The two endings differ:

    - **merged** -- the work landed. Block for a human, who starts a new run if
      more is needed. Continuing would reopen decisions already made.
    - **closed** -- the pull request was abandoned but the work was not. Drop it
      and let the PR stage open a fresh one.

    Preconditions: ``run`` is running and ``loop`` is its snapshot.
    Postconditions: ``True`` when the snapshot changed and must be persisted.
    """
    if not pr_lifecycle.has_pull_request(loop):
        return False
    status = pr_lifecycle.live_pr_status(
        loop,
        [
            artifact
            for artifact in workstore.list_artifacts_for_work(work_slug)
            if isinstance(artifact, PrArtifact)
        ],
    )
    if not is_terminal_pr_status(status):
        if not status:
            return False
        # Adopt a non-terminal correction too (draft promoted to open, say), so
        # the run stops reporting a state the poller has already moved past.
        before = actions.dict_or_empty(loop.get("pr")).get("status")
        pr_lifecycle.adopt_live_pr_status(loop, status)
        if before == status:
            return False
        run["loop"] = loop
        return True
    url = actions.str_or_empty(actions.dict_or_empty(loop.get("pr")).get("url"))
    if status == "closed":
        pr_lifecycle.clear_for_new_pr(loop)
        loop["status_reason"] = f"{url} was closed; the PR stage will open a new one."
        run["loop"] = loop
        return True
    pr_lifecycle.adopt_live_pr_status(loop, status)
    run["status"] = LoopRunStatus.BLOCKED.value
    run["completed_at"] = actions.now_iso()
    loop["status"] = LoopStatus.BLOCKED_USER.value
    loop["status_reason"] = (
        f"{url} was merged, so this run has nowhere to push. "
        "Start a new run for any further work."
    )
    run["loop"] = loop
    return True


def _without_a_pr_send_back(
    stage: LoopStepDefinition,
    report: LoopStageReport,
) -> LoopStageReport:
    """Refuse a PR stage's attempt to return work to implementation.

    Publishing is the last decision a run makes: the review passed and the
    result was approved, so findings the PR stage cannot act on are not its to
    reopen. Allowing it produced a loop that could not end -- the stage pushed,
    reported ``changes_requested`` over unaddressed reviewer comments, and the
    comments only clear on a passing PR stage, so every following pass met the
    same bundle and sent the work back again.

    Applied here rather than only in the stage definition because a loop
    authored before this rule still wires the transition, and its runs must not
    keep circling.

    Only agent-produced reports are refused. The same return edge carries the
    user's own "send this PR feedback back for implementation" decision, which
    ``_apply_pr_feedback_decision`` synthesises as a changes-requested report;
    that is the edge working as designed and must keep working.

    Preconditions: ``report`` is the consumed report for ``stage``.
    Postconditions: unchanged unless a PR stage requested changes, which becomes
    a failure naming the reason.
    """
    if not isinstance(stage, PrStage) or report.outcome != LoopOutcome.CHANGES_REQUESTED:
        return report
    return replace(
        report,
        outcome=LoopOutcome.FAILED,
        summary=(
            f"{report.summary} (Create PR cannot request changes; publishing is the "
            "final step, so the run stopped here instead of starting another pass.)"
        ),
    )
