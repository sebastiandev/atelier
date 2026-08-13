"""Run the Planning materializer and persist its reported source plan."""

from __future__ import annotations

import asyncio
from dataclasses import dataclass, field, replace
from datetime import UTC, datetime
from typing import Any

from src.domain.agents.stall_recovery import (
    StallAction,
    StallPolicy,
    StallState,
    next_stall_action,
    record_nudge,
)
from src.domain.agents.turn_monitor import observe_turn
from src.domain.chats import runtime as chat_runtime
from src.domain.chatstore import ChatRecord
from src.domain.chatstore.ports import ChatStore
from src.domain.loop.ports import LoopRunRepository
from src.domain.models import Provider
from src.domain.planning import materialization
from src.domain.planning.dtos import (
    PlanArtifactEntry,
    PlanningFramework,
    PlanningProfile,
    WorkPlanView,
)
from src.domain.planning.ports import PlanningFiles, PlanningSessionRepository
from src.domain.planning.prompts import (
    PlanningMaterializationRecoveryPrompt,
    PlanningMaterializationReportPrompt,
    PlanningMaterializerRuntimePrompt,
)
from src.domain.projectstore.ports import ProjectStore
from src.domain.prompts import build_prompt
from src.domain.supervisor import AgentSupervisorService
from src.domain.workstore.ports import WorkStore

_MAX_IDLE_FOLLOW_UPS = 2
# Far wider than a loop stage's five minutes: materializing a plan means
# reading a repository and writing epics, which is quiet, long work.
_MATERIALIZER_STALL_POLICY = StallPolicy(silence_seconds=20 * 60)
_NUDGE_PROMPT = "continue"
# Asking to cancel is a local call; waiting on it is not worth parking the
# poll that enforces every other bound.
_INTERRUPT_REQUEST_SECONDS = 10
_MAX_CONNECTION_RECOVERIES = 1
_POLL_INTERVAL_SECONDS = 0.5


class MaterializationIncomplete(ValueError):
    """The materializer did not produce a usable report before timing out."""


class PlanningSessionNotFound(ValueError):
    """No persisted PlanningSession exists for the Work."""


@dataclass(frozen=True)
class MaterializePlanRequest:
    """Inputs for creating a source-backed plan.

    Preconditions: the Work exists, the selected framework is ready in
    ``root_path``, and either ``artifacts`` is present or provider/model can
    run a materializer from the Planning chat context.
    Postconditions: the plan manifest indexes source files reported by the
    framework/tool materializer.
    """

    work_slug: str
    root_path: str | None = None
    framework: PlanningFramework | None = None
    profile: PlanningProfile | None = None
    provider: Provider | None = None
    model: str | None = None
    plan_artifacts_dir: str | None = None
    options: dict[str, Any] = field(default_factory=dict)
    planning_chat_slug: str | None = None
    artifacts: tuple[PlanArtifactEntry, ...] = ()
    fresh_session: bool = False
    # Retry stops a wedged provider process without discarding its
    # session, so the replacement resumes the plan instead of starting it
    # over. `fresh_session` remains for the paths that genuinely cannot
    # restore a session.
    restart_runtime: bool = False


