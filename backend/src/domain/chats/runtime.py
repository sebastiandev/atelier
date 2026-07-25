"""Runtime helpers for chat adapter construction and streaming."""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager, suppress
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from src.domain.agents import SPECS, AgentAdapter, AgentStartContext, CommonAgentConfig
from src.domain.agents.configs import AgentConfig
from src.domain.chats.prompts import RegularChatRuntimePrompt
from src.domain.chatstore import ChatRecord, ChatStore
from src.domain.models import Chat, Provider
from src.domain.planning.dtos import PlanningFramework, PlanningProfile
from src.domain.planning.ports import PlanningFiles
from src.domain.planning.prompts import (
    PlanningChatRuntimePrompt,
    PlanningChatTurnContextPrompt,
    PlanningDocumentRef,
)
from src.domain.planning.readiness import PLANNING_READINESS_OPTION
from src.domain.planning.session_config import PLANNING_CONFIG_OPTION
from src.domain.projectstore.ports import ProjectStore
from src.domain.prompts import build_prompt
from src.domain.supervisor import AgentSubscription, AgentSupervisorService
from src.domain.workstore.ports import WorkStore
from src.infrastructure.agents import build_adapter
from src.infrastructure.filesystem.paths import WorkspacePaths
from src.settings import Settings

_CHAT_WORK_SLUG = "__chat__"


@dataclass(frozen=True)
class ConnectChatRuntimeRequest:
    """Inputs for subscribing to a chat runtime."""

    chat_slug: str
    cursor: int = 0
    read_only: bool = False


class ChatNotFound(ValueError):
    """The requested chat slug does not resolve to a stored chat."""


@asynccontextmanager
async def connect_chat(
    chatstore: ChatStore,
    supervisor: AgentSupervisorService,
    workstore: WorkStore,
    projectstore: ProjectStore,
    planningfiles: PlanningFiles,
    settings: Settings,
    req: ConnectChatRuntimeRequest,
) -> AsyncIterator[AgentSubscription]:
    """Connect a chat stream to the shared supervisor runtime.

    Preconditions: ``req.chat_slug`` identifies a stored chat.
    Postconditions: yields a subscription replaying transcript events after
    ``req.cursor`` and publishing live events.
    """
    record = chatstore.get_chat(req.chat_slug)
    if record is None:
        raise ChatNotFound(f"chat not found: {req.chat_slug}")

    linked_work_slug = record.chat.promoted_to_work_slug
    if linked_work_slug is None and record.chat.grounding_kind == "work":
        linked_work_slug = record.chat.grounding_ref
    linked_work = (
        workstore.get_work(linked_work_slug) if linked_work_slug is not None else None
    )
    if req.read_only or (
        linked_work is not None and linked_work.work.status != "active"
    ):
        subscription = AgentSubscription(
            queue=asyncio.Queue(maxsize=1),
            kicked=asyncio.Event(),
            replay=await asyncio.to_thread(
                lambda: list(
                    chatstore.read_transcript_from_cursor(req.chat_slug, req.cursor)
                )
            ),
        )
        try:
            yield subscription
        finally:
            subscription.kicked.set()
        return

    await _ensure_runtime(
        record,
        chatstore,
        supervisor,
        workstore,
        projectstore,
        planningfiles,
        settings,
    )

    async with supervisor.subscribe(req.chat_slug, req.cursor) as sub:
        yield sub


