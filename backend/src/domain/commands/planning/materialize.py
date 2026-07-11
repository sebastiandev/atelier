"""Run the Planning materializer and persist its reported source plan."""

from __future__ import annotations

import asyncio
import re
from dataclasses import dataclass, field, replace
from typing import Any

from src.domain.agents import PermissionDecisionValue
from src.domain.chats import runtime as chat_runtime
from src.domain.chatstore.ports import ChatStore
from src.domain.models import Provider
from src.domain.planning import materialization
from src.domain.planning.dtos import (
    PlanArtifactEntry,
    PlanningFramework,
    PlanningProfile,
    WorkPlanView,
)
from src.domain.planning.ports import PlanningFiles, PlanningSessionRepository
from src.domain.projectstore.ports import ProjectStore
from src.domain.supervisor import AgentSupervisorService
from src.domain.workstore.ports import WorkStore

_MAX_FOLLOW_UPS = 2
_TURN_TIMEOUT_SECONDS = 300
_WEB_TOOL_NAMES = ("web", "fetch", "browser", "http")
_NETWORK_OR_INSTALL_COMMAND = re.compile(
    r"("
    r"https?://|"
    r"\b(curl|wget|http|httpie|aria2c|lynx|w3m)\b|"
    r"\b(npx|npm\s+(install|add|exec|create)|pnpm\s+(install|add|dlx|create)|"
    r"yarn\s+(install|add|create|dlx)|bun\s+(install|add|x|create))\b|"
    r"\b(pipx?\s+install|uv\s+(add|sync|pip\s+install|tool\s+install)|"
    r"poetry\s+(add|install)|bundle\s+install|cargo\s+install|go\s+get)\b|"
    r"\b(brew|apt-get|apt|dnf|yum|pacman)\s+install\b|"
    r"\bgit\s+(clone|pull|fetch|submodule\s+update)\b"
    r")",
    re.IGNORECASE,
)


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
    artifact_root_path: str | None = None
    options: dict[str, Any] = field(default_factory=dict)
    planning_chat_slug: str | None = None
    artifacts: tuple[PlanArtifactEntry, ...] = ()