async def execute(
    workstore: WorkStore,
    chatstore: ChatStore,
    projectstore: ProjectStore,
    files: PlanningFiles,
    loop_runs: LoopRunRepository,
    planning_sessions: PlanningSessionRepository,
    chat_supervisor: AgentSupervisorService,
    settings: Any,
    req: MaterializePlanRequest,
) -> WorkPlanView:
    """Create a source-backed plan from metadata or a materializer run.

    Preconditions: callers provide either direct metadata entries or a
    provider/model pair for the write-capable materializer.
    Postconditions: a ``WorkPlanView`` exists for the Work.
    """
    req = resolve_from_planning_session(planning_sessions, req)
    if req.artifacts:
        if req.root_path is None or req.framework is None or req.profile is None:
            raise ValueError("root_path, framework, and profile are required")
        plan = materialization.submit_plan_materialization(
            workstore,
            files,
            loop_runs,
            work_slug=req.work_slug,
            root_path=req.root_path,
            plan_artifacts_dir=req.plan_artifacts_dir,
            framework=req.framework,
            profile=req.profile,
            artifacts=req.artifacts,
        )
        await _restart_planning_chat(chat_supervisor, req)
        return plan
    if (
        req.root_path is None
        or req.framework is None
        or req.profile is None
        or req.provider is None
        or req.model is None
    ):
        raise ValueError("provider and model are required when artifacts are omitted")

    materialization.validate_materialization_provider_config(
        root_path=req.root_path,
        provider=req.provider,
        model=req.model,
        options=req.options,
    )
    record, _status = materialization.start_materialization_chat(
        workstore,
        chatstore,
        work_slug=req.work_slug,
        root_path=req.root_path,
        plan_artifacts_dir=req.plan_artifacts_dir,
        framework=req.framework,
        profile=req.profile,
        provider=req.provider,
        model=req.model,
        options=req.options,
        planning_chat_slug=req.planning_chat_slug,
    )
    chat_slug = record.chat.slug
    if chat_slug is None:
        raise RuntimeError("materialization chat has no slug")

    finalized = _try_finalize(workstore, chatstore, files, loop_runs, req, chat_slug)
    if finalized is not None:
        await _restart_planning_chat(chat_supervisor, req)
        return finalized

    if req.fresh_session:
        await materialization.reset_materializer_runtime(
            chatstore, chat_supervisor, chat_slug
        )
    elif req.restart_runtime:
        await materialization.restart_materializer_process(chat_supervisor, chat_slug)
    # A session that survived already holds the brief and everything it has
    # written, so it needs a word rather than the whole brief again. Only a
    # session that was discarded has to be told what it is doing.
    resumed = not req.fresh_session
    recover_first = _has_materializer_activity(chatstore, chat_slug)
    stall = StallState()
    original_brief = _original_materialization_brief(record)
    runtime_prompt = _runtime_prompt(req)
    follow_ups = 0
    recoveries = 0
    while True:
        active = await chat_runtime.ensure_chat_runtime(
            chatstore,
            chat_supervisor,
            workstore,
            projectstore,
            files,
            settings,
            chat_slug,
            system_prompt_override=runtime_prompt,
        )
        if not active:
            raise MaterializationIncomplete("materializer Work is not active")
        if recover_first:
            await chat_supervisor.send_input(
                chat_slug,
                _NUDGE_PROMPT if resumed else _recovery_prompt(req, original_brief),
            )
            recover_first = False
        iteration_result, error, stall = await _poll_for_report_or_turn_end(
            workstore,
            chatstore,
            files,
            loop_runs,
            chat_supervisor,
            req,
            chat_slug,
            stall,
        )
        if iteration_result is not None:
            await _restart_planning_chat(chat_supervisor, req)
            return iteration_result
        if error is not None:
            recoverable = (
                "connection closed" in error.casefold()
                or "runtime is unavailable" in error.casefold()
            )
            if recoverable and recoveries < _MAX_CONNECTION_RECOVERIES:
                recoveries += 1
                await materialization.reset_materializer_runtime(
                    chatstore, chat_supervisor, chat_slug
                )
                resumed = False
                recover_first = True
                continue
            raise MaterializationIncomplete(error)
        if follow_ups >= _MAX_IDLE_FOLLOW_UPS:
            raise MaterializationIncomplete(
                "planning materializer did not emit atelier_plan_materialization"
            )
        await chat_supervisor.send_input(chat_slug, _report_prompt(req))
        follow_ups += 1