async def ensure_chat_runtime(
    chatstore: ChatStore,
    supervisor: AgentSupervisorService,
    workstore: WorkStore,
    projectstore: ProjectStore,
    planningfiles: PlanningFiles,
    settings: Settings,
    chat_slug: str,
    *,
    system_prompt_override: str | None = None,
) -> bool:
    """Ensure an active chat has a registered provider runtime.

    Preconditions: ``chat_slug`` identifies a stored chat.
    Postconditions: active chats are registered and their initial prompt is sent;
    archived-work chats remain read-only and return false. Callers may supply an
    opaque system prompt without adding workflow rules to this shared runtime.
    """
    record = chatstore.get_chat(chat_slug)
    if record is None:
        raise ChatNotFound(f"chat not found: {chat_slug}")
    linked_work_slug = record.chat.promoted_to_work_slug
    if linked_work_slug is None and record.chat.grounding_kind == "work":
        linked_work_slug = record.chat.grounding_ref
    linked_work = (
        workstore.get_work(linked_work_slug) if linked_work_slug is not None else None
    )
    if linked_work is not None and linked_work.work.status != "active":
        return False
    await _ensure_runtime(
        record,
        chatstore,
        supervisor,
        workstore,
        projectstore,
        planningfiles,
        settings,
        system_prompt_override=system_prompt_override,
    )
    return True


async def _ensure_runtime(
    record: ChatRecord,
    chatstore: ChatStore,
    supervisor: AgentSupervisorService,
    workstore: WorkStore,
    projectstore: ProjectStore,
    planningfiles: PlanningFiles,
    settings: Settings,
    *,
    system_prompt_override: str | None = None,
) -> None:
    chat_slug = record.chat.slug
    if chat_slug is None:
        raise ChatNotFound("chat has no slug")
    if not supervisor.is_registered(chat_slug):
        adapter, context = _build_adapter(
            record,
            workstore,
            projectstore,
            planningfiles,
            settings,
            system_prompt_override=system_prompt_override,
        )
        registered = False
        try:
            await supervisor.register_agent(
                _CHAT_WORK_SLUG,
                chat_slug,
                adapter,
                context,
                # Start discussion providers without submitting a turn so the
                # dock can expose their live model/effort/fast controls.
                lazy=not bool(record.chat.discussion_only),
            )
            registered = True
        except RuntimeError:
            with suppress(Exception):
                await adapter.close()
            if not supervisor.is_registered(chat_slug):
                raise
            await supervisor.refresh_seq_from_disk(chat_slug)
        if registered:
            await _close_interrupted_turn(chatstore, supervisor, chat_slug)

    initial = chatstore.claim_initial_prompt(chat_slug)
    if initial is not None:
        await supervisor.refresh_seq_from_disk(chat_slug)
        await supervisor.send_input(
            chat_slug,
            initial,
            record_user_input=False,
        )


async def _close_interrupted_turn(
    chatstore: ChatStore,
    supervisor: AgentSupervisorService,
    chat_slug: str,
) -> None:
    events = list(chatstore.read_transcript_from_cursor(chat_slug, 0))
    last_status = next(
        (
            event.get("status")
            for event in reversed(events)
            if event.get("type") == "status_change"
        ),
        None,
    )
    if last_status not in {"live", "thinking"}:
        return
    now = datetime.now(UTC).isoformat()
    await supervisor.publish_external_event(
        chat_slug,
        {
            "type": "error",
            "ts": now,
            "message": (
                "The previous turn was interrupted when its runtime disconnected. "
                "Send Continue to resume."
            ),
        },
    )
    await supervisor.publish_external_event(
        chat_slug,
        {"type": "status_change", "ts": now, "status": "idle"},
    )


def build_chat_runtime_config(
    record: ChatRecord,
    workstore: WorkStore,
    projectstore: ProjectStore,
    planningfiles: PlanningFiles,
    settings: Settings,
    *,
    system_prompt_override: str | None = None,
) -> tuple[AgentConfig, AgentStartContext, ChatRuntimeContext]:
    """Build provider config and prompt context for a chat.

    Preconditions: ``record`` is a stored chat record.
    Postconditions: returns provider config, launch context, and resolved
    runtime metadata without mutating chat state.
    """
    chat = record.chat
    runtime = _resolve_runtime_context(
        chat, workstore, projectstore, planningfiles, settings
    )
    system_prompt = system_prompt_override or build_prompt(
        _prompt_input_for_chat(chat, runtime)
    )
    common = CommonAgentConfig(
        workdir=runtime.agent_workdir,
        writable_roots=runtime.writable_roots,
        system_prompt=system_prompt,
    )
    config = SPECS[chat.provider].build(
        common, chat.model, _provider_options(chat, runtime)
    )
    context = AgentStartContext(
        workdir=runtime.agent_workdir,
        model=chat.model,
        system_prompt=system_prompt,
        session_id=chat.session_id,
    )
    return config, context, runtime


