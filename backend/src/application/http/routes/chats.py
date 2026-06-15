"""Exploratory chat REST router."""

from __future__ import annotations

import re
from datetime import UTC, datetime
from pathlib import Path
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Query, Request, status

from src.application.http.schemas import (
    ChatCompactionSummaryResponse,
    ChatDetail,
    ChatGroundingSchema,
    ChatMessageSchema,
    ChatSummary,
    CompactChatRequest,
    CompactChatResponse,
    NewChatRequest,
    PatchChatRequest,
    PromoteChatRequest,
    SendChatMessageRequest,
    WorkChatContextFolderSummary,
    WorkChatRef,
    WorkDetail,
)
from src.domain.agents import SPECS, CommonAgentConfig
from src.domain.agents.compactions import CompactionSessionClient
from src.domain.agents.handoffs import Summarizer
from src.domain.chatstore import (
    AppendChatMessageRequest,
    ChatGrounding,
    ChatRecord,
    ChatStore,
    CreateChatRequest,
)
from src.domain.commands.chats import compact, delete, read_compaction_summary, rename
from src.domain.commands.projects import get as projects_get
from src.domain.commands.works import create as works_create
from src.domain.models import Chat, ChatMessage
from src.domain.projectstore.ports import ProjectStore
from src.domain.supervisor import AgentSupervisorService
from src.domain.workstore.dtos import (
    CreateWorkChatContextFolder,
    CreateWorkRequest,
    EnsureWorkChatContextRequest,
    WorkChatContextFolder,
    WorkChatProvenance,
    WorkRecord,
)
from src.domain.workstore.ports import WorkStore
from src.infrastructure.filesystem.paths import WorkspacePaths
from src.settings import Settings

router = APIRouter()


def get_chatstore(request: Request) -> ChatStore:
    return request.app.state.chatstore  # type: ignore[no-any-return]


def get_workstore(request: Request) -> WorkStore:
    return request.app.state.workstore  # type: ignore[no-any-return]


def get_projectstore(request: Request) -> ProjectStore:
    return request.app.state.projectstore  # type: ignore[no-any-return]


def get_settings_dep(request: Request) -> Settings:
    return request.app.state.settings  # type: ignore[no-any-return]


def get_chat_supervisor(request: Request) -> AgentSupervisorService:
    return request.app.state.chat_supervisor  # type: ignore[no-any-return]


def get_summarizer(request: Request) -> Summarizer:
    return request.app.state.summarizer  # type: ignore[no-any-return]


def get_compaction_session_client(request: Request) -> CompactionSessionClient:
    return request.app.state.compaction_session_client  # type: ignore[no-any-return]


ChatStoreDep = Annotated[ChatStore, Depends(get_chatstore)]
WorkStoreDep = Annotated[WorkStore, Depends(get_workstore)]
ProjectStoreDep = Annotated[ProjectStore, Depends(get_projectstore)]
SettingsDep = Annotated[Settings, Depends(get_settings_dep)]
ChatSupervisorDep = Annotated[AgentSupervisorService, Depends(get_chat_supervisor)]
SummarizerDep = Annotated[Summarizer, Depends(get_summarizer)]
CompactionSessionClientDep = Annotated[
    CompactionSessionClient, Depends(get_compaction_session_client)
]


@router.get("/chats", response_model=list[ChatSummary])
def list_chats_endpoint(
    chatstore: ChatStoreDep,
    project_slug: str | None = Query(default=None),
    work_slug: str | None = Query(default=None),
) -> list[ChatSummary]:
    records = chatstore.list_chats()
    records = [
        r
        for r in records
        if _matches_scope(
            r.chat,
            project_slug=project_slug,
            work_slug=work_slug,
        )
    ]
    return [_to_summary(r) for r in records]


