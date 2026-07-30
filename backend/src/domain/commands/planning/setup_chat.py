"""Start or reuse a visible chat that initializes a planning framework."""

from __future__ import annotations

from dataclasses import dataclass, field
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
from src.domain.planning.frameworks import check_framework_status
from src.domain.planning.prompts import PlanningFrameworkSetupPrompt
from src.domain.prompts import build_prompt
from src.domain.workstore.ports import WorkStore


class WorkNotFound(ValueError):
    """The work_slug doesn't exist."""


class PlanningFrameworkAlreadyReady(ValueError):
    """The selected framework is already initialized in the working folder."""


@dataclass(frozen=True)
class StartPlanningSetupChatRequest:
    """Inputs for creating a framework setup chat.

    Preconditions: the Work exists and the selected framework is not ready.
    Postconditions: a work-grounded setup chat exists in the selected folder.
    """

    work_slug: str
    root_path: str
    framework: PlanningFramework
    profile: PlanningProfile
    provider: Provider
    model: str
    options: dict[str, Any] = field(default_factory=dict)


def execute(
    workstore: WorkStore,
    chatstore: ChatStore,
    req: StartPlanningSetupChatRequest,
) -> tuple[ChatRecord, PlanningFrameworkStatus]:
    """Create or return a setup chat for the selected planning framework.

    Preconditions: the Work exists and framework markers are missing in
    ``root_path``.
    Postconditions: a visible chat can run the framework installer in
    ``root_path`` using normal chat streaming and permission prompts.
    """
    record = workstore.get_work(req.work_slug)
    if record is None:
        raise WorkNotFound(f"work not found: {req.work_slug}")
    status = check_framework_status(req.framework, req.root_path)
    if status.ready:
        raise PlanningFrameworkAlreadyReady(
            f"{status.label} is already initialized in {status.root_path}"
        )

    existing = _existing_setup_chat(chatstore, req)
    if existing is not None:
        return existing, status

    work = record.work
    first_message = build_prompt(
        PlanningFrameworkSetupPrompt(
            work_slug=req.work_slug,
            work_name=work.name,
            framework=req.framework,
            profile=req.profile,
            root_path=status.root_path,
        )
    )
    return (
        chatstore.create_chat(
            CreateChatRequest(
                provider=req.provider,
                model=req.model,
                first_message=first_message,
                title=f"Planning setup: {status.label}",
                grounding=ChatGrounding(kind="work", ref=req.work_slug),
                working_directory=status.root_path,
                options=_setup_options(req.provider, req.options),
            )
        ),
        status,
    )


def validate_provider_config(req: StartPlanningSetupChatRequest) -> None:
    """Validate provider/model/options for a Planning setup chat request."""
    workdir = Path(req.root_path).expanduser()
    SPECS[req.provider].build(
        CommonAgentConfig(
            workdir=workdir,
            writable_roots=(workdir,),
            system_prompt="",
        ),
        req.model,
        _setup_options(req.provider, req.options),
    )


def _existing_setup_chat(
    chatstore: ChatStore, req: StartPlanningSetupChatRequest
) -> ChatRecord | None:
    status = check_framework_status(req.framework, req.root_path)
    title = f"planning setup: {status.label}".casefold()
    root_path = str(Path(status.root_path).expanduser())
    for record in chatstore.list_chats():
        chat = record.chat
        if (
            chat.title.strip().casefold() == title
            and chat.grounding_kind == "work"
            and chat.grounding_ref == req.work_slug
            and str(Path(chat.working_directory or "").expanduser()) == root_path
        ):
            return record
    return None


def _setup_options(provider: Provider, options: dict[str, Any]) -> dict[str, Any]:
    next_options = dict(options)
    if provider == "codex":
        next_options["sandbox"] = "workspace-write"
        next_options.setdefault("approval_mode", "on-request")
    elif provider == "codex-acp":
        next_options["mode"] = "agent"
    elif provider == "claude-code":
        next_options["permission_mode"] = "default"
    elif provider == "claude-acp":
        next_options["permission_mode"] = "default"
    elif provider == "opencode":
        next_options["mode"] = "build"
    return next_options


__all__ = [
    "PlanningFrameworkAlreadyReady",
    "StartPlanningSetupChatRequest",
    "WorkNotFound",
    "execute",
    "validate_provider_config",
]
