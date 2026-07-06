"""Works REST router.

Thin endpoints — parse the pydantic request, build the domain DTO, hand
off to the matching command, format the result. No business logic here;
that lives behind the WorkStore port.
"""

import subprocess
from datetime import UTC, datetime
from pathlib import Path
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Request, status

from src.application.http.schemas import (
    ArtifactSummary,
    CompleteWorkResponse,
    ContextSchema,
    HandoffSummary,
    MoveWorkRequest,
    NewHandoffRequest,
    NewWorkRequest,
    PatchWorkRequest,
    WorkChatContextDocResponse,
    WorkChatContextFolderSummary,
    WorkChatRef,
    WorkDetail,
    WorkSummary,
)
from src.domain.agents.handoffs import (
    BuildHandoffRequest,
    Summarizer,
    build_handoff,
)
from src.domain.chatstore import ChatStore
from src.domain.commands.projects import get as projects_get
from src.domain.commands.works import (
    complete,
    create,
    delete,
    get,
    list_all,
    list_artifacts,
    move_to_project,
    update,
)
from src.domain.commands.works.list_artifacts import ArtifactView
from src.domain.models import Context, Handoff, Work
from src.domain.projectstore.ports import ProjectStore
from src.domain.sharedfolders.ports import SharedFolderStore
from src.domain.supervisor import AgentSupervisorService
from src.domain.workstore.dtos import (
    CreateWorkChatContextFolder,
    CreateWorkRequest,
    UpdateWorkRequest,
    WorkChatProvenance,
    WorkRecord,
)
from src.domain.workstore.ports import TranscriptLog, WorkStore
from src.domain.worktrees import WorktreeManager
from src.infrastructure.filesystem.paths import WorkspacePaths
from src.infrastructure.filesystem.reveal import open_in_file_browser
from src.settings import Settings

router = APIRouter()


def get_workstore(request: Request) -> WorkStore:
    """FastAPI dependency: pull the WorkStore off the app state set up in lifespan."""
    return request.app.state.workstore  # type: ignore[no-any-return]


def get_projectstore(request: Request) -> ProjectStore:
    return request.app.state.projectstore  # type: ignore[no-any-return]


def get_settings_dep(request: Request) -> Settings:
    return request.app.state.settings  # type: ignore[no-any-return]


def get_supervisor(request: Request) -> AgentSupervisorService:
    return request.app.state.supervisor  # type: ignore[no-any-return]


def get_chatstore(request: Request) -> ChatStore:
    return request.app.state.chatstore  # type: ignore[no-any-return]


def get_chat_supervisor(request: Request) -> AgentSupervisorService:
    return request.app.state.chat_supervisor  # type: ignore[no-any-return]


def get_worktree_manager(request: Request) -> WorktreeManager:
    return request.app.state.worktree_manager  # type: ignore[no-any-return]


def get_summarizer(request: Request) -> Summarizer:
    return request.app.state.summarizer  # type: ignore[no-any-return]


def get_transcript_log(request: Request) -> TranscriptLog:
    return request.app.state.transcript_log  # type: ignore[no-any-return]


def get_sharestore(request: Request) -> SharedFolderStore:
    return request.app.state.sharestore  # type: ignore[no-any-return]


WorkStoreDep = Annotated[WorkStore, Depends(get_workstore)]
ProjectStoreDep = Annotated[ProjectStore, Depends(get_projectstore)]
SettingsDep = Annotated[Settings, Depends(get_settings_dep)]
SupervisorDep = Annotated[AgentSupervisorService, Depends(get_supervisor)]
ChatStoreDep = Annotated[ChatStore, Depends(get_chatstore)]
ChatSupervisorDep = Annotated[AgentSupervisorService, Depends(get_chat_supervisor)]
WorktreeDep = Annotated[WorktreeManager, Depends(get_worktree_manager)]
SummarizerDep = Annotated[Summarizer, Depends(get_summarizer)]
TranscriptLogDep = Annotated[TranscriptLog, Depends(get_transcript_log)]
ShareStoreDep = Annotated[SharedFolderStore, Depends(get_sharestore)]


@router.get("/works", response_model=list[WorkSummary])
def list_works_endpoint(
    workstore: WorkStoreDep, settings: SettingsDep
) -> list[WorkSummary]:
    works = list_all.execute(workstore)
    counts = workstore.count_children_by_work_id()
    paths = WorkspacePaths(workspace_root=settings.workspace_root)
    return [
        _to_summary(w, paths, counts.get(w.id) if w.id is not None else None)
        for w in works
    ]