@router.post("/chats", response_model=ChatDetail, status_code=status.HTTP_201_CREATED)
def create_chat_endpoint(
    payload: NewChatRequest,
    chatstore: ChatStoreDep,
    settings: SettingsDep,
) -> ChatDetail:
    try:
        _validate_chat_provider_config(payload, settings)
    except ValueError as e:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, detail=str(e)) from e
    record = chatstore.create_chat(
        CreateChatRequest(
            provider=payload.provider,
            model=payload.model,
            first_message=payload.first_message,
            title=payload.title,
            grounding=(
                ChatGrounding(
                    kind=payload.grounding.kind,
                    ref=payload.grounding.ref,
                )
                if payload.grounding is not None
                else None
            ),
            working_directory=payload.working_directory,
            options=payload.options or None,
        )
    )
    return _to_detail(record)


@router.get("/chats/{chat_slug}", response_model=ChatDetail)
def get_chat_endpoint(chat_slug: str, chatstore: ChatStoreDep) -> ChatDetail:
    record = chatstore.get_chat(chat_slug)
    if record is None:
        raise HTTPException(
            status.HTTP_404_NOT_FOUND, detail=f"chat not found: {chat_slug}"
        )
    return _to_detail(record)


@router.patch("/chats/{chat_slug}", response_model=ChatDetail)
def patch_chat_endpoint(
    chat_slug: str,
    payload: PatchChatRequest,
    chatstore: ChatStoreDep,
) -> ChatDetail:
    record = chatstore.get_chat(chat_slug)
    if record is None:
        raise HTTPException(
            status.HTTP_404_NOT_FOUND, detail=f"chat not found: {chat_slug}"
        )
    if payload.title is not None:
        try:
            record = rename.execute(
                chatstore,
                rename.RenameChatRequest(chat_slug=chat_slug, title=payload.title),
            )
        except rename.ChatNotFound as e:
            raise HTTPException(status.HTTP_404_NOT_FOUND, detail=str(e)) from e
    return _to_detail(record)


@router.delete("/chats/{chat_slug}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_chat_endpoint(
    chat_slug: str,
    chatstore: ChatStoreDep,
    supervisor: ChatSupervisorDep,
) -> None:
    try:
        await delete.execute(
            chatstore,
            supervisor,
            delete.DeleteChatRequest(chat_slug=chat_slug),
        )
    except delete.ChatNotFound as e:
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail=str(e)) from e


@router.post("/chats/{chat_slug}/compact", response_model=CompactChatResponse)
async def compact_chat_endpoint(
    chat_slug: str,
    payload: CompactChatRequest,
    chatstore: ChatStoreDep,
    supervisor: ChatSupervisorDep,
    workstore: WorkStoreDep,
    projectstore: ProjectStoreDep,
    settings: SettingsDep,
    summarizer: SummarizerDep,
    session_client: CompactionSessionClientDep,
) -> CompactChatResponse:
    try:
        result = await compact.execute(
            chatstore,
            supervisor,
            workstore,
            projectstore,
            settings,
            summarizer,
            session_client,
            compact.CompactChatRequest(
                chat_slug=chat_slug,
                reason=payload.reason,
            ),
        )
    except compact.ChatNotFound as e:
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail=str(e)) from e
    except compact.ChatNotCompactable as e:
        raise HTTPException(status.HTTP_409_CONFLICT, detail=str(e)) from e
    except compact.ChatBusy as e:
        raise HTTPException(status.HTTP_409_CONFLICT, detail=str(e)) from e
    return CompactChatResponse(
        chat_slug=result.chat_slug,
        provider=result.provider,
        old_session_id=result.old_session_id,
        new_session_id=result.new_session_id,
        summary_path=result.summary_path,
        breadcrumb_written=result.breadcrumb_written,
        breadcrumb_error=result.breadcrumb_error,
    )


@router.get(
    "/chats/{chat_slug}/compactions/{filename}",
    response_model=ChatCompactionSummaryResponse,
)
def get_chat_compaction_summary_endpoint(
    chat_slug: str,
    filename: str,
    chatstore: ChatStoreDep,
) -> ChatCompactionSummaryResponse:
    try:
        result = read_compaction_summary.execute(
            chatstore,
            read_compaction_summary.ReadChatCompactionSummaryRequest(
                chat_slug=chat_slug,
                filename=filename,
            ),
        )
    except read_compaction_summary.ChatNotFound as e:
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail=str(e)) from e
    except read_compaction_summary.CompactionSummaryNotFound as e:
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail=str(e)) from e
    except ValueError as e:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, detail=str(e)) from e
    return ChatCompactionSummaryResponse(
        chat_slug=result.chat_slug,
        filename=result.filename,
        summary_path=result.summary_path,
        content=result.content,
    )