def provider_input_for_chat(
    chat: Chat,
    planningfiles: PlanningFiles,
    text: str,
) -> str:
    """Return provider-facing input for one visible chat user message.

    Preconditions: ``text`` is the visible message to persist in transcript.
    Postconditions: Planning revision chats receive a hidden, refreshed
    document index; other chats receive ``text`` unchanged.
    """
    planning_context = _planning_turn_context(chat, planningfiles)
    if planning_context is None:
        return text
    return (
        f"{planning_context}\n\n"
        "<user_message>\n"
        f"{text}\n"
        "</user_message>"
    )


def _build_adapter(
    record: ChatRecord,
    workstore: WorkStore,
    projectstore: ProjectStore,
    planningfiles: PlanningFiles,
    settings: Settings,
    *,
    system_prompt_override: str | None = None,
) -> tuple[AgentAdapter, AgentStartContext]:
    config, context, _runtime = build_chat_runtime_config(
        record,
        workstore,
        projectstore,
        planningfiles,
        settings,
        system_prompt_override=system_prompt_override,
    )
    adapter = build_adapter(config, settings)
    return adapter, context


def _planning_turn_context(
    chat: Chat,
    planningfiles: PlanningFiles,
) -> str | None:
    if not _is_planning_chat(chat) or not chat.grounding_ref:
        return None
    manifest = planningfiles.read_manifest(chat.grounding_ref)
    if manifest is None or manifest.get("phase") != "planned":
        return None
    source_path = planningfiles.artifact_root_path(chat.grounding_ref)
    return build_prompt(
        PlanningChatTurnContextPrompt(
            source_path=source_path,
            documents=_planning_documents(manifest),
        )
    )


@dataclass(frozen=True)
class ChatRuntimeContext:
    """Resolved filesystem/link context for a chat runtime."""

    workdir: Path
    agent_workdir: Path
    writable_roots: tuple[Path, ...]
    working_label: str
    working_details: str
    link_label: str
    link_details: str
    planning_phase: str = "discovery"
    planning_source_path: str | None = None
    planning_framework: PlanningFramework = "bmad"
    planning_profile: PlanningProfile = "feature"
    planning_documents: tuple[PlanningDocumentRef, ...] = ()