@router.post("/works", response_model=WorkDetail, status_code=status.HTTP_201_CREATED)
def create_work_endpoint(
    payload: NewWorkRequest,
    workstore: WorkStoreDep,
    projectstore: ProjectStoreDep,
    settings: SettingsDep,
) -> WorkDetail:
    if payload.project_slug is not None:
        if projects_get.execute(projectstore, payload.project_slug) is None:
            raise HTTPException(
                status.HTTP_422_UNPROCESSABLE_ENTITY,
                detail=f"project not found: {payload.project_slug}",
            )
    record = create.execute(workstore, _to_create_request(payload))
    return _to_detail(record, WorkspacePaths(workspace_root=settings.workspace_root))


@router.get("/works/{work_slug}", response_model=WorkDetail)
def get_work_endpoint(
    work_slug: str, workstore: WorkStoreDep, settings: SettingsDep
) -> WorkDetail:
    record = get.execute(workstore, work_slug)
    if record is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail=f"work not found: {work_slug}")
    return _to_detail(record, WorkspacePaths(workspace_root=settings.workspace_root))


@router.get(
    "/works/{work_slug}/artifacts",
    response_model=list[ArtifactSummary],
)
def list_work_artifacts_endpoint(
    work_slug: str,
    workstore: WorkStoreDep,
    sharestore: ShareStoreDep,
    settings: SettingsDep,
) -> list[ArtifactSummary]:
    paths = WorkspacePaths(workspace_root=settings.workspace_root)

    def _resolve_worktree(work: str, agent: str) -> Path | None:
        candidate = paths.worktree_dir(work, agent)
        return candidate if candidate.exists() else None

    def _resolve_share_roots(project_slug: str) -> list[Path]:
        roots: list[Path] = []
        for share in sharestore.list_for_project(project_slug):
            if share.real_path is not None:
                roots.append(share.real_path)
            elif share.slug is not None:
                roots.append(paths.project_share_dir(project_slug, share.slug))
        return roots

    try:
        views = list_artifacts.execute(
            workstore=workstore,
            work_slug=work_slug,
            resolve_worktree=_resolve_worktree,
            resolve_share_roots=_resolve_share_roots,
        )
    except ValueError as e:
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail=str(e)) from e
    return [_view_to_summary(v) for v in views]


@router.post(
    "/works/{work_slug}/handoffs",
    response_model=HandoffSummary,
    status_code=status.HTTP_201_CREATED,
)
def create_handoff_endpoint(
    work_slug: str,
    payload: NewHandoffRequest,
    workstore: WorkStoreDep,
    transcript_log: TranscriptLogDep,
    summarizer: SummarizerDep,
) -> HandoffSummary:
    """Generate a handoff doc summarizing the source agent's recent
    transcript. v1: target is always "new-agent" (the FE pre-fills the
    NewAgentDialog with the doc text). The summarizer is synchronous —
    the route blocks for the duration of the LLM call (typically a few
    seconds; 60s timeout)."""
    try:
        handoff = build_handoff(
            BuildHandoffRequest(
                work_slug=work_slug,
                source_agent_slug=payload.source_agent_slug,
            ),
            workstore=workstore,
            transcript_log=transcript_log,
            summarizer=summarizer,
            clock=lambda: datetime.now(UTC),
        )
    except ValueError as e:
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail=str(e)) from e
    agents = workstore.list_agents_for_work(work_slug)
    agent_slug_by_id = {a.id: a.slug for a in agents if a.id is not None}
    return _to_handoff_summary(
        handoff, agent_slug_by_id, source_slug=payload.source_agent_slug
    )


@router.get(
    "/works/{work_slug}/handoffs",
    response_model=list[HandoffSummary],
)
def list_work_handoffs_endpoint(
    work_slug: str, workstore: WorkStoreDep
) -> list[HandoffSummary]:
    try:
        handoffs = workstore.list_handoffs_for_work(work_slug)
    except ValueError as e:
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail=str(e)) from e
    agents = workstore.list_agents_for_work(work_slug)
    agent_slug_by_id = {a.id: a.slug for a in agents if a.id is not None}
    return [
        _to_handoff_summary(h, agent_slug_by_id) for h in handoffs
    ]