@router.post("/chats/{chat_slug}/messages", response_model=ChatDetail)
def send_chat_message_endpoint(
    chat_slug: str,
    payload: SendChatMessageRequest,
    chatstore: ChatStoreDep,
) -> ChatDetail:
    try:
        chatstore.append_message(
            AppendChatMessageRequest(
                chat_slug=chat_slug,
                role="user",
                body=payload.body,
            )
        )
        record = chatstore.append_message(
            AppendChatMessageRequest(
                chat_slug=chat_slug,
                role="assistant",
                body=_structural_reply(payload.body),
            )
        )
    except ValueError as e:
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail=str(e)) from e
    return _to_detail(record)


@router.post("/chats/{chat_slug}/promote", response_model=WorkDetail)
def promote_chat_endpoint(
    chat_slug: str,
    payload: PromoteChatRequest,
    chatstore: ChatStoreDep,
    workstore: WorkStoreDep,
    projectstore: ProjectStoreDep,
    settings: SettingsDep,
) -> WorkDetail:
    record = chatstore.get_chat(chat_slug)
    if record is None:
        raise HTTPException(
            status.HTTP_404_NOT_FOUND, detail=f"chat not found: {chat_slug}"
        )
    if record.chat.promoted_to_work_slug is not None:
        raise HTTPException(
            status.HTTP_409_CONFLICT,
            detail=f"chat already promoted to {record.chat.promoted_to_work_slug}",
        )
    if payload.project_slug is not None:
        if projects_get.execute(projectstore, payload.project_slug) is None:
            raise HTTPException(
                status.HTTP_422_UNPROCESSABLE_ENTITY,
                detail=f"project not found: {payload.project_slug}",
            )
    context_md = _build_context_markdown(
        record,
        payload.description,
        workstore=workstore,
        projectstore=projectstore,
    )
    folder_name = f"{chat_slug.lower()}-context"
    work_record = works_create.execute(
        workstore,
        CreateWorkRequest(
            name=payload.name,
            description=payload.description,
            project_slug=payload.project_slug,
            from_chat=WorkChatProvenance(
                chat_slug=chat_slug,
                chat_title=record.chat.title,
            ),
            chat_context_folders=[
                CreateWorkChatContextFolder(
                    name=folder_name,
                    mount_path=folder_name,
                    chat_slug=chat_slug,
                    chat_title=record.chat.title,
                    context_markdown=context_md,
                )
            ],
        ),
    )
    chatstore.mark_promoted(chat_slug, _require_work_slug(work_record))
    return _work_to_detail(
        work_record, WorkspacePaths(workspace_root=settings.workspace_root)
    )


@router.post(
    "/works/{work_slug}/chats/{chat_slug}/context",
    response_model=WorkChatContextFolderSummary,
)
def ensure_work_chat_context_endpoint(
    work_slug: str,
    chat_slug: str,
    chatstore: ChatStoreDep,
    workstore: WorkStoreDep,
    projectstore: ProjectStoreDep,
) -> WorkChatContextFolderSummary:
    record = chatstore.get_chat(chat_slug)
    if record is None:
        raise HTTPException(
            status.HTTP_404_NOT_FOUND, detail=f"chat not found: {chat_slug}"
        )
    if workstore.get_work(work_slug) is None:
        raise HTTPException(
            status.HTTP_404_NOT_FOUND, detail=f"work not found: {work_slug}"
        )

    context_md = _build_context_markdown(
        record,
        _summarize_chat_for_context(record),
        workstore=workstore,
        projectstore=projectstore,
    )
    folder_name = f"{chat_slug.lower()}-context"
    updated = workstore.ensure_work_chat_context(
        EnsureWorkChatContextRequest(
            work_slug=work_slug,
            folder=CreateWorkChatContextFolder(
                name=folder_name,
                mount_path=folder_name,
                chat_slug=chat_slug,
                chat_title=record.chat.title,
                context_markdown=context_md,
            ),
        )
    )
    folder = next(
        (f for f in updated.chat_context_folders if f.chat_slug == chat_slug),
        None,
    )
    if folder is None:
        raise HTTPException(
            status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"context folder not available for chat: {chat_slug}",
        )
    return _folder_to_summary(folder)


