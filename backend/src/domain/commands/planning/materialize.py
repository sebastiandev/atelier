"""Run the Planning materializer and persist its reported source plan."""

from __future__ import annotations

import asyncio
import re
from dataclasses import dataclass, field
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
from src.domain.planning.ports import PlanningFiles
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
    root_path: str
    framework: PlanningFramework
    profile: PlanningProfile
    provider: Provider | None = None
    model: str | None = None
    options: dict[str, Any] = field(default_factory=dict)
    planning_chat_slug: str | None = None
    artifacts: tuple[PlanArtifactEntry, ...] = ()


async def execute(
    workstore: WorkStore,
    chatstore: ChatStore,
    projectstore: ProjectStore,
    files: PlanningFiles,
    chat_supervisor: AgentSupervisorService,
    settings: Any,
    req: MaterializePlanRequest,
) -> WorkPlanView:
    """Create a source-backed plan from metadata or a materializer run.

    Preconditions: callers provide either direct metadata entries or a
    provider/model pair for the write-capable materializer.
    Postconditions: a ``WorkPlanView`` exists for the Work.
    """
    if req.artifacts:
        plan = materialization.submit_plan_materialization(
            workstore,
            files,
            work_slug=req.work_slug,
            root_path=req.root_path,
            framework=req.framework,
            profile=req.profile,
            artifacts=req.artifacts,
        )
        await _restart_planning_chat(chat_supervisor, req)
        return plan
    if req.provider is None or req.model is None:
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

    plan = _try_finalize(workstore, chatstore, files, req, chat_slug)
    if plan is not None:
        await _restart_planning_chat(chat_supervisor, req)
        return plan

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
                    _follow_up_prompt(req.work_slug),
                )
                nudge_first = False
            plan = await _wait_for_report_or_turn_end(
                stream,
                workstore,
                chatstore,
                files,
                chat_supervisor,
                req,
                chat_slug,
            )
            if plan is not None:
                await _restart_planning_chat(chat_supervisor, req)
                return plan

    plan = _try_finalize(workstore, chatstore, files, req, chat_slug)
    if plan is not None:
        await _restart_planning_chat(chat_supervisor, req)
        return plan
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
    try:
        return materialization.finalize_materialization_report(
            workstore,
            chatstore,
            files,
            work_slug=req.work_slug,
            chat_slug=chat_slug,
            framework=req.framework,
            profile=req.profile,
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
    return plan


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


def _follow_up_prompt(work_slug: str) -> str:
    """Build the nudge sent when the materializer omits its report."""
    return (
        "Continue the Planning materialization. If any planning files are "
        f"missing, finish writing them under `.atelier/planning/{work_slug}/`. "
        "Generate the complete reviewable set now: framework-level docs plus "
        "all known executable stories, tasks, spikes, bugs, hotfixes, or "
        "follow-up work items. Do not leave placeholders that require another "
        "agent to scope the executable item before implementation. "
        "Then emit exactly one single-line atelier_plan_materialization JSON "
        "report with path, title, artifact_kind, executable, and dependencies "
        "for every created Markdown artifact. Do not include Markdown content "
        "in the report."
    )


__all__ = [
    "MaterializationIncomplete",
    "MaterializePlanRequest",
    "execute",
    "try_finalize_existing",
]
