"""Connect a chat stream to the shared supervisor runtime."""

from __future__ import annotations

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager, suppress
from dataclasses import dataclass
from pathlib import Path

from src.domain.agents import SPECS, AgentAdapter, AgentStartContext, CommonAgentConfig
from src.domain.agents.configs import AgentConfig
from src.domain.chatstore import ChatRecord, ChatStore
from src.domain.models import Chat
from src.domain.projectstore.ports import ProjectStore
from src.domain.supervisor import AgentSubscription, AgentSupervisorService
from src.domain.workstore.ports import WorkStore
from src.infrastructure.agents import build_adapter
from src.infrastructure.filesystem.paths import WorkspacePaths
from src.settings import Settings

_CHAT_WORK_SLUG = "__chat__"


@dataclass(frozen=True)
class ConnectChatRequest:
    chat_slug: str
    cursor: int = 0


class ChatNotFound(ValueError):
    """The requested chat slug does not resolve to a stored chat."""


@asynccontextmanager
async def execute(
    chatstore: ChatStore,
    supervisor: AgentSupervisorService,
    workstore: WorkStore,
    projectstore: ProjectStore,
    settings: Settings,
    req: ConnectChatRequest,
) -> AsyncIterator[AgentSubscription]:
    record = chatstore.get_chat(req.chat_slug)
    if record is None:
        raise ChatNotFound(f"chat not found: {req.chat_slug}")

    if not supervisor.is_registered(req.chat_slug):
        adapter, context = _build_adapter(record, workstore, projectstore, settings)
        try:
            await supervisor.register_agent(
                _CHAT_WORK_SLUG,
                req.chat_slug,
                adapter,
                context,
                lazy=True,
            )
        except RuntimeError:
            with suppress(Exception):
                await adapter.close()
            if not supervisor.is_registered(req.chat_slug):
                raise
            await supervisor.refresh_seq_from_disk(req.chat_slug)

    initial = chatstore.claim_initial_prompt(req.chat_slug)
    if initial is not None:
        await supervisor.refresh_seq_from_disk(req.chat_slug)
        await supervisor.send_input(
            req.chat_slug,
            initial,
            record_user_input=False,
        )

    async with supervisor.subscribe(req.chat_slug, req.cursor) as sub:
        yield sub


def _build_adapter(
    record: ChatRecord,
    workstore: WorkStore,
    projectstore: ProjectStore,
    settings: Settings,
) -> tuple[AgentAdapter, AgentStartContext]:
    config, context, _runtime = build_chat_runtime_config(
        record, workstore, projectstore, settings
    )
    adapter = build_adapter(config, settings)
    return adapter, context


def build_chat_runtime_config(
    record: ChatRecord,
    workstore: WorkStore,
    projectstore: ProjectStore,
    settings: Settings,
) -> tuple[AgentConfig, AgentStartContext, _ChatRuntimeContext]:
    chat = record.chat
    runtime = _resolve_runtime_context(chat, workstore, projectstore, settings)
    system_prompt = _render_chat_system_prompt(chat, runtime)
    common = CommonAgentConfig(
        workdir=runtime.workdir,
        writable_roots=(runtime.workdir,),
        system_prompt=system_prompt,
    )
    config = SPECS[chat.provider].build(common, chat.model, {})
    context = AgentStartContext(
        workdir=runtime.workdir,
        model=chat.model,
        system_prompt=system_prompt,
        session_id=chat.session_id,
    )
    return config, context, runtime


@dataclass(frozen=True)
class _ChatRuntimeContext:
    workdir: Path
    working_label: str
    working_details: str
    link_label: str
    link_details: str