def _matches_scope(
    chat: Chat,
    *,
    project_slug: str | None,
    work_slug: str | None,
) -> bool:
    if work_slug is not None:
        return (chat.grounding_kind == "work" and chat.grounding_ref == work_slug) or (
            chat.promoted_to_work_slug == work_slug
        )
    if project_slug is not None:
        return (
            chat.promoted_to_work_slug is None
            and chat.grounding_kind == "project"
            and chat.grounding_ref == project_slug
        )
    return chat.promoted_to_work_slug is None and chat.grounding_kind in (None, "folder")


def _structural_reply(text: str) -> str:
    lower = text.lower()
    if any(word in lower for word in ("work", "ticket", "build", "ship", "implement")):
        return (
            "This sounds close to execution. I can keep exploring tradeoffs here, "
            "or you can start work from this chat and carry the summary forward."
        )
    if "?" in text:
        return (
            "There are a couple of paths to compare: the faster option that "
            "reduces ceremony, and the more deliberate option that buys clearer "
            "risk control. Which constraint matters more here?"
        )
    return (
        "Following. I can turn this into options, risks, and next actions "
        "when it starts to firm up."
    )


def _build_context_markdown(
    record: ChatRecord,
    brief: str,
    *,
    workstore: WorkStore,
    projectstore: ProjectStore,
) -> str:
    stamp = datetime.now(UTC).date().isoformat()
    chat = record.chat
    action_items = _extract_action_items(brief)
    grounding = _grounding_label(chat, workstore, projectstore)
    working_directory = _working_directory_label(chat)
    lines = [
        "# Context",
        "",
        f"Seeded from exploratory chat **{_require_slug(chat)}** on {stamp}.",
        "",
        "## Summary",
        brief.strip(),
        "",
        "## Action items",
    ]
    if action_items:
        lines.extend(f"- {item}" for item in action_items)
    else:
        lines.append("- No explicit action items were captured in the promotion brief.")
    lines.extend(
        [
            "",
            "## Source conversation",
            f'- Chat: {_require_slug(chat)} - "{chat.title}"',
            f"- Model: {chat.model} ({chat.provider})",
            f"- Linked to: {grounding}",
            f"- Working folder: {working_directory}",
            f"- Messages: {len(record.transcript)}",
            f"- Full transcript: chat://{_require_slug(chat)}",
            "",
            "> Open the linked chat to read the full back-and-forth that led here.",
        ]
    )
    return "\n".join(lines)


def _summarize_chat_for_context(record: ChatRecord) -> str:
    first = next((m.body for m in record.transcript if m.role == "user"), "")
    last = next(
        (m.body for m in reversed(record.transcript) if m.role == "assistant"),
        "",
    )
    lines = [f'Carried over from chat {_require_slug(record.chat)} - "{record.chat.title}".']
    if first:
        lines.extend(["", _trim_plain(first, 240)])
    if last:
        lines.extend(["", f"Where we landed: {_trim_plain(last, 280)}"])
    return "\n".join(lines)


def _trim_plain(text: str, limit: int) -> str:
    plain = " ".join(text.replace("**", "").replace("`", "").split())
    if len(plain) <= limit:
        return plain
    return plain[: limit - 3].rsplit(" ", 1)[0] + "..."


def _extract_action_items(brief: str) -> list[str]:
    out: list[str] = []
    for line in brief.splitlines():
        stripped = line.strip()
        match = re.match(r"^(?:[-*]|\d+\.)\s+(?:\[[ xX]\]\s*)?(.*)$", stripped)
        if match and match.group(1).strip():
            out.append(match.group(1).strip())
    return out