@router.patch("/works/{work_slug}", response_model=WorkDetail)
def patch_work_endpoint(
    work_slug: str,
    payload: PatchWorkRequest,
    workstore: WorkStoreDep,
    settings: SettingsDep,
) -> WorkDetail:
    try:
        record = update.execute(workstore, _to_update_request(work_slug, payload))
    except ValueError as e:
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail=str(e)) from e
    return _to_detail(record, WorkspacePaths(workspace_root=settings.workspace_root))


@router.delete("/works/{work_slug}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_work_endpoint(
    work_slug: str,
    workstore: WorkStoreDep,
    chatstore: ChatStoreDep,
    supervisor: SupervisorDep,
    chat_supervisor: ChatSupervisorDep,
    worktree_manager: WorktreeDep,
) -> None:
    try:
        await delete.execute(
            workstore,
            chatstore,
            supervisor,
            chat_supervisor,
            worktree_manager,
            delete.DeleteWorkRequest(work_slug=work_slug),
        )
    except delete.WorkNotFound as e:
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail=str(e)) from e


@router.post("/works/{work_slug}/project", response_model=WorkDetail)
def move_work_to_project_endpoint(
    work_slug: str,
    payload: MoveWorkRequest,
    workstore: WorkStoreDep,
    projectstore: ProjectStoreDep,
    settings: SettingsDep,
) -> WorkDetail:
    """Re-parent a work to a different project, or to Loose
    (``project_slug: null``). Validates the target project exists when
    one is supplied; missing target → 422. Returns the updated detail."""
    try:
        record = move_to_project.execute(
            workstore,
            projectstore,
            move_to_project.MoveWorkToProjectRequest(
                work_slug=work_slug, project_slug=payload.project_slug
            ),
        )
    except move_to_project.WorkNotFound as e:
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail=str(e)) from e
    except move_to_project.ProjectNotFound as e:
        raise HTTPException(
            status.HTTP_422_UNPROCESSABLE_ENTITY, detail=str(e)
        ) from e
    return _to_detail(record, WorkspacePaths(workspace_root=settings.workspace_root))


@router.post("/works/{work_slug}/complete", response_model=CompleteWorkResponse)
async def complete_work_endpoint(
    work_slug: str,
    workstore: WorkStoreDep,
    supervisor: SupervisorDep,
    worktree_manager: WorktreeDep,
) -> CompleteWorkResponse:
    """Mark a Work as completed: stop running agents, remove their git
    worktrees, flip the Work's status to ``completed``. Transcripts and
    the work folder under ``~/Atelier/works/<slug>/`` are preserved."""
    try:
        result = await complete.execute(
            workstore,
            supervisor,
            worktree_manager,
            complete.CompleteWorkRequest(work_slug=work_slug),
        )
    except complete.WorkNotFound as e:
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail=str(e)) from e
    except complete.WorkNotActive as e:
        raise HTTPException(status.HTTP_409_CONFLICT, detail=str(e)) from e
    return CompleteWorkResponse(
        work_slug=result.work_slug, agent_count=result.agent_count
    )


@router.post("/works/{work_slug}/reveal", status_code=status.HTTP_204_NO_CONTENT)
def reveal_work_endpoint(
    work_slug: str, workstore: WorkStoreDep, settings: SettingsDep
) -> None:
    """Open the work's atelier folder in the OS file browser. Slug → path
    is server-computed (defends against arbitrary path injection) and the
    work must exist (so we don't pop a Finder window for a typo)."""
    record = get.execute(workstore, work_slug)
    if record is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail=f"work not found: {work_slug}")
    paths = WorkspacePaths(workspace_root=settings.workspace_root)
    target = paths.work_dir(work_slug)
    target.mkdir(parents=True, exist_ok=True)
    try:
        open_in_file_browser(str(target))
    except (OSError, subprocess.SubprocessError) as exc:
        raise HTTPException(
            status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"reveal failed: {exc}",
        ) from exc


@router.get(
    "/works/{work_slug}/chat-contexts/{folder_name}/{filename}",
    response_model=WorkChatContextDocResponse,
)
def get_work_chat_context_doc_endpoint(
    work_slug: str,
    folder_name: str,
    filename: str,
    workstore: WorkStoreDep,
) -> WorkChatContextDocResponse:
    try:
        result = workstore.read_work_chat_context_doc(
            work_slug, folder_name, filename
        )
    except ValueError as e:
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail=str(e)) from e
    if result is None:
        raise HTTPException(
            status.HTTP_404_NOT_FOUND,
            detail=f"context doc not found: {work_slug}/{folder_name}/{filename}",
        )
    path, content = result
    return WorkChatContextDocResponse(path=path, content=content)