def _resolve_runtime_context(
    chat: Chat,
    workstore: WorkStore,
    projectstore: ProjectStore,
    settings: Settings,
) -> _ChatRuntimeContext:
    paths = WorkspacePaths(settings.workspace_root)
    link_label, link_details = _resolve_link_context(chat, workstore, projectstore)
    working_directory = chat.working_directory
    if working_directory is None and chat.grounding_kind == "folder":
        # Legacy shape: folder grounding used to mean "run the chat here".
        working_directory = chat.grounding_ref

    if working_directory:
        folder = Path(working_directory).expanduser()
        if folder.exists() and folder.is_dir():
            return _ChatRuntimeContext(
                workdir=folder,
                working_label=f"folder {folder}",
                working_details=_join_details(
                    "The chat uses this folder as its working directory.",
                    "Inspect files only when the user asks or when it is needed "
                    "for the conversation.",
                ),
                link_label=link_label,
                link_details=link_details,
            )
        return _fallback_context(
            settings,
            working_label=f"missing folder {folder}",
            working_details=(
                "The selected working folder no longer exists, so the runtime is "
                "using the Atelier workspace root as its cwd."
            ),
            link_label=link_label,
            link_details=link_details,
        )

    if chat.grounding_kind == "work" and chat.grounding_ref:
        workdir = paths.work_dir(chat.grounding_ref)
        workdir.mkdir(parents=True, exist_ok=True)
        return _ChatRuntimeContext(
            workdir=workdir,
            working_label=f"work metadata folder {workdir}",
            working_details=(
                "No working folder was selected. This chat has no per-agent "
                "worktree, so the cwd is the Atelier metadata folder for the "
                "linked work."
            ),
            link_label=link_label,
            link_details=link_details,
        )

    if chat.grounding_kind == "project" and chat.grounding_ref:
        workdir = paths.project_dir(chat.grounding_ref)
        workdir.mkdir(parents=True, exist_ok=True)
        return _ChatRuntimeContext(
            workdir=workdir,
            working_label=f"project metadata folder {workdir}",
            working_details=(
                "No working folder was selected. The cwd is the Atelier metadata "
                "folder for the linked project."
            ),
            link_label=link_label,
            link_details=link_details,
        )

    return _fallback_context(
        settings,
        working_label="Atelier workspace root",
        working_details="No working folder was selected.",
        link_label=link_label,
        link_details=link_details,
    )


def _fallback_context(
    settings: Settings,
    *,
    working_label: str,
    working_details: str,
    link_label: str,
    link_details: str,
) -> _ChatRuntimeContext:
    settings.workspace_root.mkdir(parents=True, exist_ok=True)
    return _ChatRuntimeContext(
        workdir=settings.workspace_root,
        working_label=working_label,
        working_details=working_details,
        link_label=link_label,
        link_details=link_details,
    )


def _resolve_link_context(
    chat: Chat,
    workstore: WorkStore,
    projectstore: ProjectStore,
) -> tuple[str, str]:
    if chat.grounding_kind == "work" and chat.grounding_ref:
        work_record = workstore.get_work(chat.grounding_ref)
        if work_record is None:
            return f"work {chat.grounding_ref}", "The referenced work could not be loaded."
        work = work_record.work
        return (
            f'work {work.slug} "{work.name}"',
            _join_details(
                f"Work name: {work.name}",
                f"Work status: {work.status}",
                f"Work description: {work.description}",
                f"Project: {work.project_slug or 'none'}",
            ),
        )

    if chat.grounding_kind == "project" and chat.grounding_ref:
        project_record = projectstore.get_project(chat.grounding_ref)
        if project_record is None:
            return (
                f"project {chat.grounding_ref}",
                "The referenced project could not be loaded.",
            )
        project = project_record.project
        return (
            f'project {project.slug} "{project.name}"',
            _join_details(
                f"Project name: {project.name}",
                f"Project description: {project.description}",
            ),
        )

    return "none", "No Project or Work link was selected."


def _join_details(*parts: str) -> str:
    return "\n".join(part.strip() for part in parts if part.strip())


def _render_chat_system_prompt(chat: Chat, runtime: _ChatRuntimeContext) -> str:
    slug = chat.slug or "unsaved chat"
    return (
        "You are an Atelier exploratory chat.\n"
        f"Chat: {slug} - {chat.title}\n"
        "Purpose: help the user think through ambiguity, compare options, "
        "identify risks, and shape possible next actions before work is "
        "handed to an agent.\n\n"
        f"Working directory: {runtime.workdir}\n"
        f"Working folder: {runtime.working_label}\n"
        f"{runtime.working_details}\n\n"
        f"Linked to: {runtime.link_label}\n"
        f"{runtime.link_details}\n\n"
        "This is not a tracked implementation agent. Do not claim that a "
        "worktree, pull request, or artifact was created from this chat. "
        "When the conversation becomes executable, summarize the recommended "
        "next action clearly so the user can hand it off to an agent."
    )


__all__ = [
    "ChatNotFound",
    "ConnectChatRequest",
    "build_chat_runtime_config",
    "execute",
]
