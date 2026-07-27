"""Start or reuse the reserved Planning chat for a Work."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from src.domain.agents import SPECS, CommonAgentConfig
from src.domain.chatstore.dtos import ChatGrounding, ChatRecord, CreateChatRequest
from src.domain.chatstore.ports import ChatStore
from src.domain.models import Provider
from src.domain.planning.dtos import (
    PlanningFramework,
    PlanningFrameworkStatus,
    PlanningProfile,
)
from src.domain.planning.frameworks import artifact_root_rel_path, check_framework_status
from src.domain.planning.models import PlanningSession
from src.domain.planning.ports import PlanningSessionRepository
from src.domain.planning.prompts import PlanningChatInitialPrompt
from src.domain.planning.session_config import (
    PLANNING_CONFIG_OPTION,
    planning_config_option,
)
from src.domain.prompts import build_prompt
from src.domain.workstore.ports import WorkStore


class WorkNotFound(ValueError):
    """The work_slug doesn't exist."""


class PlanningFrameworkNotReady(ValueError):
    """The selected framework is not initialized in the working folder."""


@dataclass(frozen=True)
class StartPlanningChatRequest:
    """Inputs for starting the framework-guided Planning chat."""

    work_slug: str
    root_path: str
    idea: str
    framework: PlanningFramework
    profile: PlanningProfile
    provider: Provider
    model: str
    artifact_root_path: str | None = None
    options: dict[str, Any] = field(default_factory=dict)


def execute(
    workstore: WorkStore,
    chatstore: ChatStore,
    planning_sessions: PlanningSessionRepository,
    req: StartPlanningChatRequest,
) -> tuple[ChatRecord, PlanningFrameworkStatus]:
    """Create or return the Work's reserved Planning chat.

    Preconditions: the Work exists and the selected framework is initialized
    in the selected root unless it is Custom.
    Postconditions: a work-grounded chat titled ``Planning`` exists.
    """
    record = workstore.get_work(req.work_slug)
    if record is None:
        raise WorkNotFound(f"work not found: {req.work_slug}")
    status = check_framework_status(req.framework, req.root_path)
    if not status.ready:
        command = " ".join(status.setup_command) or "initialize the selected framework"
        raise PlanningFrameworkNotReady(
            f"{status.label} is not initialized in {status.root_path}. "
            f"{status.setup_hint} Command: {command}"
        )

    existing = _existing_planning_chat(chatstore, req.work_slug)
    if existing is not None:
        _upsert_planning_session(planning_sessions, req, existing.chat.slug)
        return existing, status

    work = record.work
    session = _planning_session(req, None)
    chat_options = _discovery_options(req.provider, req.options)
    chat_options[PLANNING_CONFIG_OPTION] = planning_config_option(session)
    first_message = build_prompt(
        PlanningChatInitialPrompt(
            work_slug=req.work_slug,
            work_name=work.name,
            project_name=work.project_slug,
            idea=req.idea,
            framework=req.framework,
            profile=req.profile,
        )
    )
    created = chatstore.create_chat(
        CreateChatRequest(
            provider=req.provider,
            model=req.model,
            first_message=first_message,
            title="Planning",
            grounding=ChatGrounding(kind="work", ref=req.work_slug),
            working_directory=req.root_path,
            options=chat_options,
            role="planning",
        )
    )
    _upsert_planning_session(planning_sessions, req, created.chat.slug)
    return created, status


def validate_provider_config(req: StartPlanningChatRequest) -> None:
    """Validate provider/model/options for a Planning chat request."""
    SPECS[req.provider].build(
        CommonAgentConfig(
            workdir=Path(req.root_path).expanduser(),
            writable_roots=(),
            system_prompt="",
        ),
        req.model,
        _discovery_options(req.provider, req.options),
    )


def _existing_planning_chat(
    chatstore: ChatStore,
    work_slug: str,
) -> ChatRecord | None:
    for record in chatstore.list_chats():
        chat = record.chat
        if (
            chat.title.strip().casefold() == "planning"
            and chat.grounding_kind == "work"
            and chat.grounding_ref == work_slug
        ):
            return record
    return None


def _discovery_options(provider: Provider, options: dict[str, Any]) -> dict[str, Any]:
    next_options = dict(options)
    if provider == "codex":
        next_options["sandbox"] = "read-only"
        next_options.setdefault("approval_mode", "on-request")
    elif provider == "codex-acp":
        next_options["mode"] = "read-only"
    elif provider == "claude-code":
        next_options["permission_mode"] = "plan"
    elif provider == "claude-acp":
        next_options["permission_mode"] = "plan"
    return next_options


def _upsert_planning_session(
    planning_sessions: PlanningSessionRepository,
    req: StartPlanningChatRequest,
    planning_chat_slug: str | None,
) -> PlanningSession:
    session = _planning_session(req, planning_chat_slug)
    return planning_sessions.upsert_session(session)


def _planning_session(
    req: StartPlanningChatRequest, planning_chat_slug: str | None
) -> PlanningSession:
    now = datetime.now(UTC)
    return PlanningSession(
        work_slug=req.work_slug,
        planning_chat_slug=planning_chat_slug,
        root_path=req.root_path,
        artifact_root_path=req.artifact_root_path
        or artifact_root_rel_path(req.framework, req.work_slug),
        framework=req.framework,
        profile=req.profile,
        provider=req.provider,
        model=req.model,
        options=dict(req.options),
        created_at=now,
        updated_at=now,
    )


__all__ = [
    "PlanningFrameworkNotReady",
    "StartPlanningChatRequest",
    "WorkNotFound",
    "execute",
    "validate_provider_config",
]