async def execute(
    workstore: WorkStore,
    chatstore: ChatStore,
    projectstore: ProjectStore,
    files: PlanningFiles,
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
            work_slug=req.work_slug,
            root_path=req.root_path,
            artifact_root_path=req.artifact_root_path,
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
        artifact_root_path=req.artifact_root_path,
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

    await _resolve_pending_materializer_permission(chatstore, chat_supervisor, chat_slug)
    finalized = _try_finalize(workstore, chatstore, files, req, chat_slug)
    if finalized is not None:
        await _restart_planning_chat(chat_supervisor, req)
        return finalized

    cursor = _last_transcript_seq(chatstore, chat_slug)
    nudge_first = _has_materializer_activity(chatstore, chat_slug)
    async with chat_runtime.connect_chat(
        chatstore,
        chat_supervisor,
        workstore,
        projectstore,
        files,
        settings,
        chat_runtime.ConnectChatRuntimeRequest(chat_slug=chat_slug, cursor=cursor),
    ) as subscription:
        stream = subscription.stream()
        for attempt in range(_MAX_FOLLOW_UPS + 1):
            if nudge_first or attempt > 0:
                await chat_supervisor.send_input(
                    chat_slug,
                    _follow_up_prompt(req),
                )
                nudge_first = False
            iteration_result = await _wait_for_report_or_turn_end(
                stream,
                workstore,
                chatstore,
                files,
                chat_supervisor,
                req,
                chat_slug,
            )
            if iteration_result is not None:
                await _restart_planning_chat(chat_supervisor, req)
                return iteration_result

    final_result = _try_finalize(workstore, chatstore, files, req, chat_slug)
    if final_result is not None:
        await _restart_planning_chat(chat_supervisor, req)
        return final_result
    raise MaterializationIncomplete(
        "planning materializer did not emit atelier_plan_materialization"
    )


async def _wait_for_report_or_turn_end(
    stream: Any,
    workstore: WorkStore,
    chatstore: ChatStore,
    files: PlanningFiles,
    chat_supervisor: AgentSupervisorService,
    req: MaterializePlanRequest,
    chat_slug: str,
) -> WorkPlanView | None:
    """Wait for one materializer turn to finish or report a plan."""
    deadline = asyncio.get_running_loop().time() + _TURN_TIMEOUT_SECONDS
    while True:
        remaining = deadline - asyncio.get_running_loop().time()
        if remaining <= 0:
            return None
        try:
            event = await asyncio.wait_for(anext(stream), timeout=remaining)
        except StopAsyncIteration:
            return _try_finalize(workstore, chatstore, files, req, chat_slug)
        except TimeoutError:
            return None
        event_type = event.get("type")
        if event_type == "message_complete":
            plan = _try_finalize(workstore, chatstore, files, req, chat_slug)
            if plan is not None:
                return plan
        if event_type == "permission_request":
            await _resolve_materializer_permission(
                chat_supervisor, chat_slug, event
            )
        if event_type == "error":
            message = event.get("message")
            raise MaterializationIncomplete(
                str(message) if isinstance(message, str) else "materializer failed"
            )
        if event_type == "status_change" and event.get("status") == "idle":
            return _try_finalize(workstore, chatstore, files, req, chat_slug)


def _try_finalize(
    workstore: WorkStore,
    chatstore: ChatStore,
    files: PlanningFiles,
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
            work_slug=req.work_slug,
            chat_slug=chat_slug,
            framework=req.framework,
            profile=req.profile,
            artifact_root_path=req.artifact_root_path,
        )
    except materialization.MaterializationReportNotFound:
        return None


def _last_transcript_seq(chatstore: ChatStore, chat_slug: str) -> int:
    events = list(chatstore.read_transcript_from_cursor(chat_slug, 0))
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
    plan = _try_finalize(workstore, chatstore, files, req, chat_slug)
    if plan is not None:
        await _restart_planning_chat(chat_supervisor, req)
    else:
        await _resolve_pending_materializer_permission(
            chatstore, chat_supervisor, chat_slug
        )
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
        artifact_root_path=session.artifact_root_path,
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


async def _resolve_materializer_permission(
    chat_supervisor: AgentSupervisorService, chat_slug: str, event: dict[str, Any]
) -> None:
    """Answer a materializer permission request with the local policy."""
    request_id = event.get("request_id")
    if not isinstance(request_id, str) or not request_id:
        return
    await chat_supervisor.resolve_permission(
        chat_slug, request_id, _materializer_permission_decision(event)
    )


async def _resolve_pending_materializer_permission(
    chatstore: ChatStore,
    chat_supervisor: AgentSupervisorService,
    chat_slug: str,
) -> None:
    """Answer the latest still-pending materializer permission, if any."""
    pending = _pending_permission(list(chatstore.read_transcript_from_cursor(chat_slug, 0)))
    if pending is not None:
        await _resolve_materializer_permission(chat_supervisor, chat_slug, pending)


def _pending_permission(events: list[dict[str, Any]]) -> dict[str, Any] | None:
    decided: set[str] = set()
    completed_tools: set[str] = set()
    requests: list[dict[str, Any]] = []
    for event in events:
        event_type = event.get("type")
        request_id = event.get("request_id")
        tool_id = event.get("tool_id")
        if event_type == "permission_decision" and isinstance(request_id, str):
            decided.add(request_id)
        elif event_type == "tool_result" and isinstance(tool_id, str):
            completed_tools.add(tool_id)
        elif event_type == "permission_request":
            requests.append(event)
    for event in reversed(requests):
        request_id = event.get("request_id")
        tool_id = event.get("tool_id")
        if isinstance(request_id, str) and request_id in decided:
            continue
        if isinstance(tool_id, str) and tool_id in completed_tools:
            continue
        return event
    return None


def _materializer_permission_decision(event: dict[str, Any]) -> PermissionDecisionValue:
    """Allow local planning work and deny network or install activity."""
    tool_name = str(event.get("tool_name") or "").casefold()
    tool_input = event.get("tool_input")
    command = _shell_command(tool_input)
    if any(part in tool_name for part in _WEB_TOOL_NAMES):
        return "deny"
    if command and _NETWORK_OR_INSTALL_COMMAND.search(command):
        return "deny"
    return "allow"


def _shell_command(tool_input: Any) -> str:
    """Extract a shell command string from canonical tool input."""
    if not isinstance(tool_input, dict):
        return ""
    command = tool_input.get("command")
    if isinstance(command, str):
        return command
    cmd = tool_input.get("cmd")
    if isinstance(cmd, str):
        return cmd
    argv = tool_input.get("argv")
    if isinstance(argv, list) and all(isinstance(item, str) for item in argv):
        return " ".join(argv)
    return ""


def _follow_up_prompt(req: MaterializePlanRequest) -> str:
    """Build the nudge sent when the materializer omits its report."""
    if req.root_path is None or req.framework is None:
        raise ValueError("root_path and framework are required for materialization")
    artifact_root, _absolute_artifact_root = materialization.resolve_artifact_root(
        req.root_path, req.framework, req.work_slug, req.artifact_root_path
    )
    return (
        "Continue the Planning materialization. If any planning files are "
        f"missing, finish writing them under `{artifact_root}/`. "
        "Generate the complete reviewable set now: framework-level docs plus "
        "all known executable stories, tasks, spikes, bugs, hotfixes, or "
        "follow-up work items. Do not leave placeholders that require another "
        "agent to scope the executable item before implementation. "
        "Then emit exactly one single-line atelier_plan_materialization JSON "
        "report with path, title, artifact_kind, executable, and dependencies "
        "for every created Markdown artifact. Paths must be relative to that "
        "framework output folder. Do not include Markdown content in the report."
    )


__all__ = [
    "MaterializationIncomplete",
    "MaterializePlanRequest",
    "PlanningSessionNotFound",
    "execute",
    "resolve_from_planning_session",
    "try_finalize_existing",
]