async def _poll_for_report_or_turn_end(
    workstore: WorkStore,
    chatstore: ChatStore,
    files: PlanningFiles,
    loop_runs: LoopRunRepository,
    chat_supervisor: AgentSupervisorService,
    req: MaterializePlanRequest,
    chat_slug: str,
    stall: StallState,
) -> tuple[WorkPlanView | None, str | None, StallState]:
    """Poll durable transcript state until the current provider turn ends.

    A materializer is legitimately quiet for a long time -- it is reading a
    repository and writing epics -- so the stall window is far wider than a
    loop stage's. Wide is not unbounded: without one, a wedged turn left this
    polling at half-second intervals for as long as the process lived, and the
    only way out was a person noticing and retrying.
    """
    events = list(chatstore.read_transcript_from_cursor(chat_slug, 0))
    cursor = _last_event_seq(events)
    while True:
        plan = _try_finalize(workstore, chatstore, files, loop_runs, req, chat_slug)
        if plan is not None:
            return plan, None, stall
        now = datetime.now(UTC)
        observation = observe_turn(events, now)
        if observation.terminal_error is not None:
            return None, observation.terminal_error, stall
        if observation.finished:
            return None, None, stall
        if not chat_supervisor.is_registered(chat_slug):
            return None, "Materializer runtime is unavailable.", stall
        decision = next_stall_action(
            observation=observation,
            events=events,
            state=stall,
            policy=_MATERIALIZER_STALL_POLICY,
            now=now,
        )
        stall = decision.state
        if decision.action is StallAction.GIVE_UP:
            # Leaving the wedged process registered would keep it burning its
            # budget, and would make the user's next Retry re-attach to it.
            await chat_supervisor.stop_agent(chat_slug)
            return (
                None,
                (
                    "The materializer stopped responding and did not restart after "
                    f"{_MATERIALIZER_STALL_POLICY.max_strikes} attempts."
                ),
                stall,
            )
        if decision.action is StallAction.INTERRUPT:
            # A wedged turn never reads its queue, so the nudge would sit
            # behind it. Bounded and best effort: cancelling waits on the
            # runtime reporting ready, and one that never does would park this
            # poll for the life of the process.
            try:
                await asyncio.wait_for(
                    chat_supervisor.stop_turn(chat_slug),
                    timeout=_INTERRUPT_REQUEST_SECONDS,
                )
            except Exception:
                # The nudge still goes out; a runtime too broken to cancel
                # fails loudly on the send that follows.
                stall = replace(stall, interrupted_at=None)
        elif decision.action is StallAction.NUDGE:
            # Bare on purpose: this session already holds the brief and
            # whatever it has written so far, so restating them would read as a
            # new instruction and invite it to start over.
            try:
                await chat_supervisor.send_input(chat_slug, _NUDGE_PROMPT)
            except Exception as exc:
                # The dead-pump case this ladder exists for: `send_input`
                # raises once the runtime's task is gone. Reported as a
                # recoverable runtime error so the caller's one reconnect can
                # answer it, rather than escaping as an unhandled exception.
                return None, f"Materializer runtime is unavailable: {exc}", stall
            stall = record_nudge(stall, datetime.now(UTC))
        await asyncio.sleep(_POLL_INTERVAL_SECONDS)
        fresh = list(chatstore.read_transcript_from_cursor(chat_slug, cursor))
        if fresh:
            events.extend(fresh)
            cursor = _last_event_seq(events)


def _try_finalize(
    workstore: WorkStore,
    chatstore: ChatStore,
    files: PlanningFiles,
    loop_runs: LoopRunRepository,
    req: MaterializePlanRequest,
    chat_slug: str,
) -> WorkPlanView | None:
    """Return a finalized plan when the materializer report is present."""
    if req.framework is None or req.profile is None:
        raise ValueError("framework and profile are required for finalization")
    try:
        return materialization.finalize_materialization_report(
            workstore,
            chatstore,
            files,
            loop_runs,
            work_slug=req.work_slug,
            chat_slug=chat_slug,
            framework=req.framework,
            profile=req.profile,
            plan_artifacts_dir=req.plan_artifacts_dir,
        )
    except materialization.MaterializationReportNotFound:
        return None
    except (
        materialization.InvalidMaterializationReport,
        materialization.InvalidPlanMaterialization,
    ) as exc:
        # A report that arrived and could not be accepted is a result, not a
        # crash. Letting it escape killed the task that was polling: the plan
        # was finished on disk, nothing finalized it, and the only thing the
        # user ever saw was "idle, no source-plan report exists" -- with the
        # actual reason, which files were missing, thrown away.
        raise MaterializationIncomplete(str(exc)) from exc


def _last_event_seq(events: list[dict[str, Any]]) -> int:
    if not events:
        return 0
    seq = events[-1].get("seq")
    return seq if isinstance(seq, int) else 0


def _has_materializer_activity(chatstore: ChatStore, chat_slug: str) -> bool:
    active_types = {
        "error",
        "message_complete",
        "message_delta",
        "mode_change",
        "permission_decision",
        "permission_request",
        "status_change",
        "tool_call",
        "tool_result",
    }
    return any(
        event.get("type") in active_types
        for event in chatstore.read_transcript_from_cursor(chat_slug, 0)
    )