def _resolve_runtime_context(
    chat: Chat,
    workstore: WorkStore,
    projectstore: ProjectStore,
    planningfiles: PlanningFiles,
    settings: Settings,
) -> ChatRuntimeContext:
    paths = WorkspacePaths(settings.workspace_root)
    link_label, link_details = _resolve_link_context(chat, workstore, projectstore)
    working_directory = chat.working_directory
    if working_directory is None and chat.grounding_kind == "folder":
        working_directory = chat.grounding_ref

    if working_directory:
        folder = Path(working_directory).expanduser()
        if folder.exists() and folder.is_dir():
            return _with_planning_runtime(
                chat,
                planningfiles,
                ChatRuntimeContext(
                    workdir=folder,
                    agent_workdir=folder,
                    writable_roots=(folder,),
                    working_label=f"folder {folder}",
                    working_details=_join_details(
                        "The chat uses this folder as its working directory.",
                        "Inspect files only when the user asks or when it is needed "
                        "for the conversation.",
                    ),
                    link_label=link_label,
                    link_details=link_details,
                ),
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
        return _with_planning_runtime(
            chat,
            planningfiles,
            ChatRuntimeContext(
                workdir=workdir,
                agent_workdir=workdir,
                writable_roots=(workdir,),
                working_label=f"work metadata folder {workdir}",
                working_details=(
                    "No working folder was selected. This chat has no per-agent "
                    "worktree, so the cwd is the Atelier metadata folder for the "
                    "linked work."
                ),
                link_label=link_label,
                link_details=link_details,
            ),
        )

    if chat.grounding_kind == "project" and chat.grounding_ref:
        workdir = paths.project_dir(chat.grounding_ref)
        workdir.mkdir(parents=True, exist_ok=True)
        return _with_planning_runtime(
            chat,
            planningfiles,
            ChatRuntimeContext(
                workdir=workdir,
                agent_workdir=workdir,
                writable_roots=(workdir,),
                working_label=f"project metadata folder {workdir}",
                working_details=(
                    "No working folder was selected. The cwd is the Atelier metadata "
                    "folder for the linked project."
                ),
                link_label=link_label,
                link_details=link_details,
            ),
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
) -> ChatRuntimeContext:
    settings.workspace_root.mkdir(parents=True, exist_ok=True)
    return ChatRuntimeContext(
        workdir=settings.workspace_root,
        agent_workdir=settings.workspace_root,
        writable_roots=(settings.workspace_root,),
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


def _prompt_input_for_chat(
    chat: Chat, runtime: ChatRuntimeContext
) -> RegularChatRuntimePrompt | PlanningChatRuntimePrompt:
    """Return the typed prompt input for a chat runtime."""
    slug = chat.slug or "unsaved chat"
    if _is_planning_chat(chat):
        return PlanningChatRuntimePrompt(
            chat_slug=slug,
            phase=runtime.planning_phase,
            framework=runtime.planning_framework,
            profile=runtime.planning_profile,
            workdir=runtime.workdir,
            working_label=runtime.working_label,
            working_details=runtime.working_details,
            link_label=runtime.link_label,
            link_details=runtime.link_details,
            source_path=runtime.planning_source_path,
            documents=runtime.planning_documents,
        )
    return RegularChatRuntimePrompt(
        chat_slug=slug,
        title=chat.title,
        workdir=runtime.workdir,
        working_label=runtime.working_label,
        working_details=runtime.working_details,
        link_label=runtime.link_label,
        link_details=runtime.link_details,
        discussion_only=bool(chat.discussion_only),
        context_seed=chat.context_seed,
    )


def _is_planning_chat(chat: Chat) -> bool:
    """Return true for the reserved per-work Planning chat."""
    return chat.title.strip().casefold() == "planning" and chat.grounding_kind == "work"


def _provider_options(chat: Chat, runtime: ChatRuntimeContext) -> dict[str, Any]:
    """Return provider-facing options with Atelier chat metadata removed."""
    clean = dict(chat.options or {})
    clean.pop(PLANNING_READINESS_OPTION, None)
    clean.pop(PLANNING_CONFIG_OPTION, None)
    if chat.discussion_only:
        clean = _discussion_options(chat.provider, clean)
    if _is_planning_chat(chat) and runtime.planning_phase == "revision":
        return _planning_revision_options(chat.provider, clean)
    return clean


def _discussion_options(provider: Provider, options: dict[str, Any]) -> dict[str, Any]:
    """Force provider permissions to the available read-only posture."""
    next_options = dict(options)
    if provider == "amp":
        next_options.update(permission_mode="default", read_only="true")
    elif provider == "codex":
        next_options["sandbox"] = "read-only"
    elif provider in {"claude-code", "claude-acp"}:
        next_options["permission_mode"] = "plan"
    elif provider == "codex-acp":
        next_options["mode"] = "read-only"
    elif provider == "opencode":
        next_options["mode"] = "plan"
    return next_options


def _planning_revision_options(
    provider: Provider, options: dict[str, Any]
) -> dict[str, Any]:
    """Return write-capable provider options for Planning source revisions."""
    next_options = dict(options)
    if provider == "codex":
        next_options["sandbox"] = "workspace-write"
        next_options["approval_mode"] = "on-request"
    elif provider == "codex-acp":
        next_options["mode"] = "agent"
    elif provider == "claude-code":
        next_options["permission_mode"] = "default"
    elif provider == "claude-acp":
        next_options["permission_mode"] = "default"
    elif provider == "opencode":
        next_options["mode"] = "build"
    elif provider == "amp":
        next_options["permission_mode"] = "default"
    return next_options


def _with_planning_runtime(
    chat: Chat,
    planningfiles: PlanningFiles,
    runtime: ChatRuntimeContext,
) -> ChatRuntimeContext:
    """Apply Planning-chat phase metadata to a resolved runtime context."""
    if not _is_planning_chat(chat) or not chat.grounding_ref:
        return runtime
    manifest = planningfiles.read_manifest(chat.grounding_ref)
    if manifest is None:
        return runtime
    framework = _planning_framework(manifest.get("framework"))
    profile = _planning_profile(manifest.get("profile"))
    documents = _planning_documents(manifest)
    source_path = planningfiles.artifact_root_path(chat.grounding_ref)
    planning_workdir = Path(source_path)
    if manifest.get("phase") == "planned" and planning_workdir.exists():
        return ChatRuntimeContext(
            workdir=runtime.workdir,
            agent_workdir=planning_workdir,
            writable_roots=(planning_workdir,),
            working_label=runtime.working_label,
            working_details=runtime.working_details,
            link_label=runtime.link_label,
            link_details=runtime.link_details,
            planning_phase="revision",
            planning_source_path=source_path,
            planning_framework=framework,
            planning_profile=profile,
            planning_documents=documents,
        )
    return ChatRuntimeContext(
        workdir=runtime.workdir,
        agent_workdir=runtime.agent_workdir,
        writable_roots=runtime.writable_roots,
        working_label=runtime.working_label,
        working_details=runtime.working_details,
        link_label=runtime.link_label,
        link_details=runtime.link_details,
        planning_phase="discovery",
        planning_source_path=source_path,
        planning_framework=framework,
        planning_profile=profile,
        planning_documents=documents,
    )


def _planning_documents(
    manifest: dict[str, Any],
) -> tuple[PlanningDocumentRef, ...]:
    """Return the materialized plan document refs stored in the manifest."""
    raw = manifest.get("artifacts")
    if not isinstance(raw, list):
        return ()
    refs: list[PlanningDocumentRef] = []
    for item in raw:
        if not isinstance(item, dict):
            continue
        path = item.get("path")
        title = item.get("title")
        artifact_kind = item.get("artifact_kind")
        if not (
            isinstance(path, str)
            and path.strip()
            and isinstance(title, str)
            and title.strip()
            and isinstance(artifact_kind, str)
            and artifact_kind.strip()
        ):
            continue
        refs.append(
            PlanningDocumentRef(
                path=path.strip(),
                title=title.strip(),
                artifact_kind=artifact_kind.strip(),
                executable=item.get("executable") is True,
            )
        )
    return tuple(refs)


def _planning_framework(value: object) -> PlanningFramework:
    if value in {"bmad", "spec", "openspec", "custom"}:
        return value  # type: ignore[return-value]
    return "bmad"


def _planning_profile(value: object) -> PlanningProfile:
    if value in {
        "feature",
        "refactor",
        "migration",
        "bugfix",
        "hotfix",
        "full_app",
        "custom",
    }:
        return value  # type: ignore[return-value]
    return "feature"


__all__ = [
    "ChatNotFound",
    "ChatRuntimeContext",
    "ConnectChatRuntimeRequest",
    "build_chat_runtime_config",
    "connect_chat",
    "ensure_chat_runtime",
    "provider_input_for_chat",
]