def _grounding_label(
    chat: Chat, workstore: WorkStore, projectstore: ProjectStore
) -> str:
    if chat.grounding_kind is None or not chat.grounding_ref:
        return "nothing (open exploration)"
    if chat.grounding_kind == "folder":
        return "nothing (open exploration)"
    if chat.grounding_kind == "project":
        project = projects_get.execute(projectstore, chat.grounding_ref)
        return project.project.name if project is not None else chat.grounding_ref
    if chat.grounding_kind == "work":
        record = workstore.get_work(chat.grounding_ref)
        return record.work.name if record is not None else chat.grounding_ref
    return chat.grounding_ref


def _working_directory_label(chat: Chat) -> str:
    if chat.working_directory:
        return chat.working_directory
    if chat.grounding_kind == "folder" and chat.grounding_ref:
        return chat.grounding_ref
    return "default"


def _to_summary(record: ChatRecord) -> ChatSummary:
    chat = record.chat
    return ChatSummary(
        slug=_require_slug(chat),
        title=chat.title,
        provider=chat.provider,
        model=chat.model,
        grounding=(
            ChatGroundingSchema(kind=chat.grounding_kind, ref=chat.grounding_ref)
            if chat.grounding_kind is not None and chat.grounding_ref is not None
            else None
        ),
        working_directory=chat.working_directory,
        created_at=chat.created_at,
        updated_at=chat.updated_at,
        promoted_to_work_slug=chat.promoted_to_work_slug,
        message_count=len(record.transcript),
    )


def _to_detail(record: ChatRecord) -> ChatDetail:
    summary = _to_summary(record)
    return ChatDetail(
        **summary.model_dump(),
        transcript=[_message_to_schema(m) for m in record.transcript],
    )


def _validate_chat_provider_config(payload: NewChatRequest, settings: Settings) -> None:
    workdir = (
        Path(payload.working_directory).expanduser()
        if payload.working_directory
        else settings.workspace_root
    )
    SPECS[payload.provider].build(
        CommonAgentConfig(
            workdir=workdir,
            writable_roots=(workdir,),
            system_prompt="",
        ),
        payload.model,
        payload.options,
    )


def _message_to_schema(message: ChatMessage) -> ChatMessageSchema:
    return ChatMessageSchema(
        role=message.role,
        body=message.body,
        created_at=message.created_at,
    )


def _work_to_detail(record: WorkRecord, paths: WorkspacePaths) -> WorkDetail:
    work = record.work
    slug = _require_work_slug(record)
    return WorkDetail(
        slug=slug,
        name=work.name,
        description=work.description,
        status=work.status,
        created_at=work.created_at,
        atelier_path=str(paths.work_dir(slug)),
        project_slug=work.project_slug,
        agent_count=0,
        artifact_count=0,
        from_chat=(
            WorkChatRef(
                slug=work.from_chat_slug,
                title=work.from_chat_title or work.from_chat_slug,
            )
            if work.from_chat_slug is not None
            else None
        ),
        contexts=[],
        chat_context_folders=[
            WorkChatContextFolderSummary(
                name=f.name,
                mount_path=f.mount_path,
                chat_slug=f.chat_slug,
                chat_title=f.chat_title,
                context_filename=f.context_filename,
                absolute_path=str(f.absolute_path) if f.absolute_path else "",
            )
            for f in record.chat_context_folders
        ],
    )


def _folder_to_summary(folder: WorkChatContextFolder) -> WorkChatContextFolderSummary:
    return WorkChatContextFolderSummary(
        name=folder.name,
        mount_path=folder.mount_path,
        chat_slug=folder.chat_slug,
        chat_title=folder.chat_title,
        context_filename=folder.context_filename,
        absolute_path=str(folder.absolute_path) if folder.absolute_path else "",
    )


def _require_slug(chat: Chat) -> str:
    if chat.slug is None:
        raise RuntimeError("persisted Chat has no slug")
    return chat.slug


def _require_work_slug(record: WorkRecord) -> str:
    if record.work.slug is None:
        raise RuntimeError("persisted Work has no slug")
    return record.work.slug