async def try_finalize_existing(
    workstore: WorkStore,
    chatstore: ChatStore,
    files: PlanningFiles,
    loop_runs: LoopRunRepository,
    chat_supervisor: AgentSupervisorService,
    req: MaterializePlanRequest,
    chat_slug: str,
) -> WorkPlanView | None:
    """Finalize a materializer report that is already present.

    Preconditions: ``chat_slug`` identifies the materializer chat for
    ``req.work_slug``.
    Postconditions: a source-backed plan is indexed when the transcript already
    contains a valid ``atelier_plan_materialization`` report; otherwise no state
    is changed.
    """
    plan = _try_finalize(workstore, chatstore, files, loop_runs, req, chat_slug)
    if plan is not None:
        await _restart_planning_chat(chat_supervisor, req)
    return plan


def resolve_from_planning_session(
    planning_sessions: PlanningSessionRepository,
    req: MaterializePlanRequest,
) -> MaterializePlanRequest:
    """Return a request whose planning setup comes from PlanningSession.

    Preconditions: metadata-only submissions carry explicit artifact metadata;
    materializer submissions have a persisted PlanningSession for the Work.
    Postconditions: materializer fields are backend-owned, not frontend-owned.
    """
    if req.artifacts:
        return req
    session = (
        planning_sessions.get_by_chat_slug(req.planning_chat_slug)
        if req.planning_chat_slug
        else planning_sessions.get_by_work_slug(req.work_slug)
    )
    if session is None:
        session = planning_sessions.get_by_work_slug(req.work_slug)
    if session is None:
        raise PlanningSessionNotFound(
            f"planning session not found for work: {req.work_slug}"
        )
    return replace(
        req,
        root_path=session.root_path,
        plan_artifacts_dir=session.plan_artifacts_dir,
        framework=session.framework,
        profile=session.profile,
        provider=session.provider,
        model=session.model,
        options=dict(session.options or {}),
        planning_chat_slug=session.planning_chat_slug,
    )


async def _restart_planning_chat(
    chat_supervisor: AgentSupervisorService, req: MaterializePlanRequest
) -> None:
    """Force the Planning chat to reconnect with revision-mode runtime config."""
    if req.planning_chat_slug:
        await chat_supervisor.stop_agent(req.planning_chat_slug)


def _plan_artifacts_dir(req: MaterializePlanRequest) -> str:
    """Resolve the configured materialization output path."""
    if req.root_path is None or req.framework is None:
        raise ValueError("root_path and framework are required for materialization")
    plan_artifacts_dir, _absolute_plan_artifacts_path = materialization.resolve_plan_artifacts_dir(
        req.root_path, req.framework, req.work_slug, req.plan_artifacts_dir
    )
    return plan_artifacts_dir


def _runtime_prompt(req: MaterializePlanRequest) -> str:
    """Build the materializer-owned provider system prompt."""
    if req.root_path is None or req.framework is None:
        raise ValueError("root_path and framework are required for materialization")
    return build_prompt(
        PlanningMaterializerRuntimePrompt(
            framework=req.framework,
            root_path=req.root_path,
            plan_artifacts_dir=_plan_artifacts_dir(req),
        )
    )


def _recovery_prompt(req: MaterializePlanRequest, original_brief: str) -> str:
    """Build the full-context brief for a fresh recovery session."""
    if req.framework is None:
        raise ValueError("framework is required for materialization")
    return build_prompt(
        PlanningMaterializationRecoveryPrompt(
            framework=req.framework,
            plan_artifacts_dir=_plan_artifacts_dir(req),
            original_brief=original_brief,
        )
    )


def _report_prompt(req: MaterializePlanRequest) -> str:
    """Build the bounded report-only nudge after a completed provider turn."""
    if req.framework is None:
        raise ValueError("framework is required for materialization")
    return build_prompt(
        PlanningMaterializationReportPrompt(
            framework=req.framework,
            plan_artifacts_dir=_plan_artifacts_dir(req),
        )
    )


def _original_materialization_brief(record: ChatRecord) -> str:
    """Return the persisted initial user brief for recovery sessions."""
    return next(
        (message.body for message in record.transcript if message.role == "user"),
        "Materialize the source-backed plan and emit the required report.",
    )


__all__ = [
    "MaterializationIncomplete",
    "MaterializePlanRequest",
    "PlanningSessionNotFound",
    "execute",
    "resolve_from_planning_session",
    "try_finalize_existing",
]