# ---------------------------------------------------------------------------
# Translators between pydantic schemas and domain DTOs/entities
# ---------------------------------------------------------------------------


def _to_create_request(payload: NewWorkRequest) -> CreateWorkRequest:
    return CreateWorkRequest(
        name=payload.name,
        description=payload.description,
        contexts=[_to_domain_context(c) for c in payload.contexts],
        project_slug=payload.project_slug,
        from_chat=(
            WorkChatProvenance(
                chat_slug=payload.from_chat.slug,
                chat_title=payload.from_chat.title,
            )
            if payload.from_chat is not None
            else None
        ),
        chat_context_folders=[
            CreateWorkChatContextFolder(
                name=f.name,
                mount_path=f.mount_path,
                chat_slug=f.chat_slug,
                chat_title=f.chat_title,
                context_markdown=f.context_markdown,
                context_filename=f.context_filename,
            )
            for f in payload.chat_context_folders
        ],
    )


def _to_update_request(work_slug: str, payload: PatchWorkRequest) -> UpdateWorkRequest:
    return UpdateWorkRequest(
        work_slug=work_slug,
        name=payload.name,
        description=payload.description,
        status=payload.status,
        contexts=(
            [_to_domain_context(c) for c in payload.contexts]
            if payload.contexts is not None
            else None
        ),
    )


def _to_domain_context(c: ContextSchema) -> Context:
    return Context(type=c.type, value=c.value, conn_id=c.conn_id)


def _to_schema_context(c: Context) -> ContextSchema:
    return ContextSchema(type=c.type, value=c.value, conn_id=c.conn_id)


def _to_summary(
    work: Work,
    paths: WorkspacePaths,
    counts: dict[str, int] | None = None,
) -> WorkSummary:
    slug = _require_slug(work)
    return WorkSummary(
        slug=slug,
        name=work.name,
        description=work.description,
        status=work.status,
        created_at=work.created_at,
        atelier_path=str(paths.work_dir(slug)),
        project_slug=work.project_slug,
        agent_count=(counts or {}).get("agents", 0),
        artifact_count=(counts or {}).get("artifacts", 0),
        from_chat=(
            WorkChatRef(slug=work.from_chat_slug, title=work.from_chat_title or work.from_chat_slug)
            if work.from_chat_slug is not None
            else None
        ),
    )


def _to_detail(record: WorkRecord, paths: WorkspacePaths) -> WorkDetail:
    summary = _to_summary(record.work, paths)
    return WorkDetail(
        **summary.model_dump(),
        contexts=[_to_schema_context(c) for c in record.contexts],
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


def _to_handoff_summary(
    handoff: Handoff,
    agent_slug_by_id: dict[int, str | None],
    *,
    source_slug: str | None = None,
) -> HandoffSummary:
    if handoff.slug is None:
        raise RuntimeError("persisted Handoff has no slug")
    # Source slug is always known by id; the create endpoint passes the
    # request's source_agent_slug too because the agent might have been
    # deleted by the time we resolve (defensive — the workstore would
    # have errored earlier if the source were truly missing).
    resolved_source = (
        agent_slug_by_id.get(handoff.source_agent_id) or source_slug or ""
    )
    target_slug = (
        agent_slug_by_id.get(handoff.target_agent_id)
        if handoff.target_agent_id is not None
        else None
    )
    doc_text = ""
    try:
        doc_text = handoff.doc_path.read_text()
    except OSError:
        # Doc missing on disk is an integrity issue but the row is still
        # surfacable; FE just sees an empty body.
        pass
    return HandoffSummary(
        slug=handoff.slug,
        source_agent_slug=resolved_source,
        doc_path=str(handoff.doc_path),
        doc_text=doc_text,
        created_at=handoff.created_at,
        target_agent_slug=target_slug,
        target_dialog=handoff.target_dialog,
    )


def _view_to_summary(view: ArtifactView) -> ArtifactSummary:
    """Trivial DTO copy — all enrichment already happened in the query
    command. Kept as a function (not a model_validator) so the wire
    schema stays a pure pydantic concern."""
    return ArtifactSummary(
        slug=view.slug,
        type=view.type,
        title=view.title,
        status=view.status,
        created_at=view.created_at,
        agent_slug=view.agent_slug,
        url=view.url,
        repo=view.repo,
        doc_path=view.doc_path,
        location_kind=view.location_kind,
    )


def _require_slug(work: Work) -> str:
    if work.slug is None:
        raise RuntimeError("persisted Work has no slug")
    return work.slug
