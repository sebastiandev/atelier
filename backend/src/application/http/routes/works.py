"""Works REST router.

Thin endpoints — parse the pydantic request, build the domain DTO, hand
off to the matching command, format the result. No business logic here;
that lives behind the WorkStore port.
"""

import asyncio
import logging
import subprocess
from dataclasses import replace
from datetime import UTC, datetime
from pathlib import Path
from typing import Annotated, Any

from fastapi import APIRouter, Depends, HTTPException, Request, status

from src.application.http.schemas import (
    AcceptPlanArtifactRequest,
    ArtifactSummary,
    ChatDetail,
    ChatGroundingSchema,
    ChatMessageSchema,
    CompleteWorkPreviewResponse,
    CompleteWorkRequestBody,
    CompleteWorkResponse,
    CompletionWorkspaceResponse,
    ContextSchema,
    CreatePlanArtifactProposalRequest,
    CreatePlanBugRequest,
    CreatePrStageRequest,
    HandoffSummary,
    LinkPlanArtifactTrackingRequest,
    LoopBriefSchema,
    MoveWorkRequest,
    NewHandoffRequest,
    NewWorkRequest,
    PatchWorkRequest,
    PlanArtifactDetailResponse,
    PlanArtifactProposalResponse,
    PlanArtifactResponse,
    PlanArtifactRunResponse,
    PlanLoopChangedFileResponse,
    PlanLoopStageRunResponse,
    PlanMaterializationStatusResponse,
    PlanningChatReadinessResponse,
    PlanningFrameworkStatusRequest,
    PlanningFrameworkStatusResponse,
    PlanOverviewResponse,
    PlanTrackingLinkResponse,
    RequestPlanRunChangesRequest,
    RerunWorkLoopRunRequest,
    ResolvePlanMaterializationPermissionRequest,
    ResumePlanArtifactRunRequest,
    SendPrFeedbackRequest,
    StartPlanArtifactRunRequest,
    StartPlanningChatRequest,
    StartPlanningSetupChatRequest,
    StartWorkLoopRunRequest,
    StartWorkPlanRequest,
    StartWorkPlanResponse,
    UpdatePlanArtifactRequest,
    WorkChatContextDocResponse,
    WorkChatContextFolderSummary,
    WorkChatRef,
    WorkDetail,
    WorkLoopRunResponse,
    WorkPlanResponse,
    WorkSummary,
)
from src.domain.agents.handoffs import (
    BuildHandoffRequest,
    Summarizer,
    build_handoff,
)
from src.domain.agents.ports import AgentAdapterFactory
from src.domain.chatstore import ChatRecord, ChatStore
from src.domain.commands.loops import runs as loop_run_commands
from src.domain.commands.planning import (
    accept_run as planning_accept_run,
)
from src.domain.commands.planning import (
    approve as planning_approve,
)
from src.domain.commands.planning import cancel_run as planning_cancel_run
from src.domain.commands.planning import (
    create_bug as planning_create_bug,
)
from src.domain.commands.planning import (
    finish as planning_finish,
)
from src.domain.commands.planning import (
    get as planning_get,
)
from src.domain.commands.planning import (
    get_run as planning_get_run,
)
from src.domain.commands.planning import (
    link_tracking as planning_link_tracking,
)
from src.domain.commands.planning import (
    list_runs as planning_list_runs,
)
from src.domain.commands.planning import (
    mark_run_cleaned as planning_mark_run_cleaned,
)
from src.domain.commands.planning import (
    materialization_chat as planning_materialization_chat,
)
from src.domain.commands.planning import (
    materialization_status as planning_materialization_status,
)
from src.domain.commands.planning import (
    materialize as planning_materialize,
)
from src.domain.commands.planning import pr_runs as planning_pr_runs
from src.domain.commands.planning import (
    propose_update as planning_propose_update,
)
from src.domain.commands.planning import (
    request_run_changes as planning_request_run_changes,
)
from src.domain.commands.planning import rerun_run as planning_rerun_run
from src.domain.commands.planning import (
    resolve_materialization_permission as planning_resolve_materialization_permission,
)
from src.domain.commands.planning import (
    resolve_proposal as planning_resolve_proposal,
)
from src.domain.commands.planning import (
    resume_run as planning_resume_run,
)
from src.domain.commands.planning import (
    run_monitor as planning_run_monitor,
)
from src.domain.commands.planning import (
    setup_chat as planning_setup_chat,
)
from src.domain.commands.planning import (
    start_chat as planning_start_chat,
)
from src.domain.commands.planning import (
    start_run as planning_start_run,
)
from src.domain.commands.planning import (
    submit_materialization as planning_submit_materialization,
)
from src.domain.commands.planning import (
    update_artifact as planning_update_artifact,
)
from src.domain.commands.projects import get as projects_get
from src.domain.commands.works import (
    complete,
    create,
    get,
    list_all,
    list_artifacts,
    move_to_project,
    soft_delete,
    update,
)
from src.domain.commands.works.list_artifacts import ArtifactView
from src.domain.connections import ConnectionStore
from src.domain.loop import actions as loop_actions
from src.domain.loop import followups, lifecycle, pr_lifecycle, pr_review
from src.domain.loop.briefs import brief_snapshot, optional_brief_from_snapshot
from src.domain.loop.dtos import (
    LoopBrief,
    LoopBriefAgent,
    LoopBriefContext,
    LoopReviewGateMode,
    LoopRunKind,
    LoopStageBrief,
    LoopStatus,
)
from src.domain.loop.models import LoopRunRecord
from src.domain.loop.ports import (
    LoopCheckRunner,
    LoopContextResolver,
    LoopDefinitionLocations,
    LoopDefinitionRepository,
    LoopRunRepository,
)
from src.domain.loop.snapshots import definition_snapshot
from src.domain.models import Chat, ChatMessage, Context, Handoff, Work
from src.domain.planning.dtos import (
    PlanArtifactDetail,
    PlanArtifactProposal,
    PlanArtifactRun,
    PlanArtifactSummary,
    PlanningFrameworkStatus,
    PlanOverview,
    PlanRunStatus,
    PlanTrackingLink,
    WorkPlanView,
)
from src.domain.planning.frameworks import check_framework_status
from src.domain.planning.ports import PlanningFiles, PlanningSessionRepository
from src.domain.planning.readiness import (
    PlanningChatReadiness,
    planning_readiness_from_record,
)
from src.domain.projectstore.ports import ProjectStore
from src.domain.sharedfolders.ports import SharedFolderStore, ShareProvisioner
from src.domain.supervisor import AgentSupervisorService, AgentTerminated
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
_log = logging.getLogger(__name__)


def get_workstore(request: Request) -> WorkStore:
    """FastAPI dependency: pull the WorkStore off the app state set up in lifespan."""
    return request.app.state.workstore  # type: ignore[no-any-return]


def get_projectstore(request: Request) -> ProjectStore:
    return request.app.state.projectstore  # type: ignore[no-any-return]


def get_planningfiles(request: Request) -> PlanningFiles:
    return request.app.state.planningfiles  # type: ignore[no-any-return]


def get_planning_sessions(request: Request) -> PlanningSessionRepository:
    return request.app.state.planning_sessions  # type: ignore[no-any-return]


def get_loop_definitions(request: Request) -> LoopDefinitionRepository:
    return request.app.state.loop_definitions  # type: ignore[no-any-return]


def get_loop_definition_locations(request: Request) -> LoopDefinitionLocations:
    return request.app.state.workspace_paths  # type: ignore[no-any-return]


def get_connection_store(request: Request) -> ConnectionStore:
    return request.app.state.connection_store  # type: ignore[no-any-return]


def get_agent_adapter_factory(request: Request) -> AgentAdapterFactory:
    return request.app.state.agent_adapter_factory  # type: ignore[no-any-return]


def get_loop_check_runner(request: Request) -> LoopCheckRunner:
    return request.app.state.loop_check_runner  # type: ignore[no-any-return]


def get_loop_run_repository(request: Request) -> LoopRunRepository:
    return request.app.state.loop_runs  # type: ignore[no-any-return]


def get_loop_context_resolver(request: Request) -> LoopContextResolver:
    return request.app.state.loop_context_resolver  # type: ignore[no-any-return]


def get_chatstore(request: Request) -> ChatStore:
    return request.app.state.chatstore  # type: ignore[no-any-return]


def get_settings_dep(request: Request) -> Settings:
    return request.app.state.settings  # type: ignore[no-any-return]


def get_supervisor(request: Request) -> AgentSupervisorService:
    return request.app.state.supervisor  # type: ignore[no-any-return]


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


def get_share_provisioner(request: Request) -> ShareProvisioner:
    return request.app.state.share_provisioner  # type: ignore[no-any-return]


WorkStoreDep = Annotated[WorkStore, Depends(get_workstore)]
ProjectStoreDep = Annotated[ProjectStore, Depends(get_projectstore)]
PlanningFilesDep = Annotated[PlanningFiles, Depends(get_planningfiles)]
PlanningSessionsDep = Annotated[PlanningSessionRepository, Depends(get_planning_sessions)]
LoopDefinitionsDep = Annotated[LoopDefinitionRepository, Depends(get_loop_definitions)]
LoopDefinitionLocationsDep = Annotated[
    LoopDefinitionLocations, Depends(get_loop_definition_locations)
]
ConnectionStoreDep = Annotated[ConnectionStore, Depends(get_connection_store)]
AgentAdapterFactoryDep = Annotated[AgentAdapterFactory, Depends(get_agent_adapter_factory)]
LoopCheckRunnerDep = Annotated[LoopCheckRunner, Depends(get_loop_check_runner)]
LoopRunRepositoryDep = Annotated[LoopRunRepository, Depends(get_loop_run_repository)]
LoopContextResolverDep = Annotated[LoopContextResolver, Depends(get_loop_context_resolver)]
ChatStoreDep = Annotated[ChatStore, Depends(get_chatstore)]
SettingsDep = Annotated[Settings, Depends(get_settings_dep)]
SupervisorDep = Annotated[AgentSupervisorService, Depends(get_supervisor)]
ChatSupervisorDep = Annotated[AgentSupervisorService, Depends(get_chat_supervisor)]
WorktreeDep = Annotated[WorktreeManager, Depends(get_worktree_manager)]
SummarizerDep = Annotated[Summarizer, Depends(get_summarizer)]
TranscriptLogDep = Annotated[TranscriptLog, Depends(get_transcript_log)]
ShareStoreDep = Annotated[SharedFolderStore, Depends(get_sharestore)]
ShareProvisionerDep = Annotated[ShareProvisioner, Depends(get_share_provisioner)]


@router.get("/works", response_model=list[WorkSummary])
def list_works_endpoint(workstore: WorkStoreDep, settings: SettingsDep) -> list[WorkSummary]:
    works = list_all.execute(workstore)
    counts = workstore.count_children_by_work_id()
    paths = WorkspacePaths(workspace_root=settings.workspace_root)
    return [_to_summary(w, paths, counts.get(w.id) if w.id is not None else None) for w in works]


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
def get_work_endpoint(work_slug: str, workstore: WorkStoreDep, settings: SettingsDep) -> WorkDetail:
    record = get.execute(workstore, work_slug)
    if record is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail=f"work not found: {work_slug}")
    return _to_detail(record, WorkspacePaths(workspace_root=settings.workspace_root))


@router.get("/works/{work_slug}/runs", response_model=list[WorkLoopRunResponse])
def list_work_loop_runs_endpoint(
    work_slug: str,
    loop_runs: LoopRunRepositoryDep,
) -> list[WorkLoopRunResponse]:
    """List durable standalone Loop runs for one Work."""
    return [
        _to_work_loop_run(record)
        for record in loop_run_commands.list_runs(loop_runs, work_slug)
    ]


@router.get("/works/{work_slug}/loop-brief", response_model=LoopBriefSchema | None)
def get_work_loop_brief_endpoint(
    work_slug: str,
    workstore: WorkStoreDep,
) -> LoopBriefSchema | None:
    """Return the latest editable Loop brief saved on one Work."""
    try:
        brief = loop_run_commands.get_work_brief(workstore, work_slug)
    except loop_run_commands.WorkNotFound as exc:
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail=str(exc)) from exc
    return _to_loop_brief_schema(brief) if brief is not None else None


@router.put("/works/{work_slug}/loop-brief", response_model=LoopBriefSchema)
def save_work_loop_brief_endpoint(
    work_slug: str,
    payload: LoopBriefSchema,
    workstore: WorkStoreDep,
) -> LoopBriefSchema:
    """Save task input on the Work without changing its loop definition."""
    try:
        brief = loop_run_commands.save_work_brief(
            workstore,
            work_slug,
            _to_loop_brief(payload),
        )
    except loop_run_commands.WorkNotFound as exc:
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, detail=str(exc)) from exc
    return _to_loop_brief_schema(brief)


@router.get("/works/{work_slug}/runs/{run_id}", response_model=WorkLoopRunResponse)
def get_work_loop_run_endpoint(
    work_slug: str,
    run_id: str,
    loop_runs: LoopRunRepositoryDep,
) -> WorkLoopRunResponse:
    """Return one durable standalone Loop run."""
    try:
        record = loop_run_commands.get_run(
            loop_runs,
            loop_run_commands.LoopRunRequest(work_slug, run_id),
        )
    except loop_run_commands.RunNotFound as exc:
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail=str(exc)) from exc
    return _to_work_loop_run(record)


@router.post(
    "/works/{work_slug}/runs/{run_id}/pr-refresh",
    response_model=WorkLoopRunResponse,
)
async def refresh_work_loop_pr_endpoint(
    request: Request,
    work_slug: str,
    run_id: str,
    workstore: WorkStoreDep,
    loop_runs: LoopRunRepositoryDep,
    force: bool = False,
) -> WorkLoopRunResponse:
    """Synchronize one standalone run's pull-request lifecycle."""
    poller = getattr(request.app.state, "pr_status_poller", None)
    gateway = poller.lifecycle_gateway() if poller is not None else None
    if gateway is None:
        raise HTTPException(
            status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="pull-request lifecycle service is unavailable",
        )
    try:
        record = await loop_run_commands.refresh_pr(
            workstore,
            loop_runs,
            gateway,
            loop_run_commands.RefreshPrRequest(
                work_slug=work_slug,
                run_id=run_id,
                force=force,
            ),
        )
    except (loop_run_commands.RunNotFound, loop_run_commands.WorkNotFound) as exc:
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail=str(exc)) from exc
    except pr_review.PrReviewUnavailable as exc:
        raise HTTPException(status.HTTP_502_BAD_GATEWAY, detail=str(exc)) from exc
    return _to_work_loop_run(record)


@router.post(
    "/works/{work_slug}/runs",
    response_model=WorkLoopRunResponse,
    status_code=status.HTTP_201_CREATED,
)
async def start_work_loop_run_endpoint(
    request: Request,
    work_slug: str,
    payload: StartWorkLoopRunRequest,
    workstore: WorkStoreDep,
    loop_definitions: LoopDefinitionsDep,
    loop_locations: LoopDefinitionLocationsDep,
    loop_runs: LoopRunRepositoryDep,
    context_resolver: LoopContextResolverDep,
    supervisor: SupervisorDep,
    worktree_manager: WorktreeDep,
    connection_store: ConnectionStoreDep,
    sharestore: ShareStoreDep,
    share_provisioner: ShareProvisionerDep,
    adapter_factory: AgentAdapterFactoryDep,
    check_runner: LoopCheckRunnerDep,
    settings: SettingsDep,
) -> WorkLoopRunResponse:
    """Start a standalone Loop run and schedule its monitor."""
    try:
        record = await loop_run_commands.start_run(
            workstore,
            loop_definitions,
            loop_locations,
            loop_runs,
            context_resolver,
            supervisor,
            worktree_manager,
            connection_store,
            sharestore,
            share_provisioner,
            adapter_factory,
            settings,
            loop_run_commands.StartLoopRunRequest(
                work_slug=work_slug,
                goal=payload.goal,
                root_path=payload.root_path,
                loop_definition_id=payload.loop_definition_id,
                loop_revision=payload.loop_revision,
                provider=payload.provider,
                model=payload.model,
                options=dict(payload.options),
                brief=_to_loop_brief(payload.brief) if payload.brief is not None else None,
                source_run_id=payload.source_run_id,
            ),
        )
    except (
        loop_run_commands.WorkNotFound,
        loop_run_commands.RunNotFound,
        loop_run_commands.LoopDefinitionNotFound,
    ) as exc:
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail=str(exc)) from exc
    except loop_run_commands.LoopDefinitionConflict as exc:
        raise HTTPException(status.HTTP_409_CONFLICT, detail=str(exc)) from exc
    except loop_run_commands.WorkNotActive as exc:
        raise HTTPException(status.HTTP_409_CONFLICT, detail=str(exc)) from exc
    except (
        loop_run_commands.AgentFolderMissing,
        loop_run_commands.InvalidProviderConfig,
        loop_run_commands.LoopContextMissing,
        loop_run_commands.LoopDefinitionInvalid,
        ValueError,
    ) as exc:
        raise HTTPException(
            status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail=str(exc),
        ) from exc
    except AgentTerminated as exc:
        raise HTTPException(status.HTTP_409_CONFLICT, detail=str(exc)) from exc
    _ensure_loop_run_monitor_task(
        request,
        workstore,
        supervisor,
        worktree_manager,
        connection_store,
        sharestore,
        share_provisioner,
        adapter_factory,
        check_runner,
        loop_runs,
        settings,
        loop_run_commands.LoopRunRequest(work_slug, str(record.state["id"])),
    )
    return _to_work_loop_run(record)


@router.post(
    "/works/{work_slug}/runs/{run_id}/resume",
    response_model=WorkLoopRunResponse,
)
async def resume_work_loop_run_endpoint(
    request: Request,
    work_slug: str,
    run_id: str,
    payload: ResumePlanArtifactRunRequest,
    workstore: WorkStoreDep,
    loop_runs: LoopRunRepositoryDep,
    supervisor: SupervisorDep,
    worktree_manager: WorktreeDep,
    connection_store: ConnectionStoreDep,
    sharestore: ShareStoreDep,
    share_provisioner: ShareProvisionerDep,
    adapter_factory: AgentAdapterFactoryDep,
    check_runner: LoopCheckRunnerDep,
    settings: SettingsDep,
) -> WorkLoopRunResponse:
    """Resume one blocker-paused standalone Loop run."""
    try:
        record = await loop_run_commands.resume_run(
            workstore,
            loop_runs,
            supervisor,
            worktree_manager,
            connection_store,
            sharestore,
            share_provisioner,
            adapter_factory,
            settings,
            loop_run_commands.ResumeLoopRunRequest(
                work_slug,
                run_id,
                payload.resolution_note,
                gate_decision=payload.gate_decision,
                enforced_findings=tuple(payload.enforced_findings),
            ),
        )
    except loop_run_commands.RunNotFound as exc:
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(
            status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail=str(exc),
        ) from exc
    _ensure_loop_run_monitor_task(
        request,
        workstore,
        supervisor,
        worktree_manager,
        connection_store,
        sharestore,
        share_provisioner,
        adapter_factory,
        check_runner,
        loop_runs,
        settings,
        loop_run_commands.LoopRunRequest(work_slug, run_id),
    )
    return _to_work_loop_run(record)


@router.post(
    "/works/{work_slug}/runs/{run_id}/retry-stage",
    response_model=WorkLoopRunResponse,
)
async def retry_work_loop_run_stage_endpoint(
    request: Request,
    work_slug: str,
    run_id: str,
    workstore: WorkStoreDep,
    loop_runs: LoopRunRepositoryDep,
    supervisor: SupervisorDep,
    worktree_manager: WorktreeDep,
    connection_store: ConnectionStoreDep,
    sharestore: ShareStoreDep,
    share_provisioner: ShareProvisionerDep,
    adapter_factory: AgentAdapterFactoryDep,
    check_runner: LoopCheckRunnerDep,
    settings: SettingsDep,
) -> WorkLoopRunResponse:
    """Retry one failed standalone Loop stage in place."""
    try:
        record = await loop_run_commands.retry_stage(
            workstore,
            loop_runs,
            supervisor,
            worktree_manager,
            connection_store,
            sharestore,
            share_provisioner,
            adapter_factory,
            settings,
            loop_run_commands.LoopRunRequest(work_slug, run_id),
        )
    except loop_run_commands.RunNotFound as exc:
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(
            status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail=str(exc),
        ) from exc
    except AgentTerminated as exc:
        raise HTTPException(status.HTTP_409_CONFLICT, detail=str(exc)) from exc
    _ensure_loop_run_monitor_task(
        request,
        workstore,
        supervisor,
        worktree_manager,
        connection_store,
        sharestore,
        share_provisioner,
        adapter_factory,
        check_runner,
        loop_runs,
        settings,
        loop_run_commands.LoopRunRequest(work_slug, run_id),
    )
    return _to_work_loop_run(record)


@router.post(
    "/works/{work_slug}/runs/{run_id}/request-changes",
    response_model=WorkLoopRunResponse,
)
async def request_work_loop_run_changes_endpoint(
    request: Request,
    work_slug: str,
    run_id: str,
    payload: RequestPlanRunChangesRequest,
    workstore: WorkStoreDep,
    loop_runs: LoopRunRepositoryDep,
    supervisor: SupervisorDep,
    worktree_manager: WorktreeDep,
    connection_store: ConnectionStoreDep,
    sharestore: ShareStoreDep,
    share_provisioner: ShareProvisionerDep,
    adapter_factory: AgentAdapterFactoryDep,
    check_runner: LoopCheckRunnerDep,
    settings: SettingsDep,
) -> WorkLoopRunResponse:
    """Return a sourceless loop run to its configured write stage."""
    try:
        record = await loop_run_commands.request_changes(
            workstore,
            loop_runs,
            supervisor,
            worktree_manager,
            sharestore,
            share_provisioner,
            settings,
            loop_run_commands.RequestChangesRequest(
                work_slug,
                run_id,
                payload.note,
            ),
        )
    except loop_run_commands.RunNotFound as exc:
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(
            status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail=str(exc),
        ) from exc
    _ensure_loop_run_monitor_task(
        request,
        workstore,
        supervisor,
        worktree_manager,
        connection_store,
        sharestore,
        share_provisioner,
        adapter_factory,
        check_runner,
        loop_runs,
        settings,
        loop_run_commands.LoopRunRequest(work_slug, run_id),
    )
    return _to_work_loop_run(record)


@router.post(
    "/works/{work_slug}/runs/{run_id}/accept",
    response_model=WorkLoopRunResponse,
)
async def accept_work_loop_run_endpoint(
    request: Request,
    work_slug: str,
    run_id: str,
    workstore: WorkStoreDep,
    loop_runs: LoopRunRepositoryDep,
    supervisor: SupervisorDep,
    worktree_manager: WorktreeDep,
    connection_store: ConnectionStoreDep,
    sharestore: ShareStoreDep,
    share_provisioner: ShareProvisionerDep,
    adapter_factory: AgentAdapterFactoryDep,
    check_runner: LoopCheckRunnerDep,
    settings: SettingsDep,
) -> WorkLoopRunResponse:
    """Accept one completed standalone Loop run."""
    try:
        record = await loop_run_commands.accept_run(
            workstore,
            loop_runs,
            supervisor,
            loop_run_commands.LoopRunRequest(work_slug, run_id),
        )
    except loop_run_commands.RunNotFound as exc:
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(
            status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail=str(exc),
        ) from exc
    if record.status not in {LoopStatus.ACCEPTED, LoopStatus.CLEANED}:
        _ensure_loop_run_monitor_task(
            request,
            workstore,
            supervisor,
            worktree_manager,
            connection_store,
            sharestore,
            share_provisioner,
            adapter_factory,
            check_runner,
            loop_runs,
            settings,
            loop_run_commands.LoopRunRequest(work_slug, run_id),
        )
    return _to_work_loop_run(record)


@router.post(
    "/works/{work_slug}/runs/{run_id}/create-pr",
    response_model=WorkLoopRunResponse,
)
async def create_work_loop_pr_stage_endpoint(
    request: Request,
    work_slug: str,
    run_id: str,
    payload: CreatePrStageRequest,
    workstore: WorkStoreDep,
    loop_runs: LoopRunRepositoryDep,
    supervisor: SupervisorDep,
    worktree_manager: WorktreeDep,
    connection_store: ConnectionStoreDep,
    sharestore: ShareStoreDep,
    share_provisioner: ShareProvisionerDep,
    adapter_factory: AgentAdapterFactoryDep,
    check_runner: LoopCheckRunnerDep,
    settings: SettingsDep,
) -> WorkLoopRunResponse:
    """Add and launch a Work-local Create PR stage."""
    try:
        record = loop_run_commands.create_pr_stage(
            workstore,
            loop_runs,
            loop_run_commands.CreatePrRequest(
                work_slug=work_slug,
                run_id=run_id,
                setup=pr_lifecycle.PrSetup(**payload.model_dump()),
            ),
        )
    except (loop_run_commands.RunNotFound, loop_run_commands.WorkNotFound) as exc:
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(
            status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail=str(exc),
        ) from exc
    _ensure_loop_run_monitor_task(
        request,
        workstore,
        supervisor,
        worktree_manager,
        connection_store,
        sharestore,
        share_provisioner,
        adapter_factory,
        check_runner,
        loop_runs,
        settings,
        loop_run_commands.LoopRunRequest(work_slug, run_id),
    )
    return _to_work_loop_run(record)


@router.post(
    "/works/{work_slug}/runs/{run_id}/pr-feedback",
    response_model=WorkLoopRunResponse,
)
async def send_work_loop_pr_feedback_endpoint(
    request: Request,
    work_slug: str,
    run_id: str,
    payload: SendPrFeedbackRequest,
    workstore: WorkStoreDep,
    loop_runs: LoopRunRepositoryDep,
    supervisor: SupervisorDep,
    worktree_manager: WorktreeDep,
    connection_store: ConnectionStoreDep,
    sharestore: ShareStoreDep,
    share_provisioner: ShareProvisionerDep,
    adapter_factory: AgentAdapterFactoryDep,
    check_runner: LoopCheckRunnerDep,
    settings: SettingsDep,
) -> WorkLoopRunResponse:
    """Start another pass from selected pull-request feedback."""
    try:
        record = loop_run_commands.send_pr_feedback(
            workstore,
            loop_runs,
            loop_run_commands.SendPrFeedbackRequest(
                work_slug=work_slug,
                run_id=run_id,
                comments=tuple(
                    pr_lifecycle.PrFeedbackItem(
                        comment_id=item.comment_id,
                        instruction=item.instruction,
                    )
                    for item in payload.comments
                ),
                instruction=payload.instruction,
            ),
        )
    except (loop_run_commands.RunNotFound, loop_run_commands.WorkNotFound) as exc:
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(
            status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail=str(exc),
        ) from exc
    _ensure_loop_run_monitor_task(
        request,
        workstore,
        supervisor,
        worktree_manager,
        connection_store,
        sharestore,
        share_provisioner,
        adapter_factory,
        check_runner,
        loop_runs,
        settings,
        loop_run_commands.LoopRunRequest(work_slug, run_id),
    )
    return _to_work_loop_run(record)


@router.post(
    "/works/{work_slug}/runs/{run_id}/cancel",
    response_model=WorkLoopRunResponse,
)
async def cancel_work_loop_run_endpoint(
    work_slug: str,
    run_id: str,
    workstore: WorkStoreDep,
    loop_runs: LoopRunRepositoryDep,
    supervisor: SupervisorDep,
) -> WorkLoopRunResponse:
    """Cancel one active standalone Loop run."""
    try:
        record = await loop_run_commands.cancel_run(
            workstore,
            loop_runs,
            supervisor,
            loop_run_commands.LoopRunRequest(work_slug, run_id),
        )
    except loop_run_commands.RunNotFound as exc:
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(
            status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail=str(exc),
        ) from exc
    return _to_work_loop_run(record)


@router.post(
    "/works/{work_slug}/runs/{run_id}/cleanup",
    response_model=WorkLoopRunResponse,
)
async def cleanup_work_loop_run_endpoint(
    work_slug: str,
    run_id: str,
    workstore: WorkStoreDep,
    loop_runs: LoopRunRepositoryDep,
    supervisor: SupervisorDep,
) -> WorkLoopRunResponse:
    """Deprecated compatibility alias that releases provider runtimes."""
    try:
        record = await loop_run_commands.clean_run(
            workstore,
            loop_runs,
            supervisor,
            loop_run_commands.LoopRunRequest(work_slug, run_id),
        )
    except loop_run_commands.RunNotFound as exc:
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(
            status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail=str(exc),
        ) from exc
    return _to_work_loop_run(record)


@router.post(
    "/works/{work_slug}/runs/{run_id}/rerun",
    response_model=WorkLoopRunResponse,
    status_code=status.HTTP_201_CREATED,
)
async def rerun_work_loop_run_endpoint(
    request: Request,
    work_slug: str,
    run_id: str,
    workstore: WorkStoreDep,
    loop_definitions: LoopDefinitionsDep,
    loop_locations: LoopDefinitionLocationsDep,
    loop_runs: LoopRunRepositoryDep,
    context_resolver: LoopContextResolverDep,
    supervisor: SupervisorDep,
    worktree_manager: WorktreeDep,
    connection_store: ConnectionStoreDep,
    sharestore: ShareStoreDep,
    share_provisioner: ShareProvisionerDep,
    adapter_factory: AgentAdapterFactoryDep,
    check_runner: LoopCheckRunnerDep,
    settings: SettingsDep,
    payload: RerunWorkLoopRunRequest | None = None,
) -> WorkLoopRunResponse:
    """Start another run from a terminal run's kept workspace."""
    try:
        record = await loop_run_commands.rerun(
            workstore,
            loop_definitions,
            loop_locations,
            loop_runs,
            context_resolver,
            supervisor,
            worktree_manager,
            connection_store,
            sharestore,
            share_provisioner,
            adapter_factory,
            settings,
            loop_run_commands.RerunLoopRunRequest(
                work_slug,
                run_id,
                LoopRunKind(payload.kind) if payload is not None else LoopRunKind.INITIAL,
                payload.note if payload is not None else "",
            ),
        )
    except loop_run_commands.RunNotFound as exc:
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail=str(exc)) from exc
    except loop_run_commands.LoopDefinitionConflict as exc:
        raise HTTPException(status.HTTP_409_CONFLICT, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(
            status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail=str(exc),
        ) from exc
    except AgentTerminated as exc:
        raise HTTPException(status.HTTP_409_CONFLICT, detail=str(exc)) from exc
    new_run_id = str(record.state["id"])
    _ensure_loop_run_monitor_task(
        request,
        workstore,
        supervisor,
        worktree_manager,
        connection_store,
        sharestore,
        share_provisioner,
        adapter_factory,
        check_runner,
        loop_runs,
        settings,
        loop_run_commands.LoopRunRequest(work_slug, new_run_id),
    )
    return _to_work_loop_run(record)


@router.post(
    "/works/{work_slug}/plan/framework-status",
    response_model=PlanningFrameworkStatusResponse,
)
def planning_framework_status_endpoint(
    work_slug: str,
    payload: PlanningFrameworkStatusRequest,
    workstore: WorkStoreDep,
) -> PlanningFrameworkStatusResponse:
    if workstore.get_work(work_slug) is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail=f"work not found: {work_slug}")
    return _to_framework_status(check_framework_status(payload.framework, payload.root_path))


@router.post("/works/{work_slug}/planning-chat", response_model=ChatDetail)
def start_planning_chat_endpoint(
    work_slug: str,
    payload: StartPlanningChatRequest,
    workstore: WorkStoreDep,
    chatstore: ChatStoreDep,
    planning_sessions: PlanningSessionsDep,
) -> ChatDetail:
    req = planning_start_chat.StartPlanningChatRequest(
        work_slug=work_slug,
        root_path=payload.root_path,
        idea=payload.idea,
        artifact_root_path=payload.artifact_root_path,
        framework=payload.framework,
        profile=payload.profile,
        provider=payload.provider,
        model=payload.model,
        options=payload.options,
    )
    try:
        planning_start_chat.validate_provider_config(req)
        record, _framework_status = planning_start_chat.execute(
            workstore, chatstore, planning_sessions, req
        )
    except planning_start_chat.WorkNotFound as e:
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail=str(e)) from e
    except planning_start_chat.PlanningFrameworkNotReady as e:
        raise HTTPException(status.HTTP_409_CONFLICT, detail=str(e)) from e
    except ValueError as e:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, detail=str(e)) from e
    return _to_chat_detail(record)


@router.post("/works/{work_slug}/planning-setup-chat", response_model=ChatDetail)
def start_planning_setup_chat_endpoint(
    work_slug: str,
    payload: StartPlanningSetupChatRequest,
    workstore: WorkStoreDep,
    chatstore: ChatStoreDep,
) -> ChatDetail:
    req = planning_setup_chat.StartPlanningSetupChatRequest(
        work_slug=work_slug,
        root_path=payload.root_path,
        framework=payload.framework,
        profile=payload.profile,
        provider=payload.provider,
        model=payload.model,
        options=payload.options,
    )
    try:
        planning_setup_chat.validate_provider_config(req)
        record, _framework_status = planning_setup_chat.execute(workstore, chatstore, req)
    except planning_setup_chat.WorkNotFound as e:
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail=str(e)) from e
    except planning_setup_chat.PlanningFrameworkAlreadyReady as e:
        raise HTTPException(status.HTTP_409_CONFLICT, detail=str(e)) from e
    except ValueError as e:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, detail=str(e)) from e
    return _to_chat_detail(record)


@router.post("/works/{work_slug}/plan", response_model=StartWorkPlanResponse)
async def start_work_plan_endpoint(
    work_slug: str,
    payload: StartWorkPlanRequest,
    request: Request,
    workstore: WorkStoreDep,
    chatstore: ChatStoreDep,
    projectstore: ProjectStoreDep,
    planningfiles: PlanningFilesDep,
    loop_runs: LoopRunRepositoryDep,
    planning_sessions: PlanningSessionsDep,
    chat_supervisor: ChatSupervisorDep,
    settings: SettingsDep,
) -> StartWorkPlanResponse:
    req = planning_materialize.MaterializePlanRequest(
        work_slug=work_slug,
        root_path=payload.root_path,
        artifact_root_path=payload.artifact_root_path,
        framework=payload.framework,
        profile=payload.profile,
        provider=payload.provider,
        model=payload.model,
        options=payload.options,
        planning_chat_slug=payload.planning_chat_slug,
    )
    try:
        req = planning_materialize.resolve_from_planning_session(planning_sessions, req)
    except planning_materialize.PlanningSessionNotFound as e:
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail=str(e)) from e
    if (
        req.root_path is None
        or req.framework is None
        or req.profile is None
        or req.provider is None
        or req.model is None
    ):
        raise HTTPException(
            status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="planning session is incomplete",
        )
    chat_req = planning_materialization_chat.StartPlanningMaterializationChatRequest(
        work_slug=work_slug,
        root_path=req.root_path,
        artifact_root_path=req.artifact_root_path,
        framework=req.framework,
        profile=req.profile,
        provider=req.provider,
        model=req.model,
        options=req.options,
        planning_chat_slug=req.planning_chat_slug,
    )
    try:
        planning_materialization_chat.validate_provider_config(chat_req)
        record, _status = planning_materialization_chat.execute(workstore, chatstore, chat_req)
        if record.chat.slug is None:
            raise ValueError("materialization chat has no slug")
        plan = await planning_materialize.try_finalize_existing(
            workstore,
            chatstore,
            planningfiles,
            loop_runs,
            chat_supervisor,
            req,
            record.chat.slug,
        )
        if plan is not None:
            return StartWorkPlanResponse(
                plan=_to_plan_response(plan),
                materialization_status=_to_plan_materialization_status_response(
                    planning_materialization_status.execute(
                        workstore, chatstore, planningfiles, loop_runs, work_slug
                    )
                ),
            )
        materialization_view = planning_materialization_status.execute(
            workstore, chatstore, planningfiles, loop_runs, work_slug
        )
        await _ensure_plan_materialization_task(
            request,
            workstore,
            chatstore,
            projectstore,
            planningfiles,
            loop_runs,
            planning_sessions,
            chat_supervisor,
            settings,
            req,
            replace_existing=materialization_view.state in {"stalled", "failed"},
        )
        return _to_start_work_plan_response(
            workstore, chatstore, planningfiles, loop_runs, work_slug
        )
    except (
        planning_submit_materialization.WorkNotFound,
        planning_materialization_chat.WorkNotFound,
    ) as e:
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail=str(e)) from e
    except (
        planning_submit_materialization.PlanningFrameworkNotReady,
        planning_materialization_chat.PlanningFrameworkNotReady,
    ) as e:
        raise HTTPException(status.HTTP_409_CONFLICT, detail=str(e)) from e
    except (
        planning_submit_materialization.InvalidPlanMaterialization,
        ValueError,
    ) as e:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, detail=str(e)) from e


async def _ensure_plan_materialization_task(
    request: Request,
    workstore: WorkStore,
    chatstore: ChatStore,
    projectstore: ProjectStore,
    planningfiles: PlanningFiles,
    loop_runs: LoopRunRepository,
    planning_sessions: PlanningSessionRepository,
    chat_supervisor: AgentSupervisorService,
    settings: Settings,
    req: planning_materialize.MaterializePlanRequest,
    *,
    replace_existing: bool,
) -> None:
    """Schedule materialization, replacing a stalled task on explicit retry."""
    key = f"{req.work_slug}:{req.root_path}"
    tasks = getattr(request.app.state, "planning_materialization_tasks", None)
    if tasks is None:
        tasks = {}
        request.app.state.planning_materialization_tasks = tasks
    existing = tasks.get(key)
    if existing is not None and not existing.done():
        if not replace_existing:
            return
        existing.cancel()
        try:
            await existing
        except asyncio.CancelledError:
            pass
    if replace_existing:
        req = replace(req, fresh_session=replace_existing)
    task = asyncio.create_task(
        planning_materialize.execute(
            workstore,
            chatstore,
            projectstore,
            planningfiles,
            loop_runs,
            planning_sessions,
            chat_supervisor,
            settings,
            req,
        ),
        name=f"planning-materialize-{req.work_slug}",
    )
    tasks[key] = task

    def _clear(done: asyncio.Task[WorkPlanView]) -> None:
        if tasks.get(key) is done:
            tasks.pop(key, None)
        try:
            done.result()
        except asyncio.CancelledError:
            pass
        except planning_materialize.MaterializationIncomplete as exc:
            _log.info(
                "planning materialization incomplete for %s: %s",
                req.work_slug,
                exc,
            )
        except Exception:
            _log.exception("planning materialization failed for %s", req.work_slug)

    task.add_done_callback(_clear)


def _ensure_plan_run_monitor_task(
    request: Request,
    workstore: WorkStore,
    planningfiles: PlanningFiles,
    supervisor: AgentSupervisorService,
    worktree_manager: WorktreeManager,
    connection_store: ConnectionStore,
    sharestore: SharedFolderStore,
    share_provisioner: ShareProvisioner,
    adapter_factory: AgentAdapterFactory,
    check_runner: LoopCheckRunner,
    loop_runs: LoopRunRepository,
    settings: Settings,
    req: planning_run_monitor.MonitorArtifactRunRequest,
) -> None:
    key = f"{req.work_slug}:{req.artifact_id}:{req.run_id}"
    tasks = getattr(request.app.state, "planning_run_monitor_tasks", None)
    if tasks is None:
        tasks = {}
        request.app.state.planning_run_monitor_tasks = tasks
    existing = tasks.get(key)
    if existing is not None and not existing.done():
        return
    poller = getattr(request.app.state, "pr_status_poller", None)
    pr_gateway = poller.lifecycle_gateway() if poller is not None else None
    task = asyncio.create_task(
        planning_run_monitor.execute(
            workstore,
            planningfiles,
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
            pr_gateway=pr_gateway,
        ),
        name=f"planning-run-{req.work_slug}-{req.artifact_id}-{req.run_id}",
    )
    tasks[key] = task

    def _clear(done: asyncio.Task[PlanArtifactDetail]) -> None:
        tasks.pop(key, None)
        try:
            done.result()
        except asyncio.CancelledError:
            pass
        except Exception:
            _log.exception(
                "planning run monitor failed for %s %s %s",
                req.work_slug,
                req.artifact_id,
                req.run_id,
            )

    task.add_done_callback(_clear)


def _ensure_loop_run_monitor_task(
    request: Request,
    workstore: WorkStore,
    supervisor: AgentSupervisorService,
    worktree_manager: WorktreeManager,
    connection_store: ConnectionStore,
    sharestore: SharedFolderStore,
    share_provisioner: ShareProvisioner,
    adapter_factory: AgentAdapterFactory,
    check_runner: LoopCheckRunner,
    loop_runs: LoopRunRepository,
    settings: Settings,
    req: loop_run_commands.LoopRunRequest,
) -> None:
    """Schedule one loop-run monitor unless that monitor is already active."""
    key = f"{req.work_slug}:loop:{req.run_id}"
    tasks = getattr(request.app.state, "planning_run_monitor_tasks", None)
    if tasks is None:
        tasks = {}
        request.app.state.planning_run_monitor_tasks = tasks
    existing = tasks.get(key)
    if existing is not None and not existing.done():
        return
    poller = getattr(request.app.state, "pr_status_poller", None)
    pr_gateway = poller.lifecycle_gateway() if poller is not None else None
    task = asyncio.create_task(
        loop_run_commands.monitor_run(
            workstore,
            loop_runs,
            supervisor,
            worktree_manager,
            connection_store,
            sharestore,
            share_provisioner,
            adapter_factory,
            check_runner,
            settings,
            req,
            pr_gateway=pr_gateway,
        ),
        name=f"loop-run-{req.work_slug}-{req.run_id}",
    )
    tasks[key] = task

    def _clear(done: asyncio.Task[LoopRunRecord]) -> None:
        """Remove the finished monitor and log unexpected failures."""
        tasks.pop(key, None)
        try:
            done.result()
        except asyncio.CancelledError:
            pass
        except Exception:
            _log.exception(
                "loop run monitor failed for %s %s",
                req.work_slug,
                req.run_id,
            )

    task.add_done_callback(_clear)


def _to_start_work_plan_response(
    workstore: WorkStore,
    chatstore: ChatStore,
    planningfiles: PlanningFiles,
    loop_runs: LoopRunRepository,
    work_slug: str,
) -> StartWorkPlanResponse:
    materialization = planning_materialization_status.execute(
        workstore, chatstore, planningfiles, loop_runs, work_slug
    )
    plan = None
    if materialization.state == "complete":
        try:
            plan = _to_plan_response(
                planning_get.execute(workstore, planningfiles, loop_runs, work_slug)
            )
        except (planning_get.WorkNotFound, planning_get.PlanNotFound):
            plan = None
    return StartWorkPlanResponse(
        plan=plan,
        materialization_status=_to_plan_materialization_status_response(materialization),
    )


@router.get("/works/{work_slug}/plan", response_model=WorkPlanResponse)
def get_work_plan_endpoint(
    work_slug: str,
    workstore: WorkStoreDep,
    planningfiles: PlanningFilesDep,
    loop_runs: LoopRunRepositoryDep,
) -> WorkPlanResponse:
    try:
        view = planning_get.execute(workstore, planningfiles, loop_runs, work_slug)
    except (planning_get.WorkNotFound, planning_get.PlanNotFound) as e:
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail=str(e)) from e
    return _to_plan_response(view)


@router.get(
    "/works/{work_slug}/plan/materialization-status",
    response_model=PlanMaterializationStatusResponse,
)
def get_work_plan_materialization_status_endpoint(
    work_slug: str,
    workstore: WorkStoreDep,
    chatstore: ChatStoreDep,
    planningfiles: PlanningFilesDep,
    loop_runs: LoopRunRepositoryDep,
) -> PlanMaterializationStatusResponse:
    try:
        view = planning_materialization_status.execute(
            workstore, chatstore, planningfiles, loop_runs, work_slug
        )
    except planning_materialization_status.WorkNotFound as e:
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail=str(e)) from e
    return _to_plan_materialization_status_response(view)


@router.post(
    "/works/{work_slug}/plan/materialization-permission",
    status_code=status.HTTP_204_NO_CONTENT,
)
async def resolve_work_plan_materialization_permission_endpoint(
    work_slug: str,
    payload: ResolvePlanMaterializationPermissionRequest,
    workstore: WorkStoreDep,
    chatstore: ChatStoreDep,
    chat_supervisor: ChatSupervisorDep,
) -> None:
    try:
        await planning_resolve_materialization_permission.execute(
            workstore,
            chatstore,
            chat_supervisor,
            planning_resolve_materialization_permission.ResolveMaterializationPermissionRequest(
                work_slug=work_slug,
                request_id=payload.request_id,
                decision=payload.decision,
            ),
        )
    except planning_resolve_materialization_permission.WorkNotFound as exc:
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail=str(exc)) from exc
    except planning_resolve_materialization_permission.PermissionNotFound as exc:
        raise HTTPException(status.HTTP_409_CONFLICT, detail=str(exc)) from exc
    except planning_resolve_materialization_permission.MaterializerNotRunning as exc:
        raise HTTPException(status.HTTP_409_CONFLICT, detail=str(exc)) from exc


@router.post("/works/{work_slug}/plan/approve", response_model=WorkPlanResponse)
def approve_work_plan_endpoint(
    work_slug: str,
    workstore: WorkStoreDep,
    planningfiles: PlanningFilesDep,
    loop_runs: LoopRunRepositoryDep,
) -> WorkPlanResponse:
    try:
        view = planning_approve.execute(workstore, planningfiles, loop_runs, work_slug)
    except (planning_approve.WorkNotFound, planning_approve.PlanningNotStarted) as e:
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail=str(e)) from e
    return _to_plan_response(view)


@router.post("/works/{work_slug}/plan/finish", response_model=WorkPlanResponse)
def finish_work_plan_conversation_endpoint(
    work_slug: str,
    workstore: WorkStoreDep,
    planningfiles: PlanningFilesDep,
    loop_runs: LoopRunRepositoryDep,
) -> WorkPlanResponse:
    try:
        view = planning_finish.execute(workstore, planningfiles, loop_runs, work_slug)
    except (planning_finish.WorkNotFound, planning_finish.PlanningNotStarted) as e:
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail=str(e)) from e
    return _to_plan_response(view)


@router.get(
    "/works/{work_slug}/plan/artifacts/{artifact_id}",
    response_model=PlanArtifactDetailResponse,
)
def get_work_plan_artifact_endpoint(
    work_slug: str,
    artifact_id: str,
    workstore: WorkStoreDep,
    planningfiles: PlanningFilesDep,
    loop_runs: LoopRunRepositoryDep,
) -> PlanArtifactDetailResponse:
    try:
        detail = planning_get.artifact(
            workstore, planningfiles, loop_runs, work_slug, artifact_id
        )
    except (
        planning_get.WorkNotFound,
        planning_get.PlanNotFound,
        planning_get.ArtifactNotFound,
    ) as e:
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail=str(e)) from e
    return _to_plan_detail_response(detail)


@router.post(
    "/works/{work_slug}/plan/artifacts/{artifact_id}/runs/{run_id}/pr-refresh",
    response_model=PlanArtifactDetailResponse,
)
async def refresh_work_plan_pr_endpoint(
    request: Request,
    work_slug: str,
    artifact_id: str,
    run_id: str,
    workstore: WorkStoreDep,
    planningfiles: PlanningFilesDep,
    loop_runs: LoopRunRepositoryDep,
    force: bool = False,
) -> PlanArtifactDetailResponse:
    """Synchronize one Planning run's pull-request lifecycle."""
    poller = getattr(request.app.state, "pr_status_poller", None)
    gateway = poller.lifecycle_gateway() if poller is not None else None
    if gateway is None:
        raise HTTPException(
            status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="pull-request lifecycle service is unavailable",
        )
    try:
        detail = await planning_pr_runs.refresh(
            workstore,
            planningfiles,
            loop_runs,
            gateway,
            planning_pr_runs.RefreshPlanningPrRequest(
                work_slug=work_slug,
                artifact_id=artifact_id,
                run_id=run_id,
                force=force,
            ),
        )
    except (
        planning_pr_runs.WorkNotFound,
        planning_pr_runs.PlanArtifactNotFound,
        planning_pr_runs.PlanArtifactRunNotFound,
        planning_pr_runs.PlanningNotStarted,
    ) as exc:
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail=str(exc)) from exc
    except pr_review.PrReviewUnavailable as exc:
        raise HTTPException(status.HTTP_502_BAD_GATEWAY, detail=str(exc)) from exc
    return _to_plan_detail_response(detail)


@router.put(
    "/works/{work_slug}/plan/artifacts/{artifact_id}",
    response_model=PlanArtifactDetailResponse,
)
def update_work_plan_artifact_endpoint(
    work_slug: str,
    artifact_id: str,
    payload: UpdatePlanArtifactRequest,
    workstore: WorkStoreDep,
    planningfiles: PlanningFilesDep,
    loop_runs: LoopRunRepositoryDep,
) -> PlanArtifactDetailResponse:
    try:
        detail = planning_update_artifact.execute(
            workstore,
            planningfiles,
            loop_runs,
            planning_update_artifact.SavePlanArtifactRequest(
                work_slug=work_slug,
                artifact_id=artifact_id,
                content=payload.content,
                expected_hash=payload.expected_hash,
            ),
        )
    except (
        planning_update_artifact.WorkNotFound,
        planning_update_artifact.PlanningNotStarted,
        planning_update_artifact.PlanArtifactNotFound,
    ) as e:
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail=str(e)) from e
    except planning_update_artifact.PlanArtifactConflict as e:
        raise HTTPException(status.HTTP_409_CONFLICT, detail=str(e)) from e
    return _to_plan_detail_response(detail)


@router.post(
    "/works/{work_slug}/plan/artifacts/{artifact_id}/proposals",
    response_model=PlanArtifactDetailResponse,
)
def propose_work_plan_artifact_update_endpoint(
    work_slug: str,
    artifact_id: str,
    payload: CreatePlanArtifactProposalRequest,
    workstore: WorkStoreDep,
    planningfiles: PlanningFilesDep,
    loop_runs: LoopRunRepositoryDep,
) -> PlanArtifactDetailResponse:
    try:
        detail = planning_propose_update.execute(
            workstore,
            planningfiles,
            loop_runs,
            planning_propose_update.ProposeArtifactUpdateRequest(
                work_slug=work_slug,
                artifact_id=artifact_id,
                title=payload.title,
                proposed_content=payload.proposed_content,
            ),
        )
    except (
        planning_propose_update.WorkNotFound,
        planning_propose_update.PlanningNotStarted,
        planning_propose_update.PlanArtifactNotFound,
    ) as e:
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail=str(e)) from e
    return _to_plan_detail_response(detail)


@router.post(
    "/works/{work_slug}/plan/artifacts/{artifact_id}/proposals/{proposal_id}/accept",
    response_model=PlanArtifactDetailResponse,
)
def accept_work_plan_artifact_proposal_endpoint(
    work_slug: str,
    artifact_id: str,
    proposal_id: str,
    workstore: WorkStoreDep,
    planningfiles: PlanningFilesDep,
    loop_runs: LoopRunRepositoryDep,
) -> PlanArtifactDetailResponse:
    try:
        detail = planning_resolve_proposal.execute(
            workstore,
            planningfiles,
            loop_runs,
            planning_resolve_proposal.ResolveArtifactProposalRequest(
                work_slug=work_slug,
                artifact_id=artifact_id,
                proposal_id=proposal_id,
                decision="accept",
            ),
        )
    except (
        planning_resolve_proposal.WorkNotFound,
        planning_resolve_proposal.PlanningNotStarted,
        planning_resolve_proposal.PlanArtifactNotFound,
        planning_resolve_proposal.PlanArtifactProposalNotFound,
    ) as e:
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail=str(e)) from e
    except planning_resolve_proposal.PlanArtifactConflict as e:
        raise HTTPException(status.HTTP_409_CONFLICT, detail=str(e)) from e
    return _to_plan_detail_response(detail)


@router.post(
    "/works/{work_slug}/plan/artifacts/{artifact_id}/proposals/{proposal_id}/reject",
    response_model=PlanArtifactDetailResponse,
)
def reject_work_plan_artifact_proposal_endpoint(
    work_slug: str,
    artifact_id: str,
    proposal_id: str,
    workstore: WorkStoreDep,
    planningfiles: PlanningFilesDep,
    loop_runs: LoopRunRepositoryDep,
) -> PlanArtifactDetailResponse:
    try:
        detail = planning_resolve_proposal.execute(
            workstore,
            planningfiles,
            loop_runs,
            planning_resolve_proposal.ResolveArtifactProposalRequest(
                work_slug=work_slug,
                artifact_id=artifact_id,
                proposal_id=proposal_id,
                decision="reject",
            ),
        )
    except (
        planning_resolve_proposal.WorkNotFound,
        planning_resolve_proposal.PlanningNotStarted,
        planning_resolve_proposal.PlanArtifactNotFound,
        planning_resolve_proposal.PlanArtifactProposalNotFound,
    ) as e:
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail=str(e)) from e
    return _to_plan_detail_response(detail)


@router.post(
    "/works/{work_slug}/plan/artifacts/{artifact_id}/runs",
    response_model=PlanArtifactDetailResponse,
)
async def start_work_plan_artifact_run_endpoint(
    request: Request,
    work_slug: str,
    artifact_id: str,
    payload: StartPlanArtifactRunRequest,
    workstore: WorkStoreDep,
    planningfiles: PlanningFilesDep,
    planning_sessions: PlanningSessionsDep,
    loop_definitions: LoopDefinitionsDep,
    loop_locations: LoopDefinitionLocationsDep,
    loop_runs: LoopRunRepositoryDep,
    context_resolver: LoopContextResolverDep,
    supervisor: SupervisorDep,
    worktree_manager: WorktreeDep,
    connection_store: ConnectionStoreDep,
    sharestore: ShareStoreDep,
    share_provisioner: ShareProvisionerDep,
    adapter_factory: AgentAdapterFactoryDep,
    check_runner: LoopCheckRunnerDep,
    settings: SettingsDep,
) -> PlanArtifactDetailResponse:
    try:
        detail = await planning_start_run.execute(
            workstore,
            planningfiles,
            planning_sessions,
            loop_definitions,
            loop_locations,
            loop_runs,
            context_resolver,
            supervisor,
            worktree_manager,
            connection_store,
            sharestore,
            share_provisioner,
            adapter_factory,
            settings,
            planning_start_run.StartArtifactRunRequest(
                work_slug=work_slug,
                artifact_id=artifact_id,
                loop_definition_id=payload.loop_definition_id,
                loop_revision=payload.loop_revision,
                brief=_to_loop_brief(payload.brief) if payload.brief is not None else None,
                brief_note=payload.brief_note,
            ),
        )
    except (
        planning_start_run.WorkNotFound,
        planning_start_run.AgentNotFound,
        planning_start_run.PlanningNotStarted,
        planning_start_run.PlanArtifactNotFound,
    ) as e:
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail=str(e)) from e
    except planning_start_run.LoopDefinitionNotFound as e:
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail=str(e)) from e
    except planning_start_run.WorkNotActive as e:
        raise HTTPException(status.HTTP_409_CONFLICT, detail=str(e)) from e
    except (
        planning_start_run.PlanArtifactNotExecutable,
        planning_start_run.LoopDefinitionConflict,
        planning_start_run.LoopDefinitionInvalid,
        planning_start_run.LoopContextMissing,
        planning_start_run.AgentFolderMissing,
        planning_start_run.InvalidProviderConfig,
    ) as e:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, detail=str(e)) from e
    except AgentTerminated as e:
        raise HTTPException(status.HTTP_409_CONFLICT, detail=str(e)) from e
    run = detail.artifact.runs[-1]
    _ensure_plan_run_monitor_task(
        request,
        workstore,
        planningfiles,
        supervisor,
        worktree_manager,
        connection_store,
        sharestore,
        share_provisioner,
        adapter_factory,
        check_runner,
        loop_runs,
        settings,
        planning_run_monitor.MonitorArtifactRunRequest(
            work_slug=work_slug,
            artifact_id=artifact_id,
            run_id=run.id,
        ),
    )
    return _to_plan_detail_response(detail)


@router.post(
    "/works/{work_slug}/plan/artifacts/{artifact_id}/runs/{run_id}/rerun",
    response_model=PlanArtifactDetailResponse,
    status_code=status.HTTP_201_CREATED,
)
async def rerun_work_plan_artifact_run_endpoint(
    request: Request,
    work_slug: str,
    artifact_id: str,
    run_id: str,
    payload: RerunWorkLoopRunRequest,
    workstore: WorkStoreDep,
    planningfiles: PlanningFilesDep,
    planning_sessions: PlanningSessionsDep,
    loop_definitions: LoopDefinitionsDep,
    loop_locations: LoopDefinitionLocationsDep,
    loop_runs: LoopRunRepositoryDep,
    context_resolver: LoopContextResolverDep,
    supervisor: SupervisorDep,
    worktree_manager: WorktreeDep,
    connection_store: ConnectionStoreDep,
    sharestore: ShareStoreDep,
    share_provisioner: ShareProvisionerDep,
    adapter_factory: AgentAdapterFactoryDep,
    check_runner: LoopCheckRunnerDep,
    settings: SettingsDep,
) -> PlanArtifactDetailResponse:
    """Start a follow-up run from a terminal story run's kept workspace."""
    try:
        detail = await planning_rerun_run.execute(
            workstore,
            planningfiles,
            planning_sessions,
            loop_definitions,
            loop_locations,
            loop_runs,
            context_resolver,
            supervisor,
            worktree_manager,
            connection_store,
            sharestore,
            share_provisioner,
            adapter_factory,
            settings,
            planning_rerun_run.RerunArtifactRunRequest(
                work_slug=work_slug,
                artifact_id=artifact_id,
                run_id=run_id,
                kind=LoopRunKind(payload.kind),
                note=payload.note,
            ),
        )
    except (
        planning_start_run.WorkNotFound,
        planning_start_run.AgentNotFound,
        planning_start_run.PlanningNotStarted,
        planning_start_run.PlanArtifactNotFound,
        planning_rerun_run.PlanArtifactRunNotFound,
    ) as e:
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail=str(e)) from e
    except planning_start_run.LoopDefinitionNotFound as e:
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail=str(e)) from e
    except planning_start_run.WorkNotActive as e:
        raise HTTPException(status.HTTP_409_CONFLICT, detail=str(e)) from e
    except (
        followups.FollowUpNotAvailable,
        planning_start_run.PlanArtifactNotExecutable,
        planning_start_run.LoopDefinitionConflict,
        planning_start_run.LoopDefinitionInvalid,
        planning_start_run.LoopContextMissing,
        planning_start_run.AgentFolderMissing,
        planning_start_run.InvalidProviderConfig,
    ) as e:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, detail=str(e)) from e
    except AgentTerminated as e:
        raise HTTPException(status.HTTP_409_CONFLICT, detail=str(e)) from e
    run = detail.artifact.runs[-1]
    _ensure_plan_run_monitor_task(
        request,
        workstore,
        planningfiles,
        supervisor,
        worktree_manager,
        connection_store,
        sharestore,
        share_provisioner,
        adapter_factory,
        check_runner,
        loop_runs,
        settings,
        planning_run_monitor.MonitorArtifactRunRequest(
            work_slug=work_slug,
            artifact_id=artifact_id,
            run_id=run.id,
        ),
    )
    return _to_plan_detail_response(detail)


@router.get(
    "/works/{work_slug}/plan/artifacts/{artifact_id}/runs",
    response_model=list[PlanArtifactRunResponse],
)
def list_work_plan_artifact_runs_endpoint(
    work_slug: str,
    artifact_id: str,
    workstore: WorkStoreDep,
    planningfiles: PlanningFilesDep,
    loop_runs: LoopRunRepositoryDep,
) -> list[PlanArtifactRunResponse]:
    try:
        runs = planning_list_runs.execute(
            workstore,
            planningfiles,
            loop_runs,
            planning_list_runs.ListArtifactRunsRequest(
                work_slug=work_slug,
                artifact_id=artifact_id,
            ),
        )
    except (
        planning_list_runs.WorkNotFound,
        planning_list_runs.PlanningNotStarted,
        planning_list_runs.PlanArtifactNotFound,
    ) as e:
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail=str(e)) from e
    return [_to_plan_run(run) for run in runs]


@router.get(
    "/works/{work_slug}/plan/artifacts/{artifact_id}/runs/{run_id}",
    response_model=PlanArtifactRunResponse,
)
def get_work_plan_artifact_run_endpoint(
    work_slug: str,
    artifact_id: str,
    run_id: str,
    workstore: WorkStoreDep,
    planningfiles: PlanningFilesDep,
    loop_runs: LoopRunRepositoryDep,
) -> PlanArtifactRunResponse:
    try:
        run = planning_get_run.execute(
            workstore,
            planningfiles,
            loop_runs,
            planning_get_run.GetArtifactRunRequest(
                work_slug=work_slug,
                artifact_id=artifact_id,
                run_id=run_id,
            ),
        )
    except (
        planning_get_run.WorkNotFound,
        planning_get_run.PlanningNotStarted,
        planning_get_run.PlanArtifactNotFound,
        planning_get_run.PlanArtifactRunNotFound,
    ) as e:
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail=str(e)) from e
    return _to_plan_run(run)


@router.post(
    "/works/{work_slug}/plan/artifacts/{artifact_id}/runs/{run_id}/resume",
    response_model=PlanArtifactDetailResponse,
)
async def resume_work_plan_artifact_run_endpoint(
    request: Request,
    work_slug: str,
    artifact_id: str,
    run_id: str,
    payload: ResumePlanArtifactRunRequest,
    workstore: WorkStoreDep,
    planningfiles: PlanningFilesDep,
    supervisor: SupervisorDep,
    worktree_manager: WorktreeDep,
    connection_store: ConnectionStoreDep,
    sharestore: ShareStoreDep,
    share_provisioner: ShareProvisionerDep,
    adapter_factory: AgentAdapterFactoryDep,
    check_runner: LoopCheckRunnerDep,
    loop_runs: LoopRunRepositoryDep,
    settings: SettingsDep,
) -> PlanArtifactDetailResponse:
    try:
        detail = await planning_resume_run.execute(
            workstore,
            planningfiles,
            loop_runs,
            supervisor,
            worktree_manager,
            connection_store,
            sharestore,
            share_provisioner,
            adapter_factory,
            settings,
            planning_resume_run.ResumeArtifactRunRequest(
                work_slug=work_slug,
                artifact_id=artifact_id,
                run_id=run_id,
                resolution_note=payload.resolution_note,
                retry_failed=payload.retry_failed,
                gate_decision=payload.gate_decision,
                enforced_findings=tuple(payload.enforced_findings),
            ),
        )
    except (
        planning_resume_run.WorkNotFound,
        planning_resume_run.AgentNotFound,
        planning_resume_run.PlanningNotStarted,
        planning_resume_run.PlanArtifactNotFound,
        planning_resume_run.PlanArtifactRunNotFound,
    ) as e:
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail=str(e)) from e
    except (
        planning_resume_run.PlanArtifactNotExecutable,
        planning_resume_run.PlanArtifactRunNotResumable,
    ) as e:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, detail=str(e)) from e
    except AgentTerminated as e:
        raise HTTPException(status.HTTP_409_CONFLICT, detail=str(e)) from e
    _ensure_plan_run_monitor_task(
        request,
        workstore,
        planningfiles,
        supervisor,
        worktree_manager,
        connection_store,
        sharestore,
        share_provisioner,
        adapter_factory,
        check_runner,
        loop_runs,
        settings,
        planning_run_monitor.MonitorArtifactRunRequest(
            work_slug=work_slug,
            artifact_id=artifact_id,
            run_id=run_id,
        ),
    )
    return _to_plan_detail_response(detail)


@router.post(
    "/works/{work_slug}/plan/artifacts/{artifact_id}/runs/{run_id}/request-changes",
    response_model=PlanArtifactDetailResponse,
)
async def request_work_plan_run_changes_endpoint(
    request: Request,
    work_slug: str,
    artifact_id: str,
    run_id: str,
    payload: RequestPlanRunChangesRequest,
    workstore: WorkStoreDep,
    planningfiles: PlanningFilesDep,
    supervisor: SupervisorDep,
    worktree_manager: WorktreeDep,
    connection_store: ConnectionStoreDep,
    sharestore: ShareStoreDep,
    share_provisioner: ShareProvisionerDep,
    adapter_factory: AgentAdapterFactoryDep,
    check_runner: LoopCheckRunnerDep,
    loop_runs: LoopRunRepositoryDep,
    settings: SettingsDep,
) -> PlanArtifactDetailResponse:
    try:
        detail = await planning_request_run_changes.execute(
            workstore,
            planningfiles,
            loop_runs,
            supervisor,
            worktree_manager,
            sharestore,
            share_provisioner,
            settings,
            planning_request_run_changes.RequestRunChangesRequest(
                work_slug=work_slug,
                artifact_id=artifact_id,
                run_id=run_id,
                note=payload.note,
            ),
        )
    except (
        planning_request_run_changes.WorkNotFound,
        planning_request_run_changes.AgentNotFound,
        planning_request_run_changes.PlanningNotStarted,
        planning_request_run_changes.PlanArtifactNotFound,
        planning_request_run_changes.PlanArtifactRunNotFound,
    ) as exc:
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail=str(exc)) from exc
    except planning_request_run_changes.PlanArtifactRunNotChangeable as exc:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, detail=str(exc)) from exc
    _ensure_plan_run_monitor_task(
        request,
        workstore,
        planningfiles,
        supervisor,
        worktree_manager,
        connection_store,
        sharestore,
        share_provisioner,
        adapter_factory,
        check_runner,
        loop_runs,
        settings,
        planning_run_monitor.MonitorArtifactRunRequest(
            work_slug=work_slug,
            artifact_id=artifact_id,
            run_id=run_id,
        ),
    )
    return _to_plan_detail_response(detail)


@router.post(
    "/works/{work_slug}/plan/artifacts/{artifact_id}/runs/{run_id}/accept",
    response_model=PlanArtifactDetailResponse,
)
async def accept_work_plan_artifact_run_endpoint(
    request: Request,
    work_slug: str,
    artifact_id: str,
    run_id: str,
    payload: AcceptPlanArtifactRequest,
    workstore: WorkStoreDep,
    planningfiles: PlanningFilesDep,
    loop_runs: LoopRunRepositoryDep,
    supervisor: SupervisorDep,
    worktree_manager: WorktreeDep,
    connection_store: ConnectionStoreDep,
    sharestore: ShareStoreDep,
    share_provisioner: ShareProvisionerDep,
    adapter_factory: AgentAdapterFactoryDep,
    check_runner: LoopCheckRunnerDep,
    settings: SettingsDep,
) -> PlanArtifactDetailResponse:
    try:
        detail = await planning_accept_run.execute(
            workstore,
            planningfiles,
            loop_runs,
            supervisor,
            planning_accept_run.AcceptArtifactRunRequest(
                work_slug=work_slug,
                artifact_id=artifact_id,
                run_id=run_id,
                summary=payload.summary,
                divergences=payload.divergences,
                skipped_scope=payload.skipped_scope,
                blockers=payload.blockers,
                decisions=payload.decisions,
                changes=payload.changes,
                validation_evidence=payload.validation_evidence,
            ),
        )
    except (
        planning_accept_run.WorkNotFound,
        planning_accept_run.PlanningNotStarted,
        planning_accept_run.PlanArtifactNotFound,
        planning_accept_run.PlanArtifactRunNotFound,
    ) as e:
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail=str(e)) from e
    except (
        planning_accept_run.PlanArtifactNotExecutable,
        planning_accept_run.PlanArtifactRunNotAcceptable,
    ) as e:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, detail=str(e)) from e
    run = next((item for item in detail.artifact.runs if item.id == run_id), None)
    if run is not None and run.status == PlanRunStatus.RUNNING:
        _ensure_plan_run_monitor_task(
            request,
            workstore,
            planningfiles,
            supervisor,
            worktree_manager,
            connection_store,
            sharestore,
            share_provisioner,
            adapter_factory,
            check_runner,
            loop_runs,
            settings,
            planning_run_monitor.MonitorArtifactRunRequest(
                work_slug=work_slug,
                artifact_id=artifact_id,
                run_id=run_id,
            ),
        )
    return _to_plan_detail_response(detail)


@router.post(
    "/works/{work_slug}/plan/artifacts/{artifact_id}/runs/{run_id}/create-pr",
    response_model=PlanArtifactDetailResponse,
)
async def create_work_plan_pr_stage_endpoint(
    request: Request,
    work_slug: str,
    artifact_id: str,
    run_id: str,
    payload: CreatePrStageRequest,
    workstore: WorkStoreDep,
    planningfiles: PlanningFilesDep,
    loop_runs: LoopRunRepositoryDep,
    supervisor: SupervisorDep,
    worktree_manager: WorktreeDep,
    connection_store: ConnectionStoreDep,
    sharestore: ShareStoreDep,
    share_provisioner: ShareProvisionerDep,
    adapter_factory: AgentAdapterFactoryDep,
    check_runner: LoopCheckRunnerDep,
    settings: SettingsDep,
) -> PlanArtifactDetailResponse:
    """Add and launch a Work-local Create PR stage for Planning."""
    try:
        detail = planning_pr_runs.create_stage(
            workstore,
            planningfiles,
            loop_runs,
            planning_pr_runs.CreatePlanningPrRequest(
                work_slug=work_slug,
                artifact_id=artifact_id,
                run_id=run_id,
                setup=pr_lifecycle.PrSetup(**payload.model_dump()),
            ),
        )
    except (
        planning_pr_runs.WorkNotFound,
        planning_pr_runs.PlanArtifactNotFound,
        planning_pr_runs.PlanArtifactRunNotFound,
        planning_pr_runs.PlanningNotStarted,
    ) as exc:
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(
            status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail=str(exc),
        ) from exc
    _ensure_plan_run_monitor_task(
        request,
        workstore,
        planningfiles,
        supervisor,
        worktree_manager,
        connection_store,
        sharestore,
        share_provisioner,
        adapter_factory,
        check_runner,
        loop_runs,
        settings,
        planning_run_monitor.MonitorArtifactRunRequest(
            work_slug=work_slug,
            artifact_id=artifact_id,
            run_id=run_id,
        ),
    )
    return _to_plan_detail_response(detail)


@router.post(
    "/works/{work_slug}/plan/artifacts/{artifact_id}/runs/{run_id}/pr-feedback",
    response_model=PlanArtifactDetailResponse,
)
async def send_work_plan_pr_feedback_endpoint(
    request: Request,
    work_slug: str,
    artifact_id: str,
    run_id: str,
    payload: SendPrFeedbackRequest,
    workstore: WorkStoreDep,
    planningfiles: PlanningFilesDep,
    loop_runs: LoopRunRepositoryDep,
    supervisor: SupervisorDep,
    worktree_manager: WorktreeDep,
    connection_store: ConnectionStoreDep,
    sharestore: ShareStoreDep,
    share_provisioner: ShareProvisionerDep,
    adapter_factory: AgentAdapterFactoryDep,
    check_runner: LoopCheckRunnerDep,
    settings: SettingsDep,
) -> PlanArtifactDetailResponse:
    """Start another Planning pass from selected PR feedback."""
    try:
        detail = planning_pr_runs.send_feedback(
            workstore,
            planningfiles,
            loop_runs,
            planning_pr_runs.SendPlanningPrFeedbackRequest(
                work_slug=work_slug,
                artifact_id=artifact_id,
                run_id=run_id,
                comments=tuple(
                    pr_lifecycle.PrFeedbackItem(
                        comment_id=item.comment_id,
                        instruction=item.instruction,
                    )
                    for item in payload.comments
                ),
                instruction=payload.instruction,
            ),
        )
    except (
        planning_pr_runs.WorkNotFound,
        planning_pr_runs.PlanArtifactNotFound,
        planning_pr_runs.PlanArtifactRunNotFound,
        planning_pr_runs.PlanningNotStarted,
    ) as exc:
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(
            status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail=str(exc),
        ) from exc
    _ensure_plan_run_monitor_task(
        request,
        workstore,
        planningfiles,
        supervisor,
        worktree_manager,
        connection_store,
        sharestore,
        share_provisioner,
        adapter_factory,
        check_runner,
        loop_runs,
        settings,
        planning_run_monitor.MonitorArtifactRunRequest(
            work_slug=work_slug,
            artifact_id=artifact_id,
            run_id=run_id,
        ),
    )
    return _to_plan_detail_response(detail)


@router.post(
    "/works/{work_slug}/plan/artifacts/{artifact_id}/runs/{run_id}/cancel",
    response_model=PlanArtifactDetailResponse,
)
async def cancel_work_plan_artifact_run_endpoint(
    work_slug: str,
    artifact_id: str,
    run_id: str,
    workstore: WorkStoreDep,
    planningfiles: PlanningFilesDep,
    loop_runs: LoopRunRepositoryDep,
    supervisor: SupervisorDep,
) -> PlanArtifactDetailResponse:
    """Cancel one active Planning artifact run."""
    try:
        detail = await planning_cancel_run.execute(
            workstore,
            planningfiles,
            loop_runs,
            supervisor,
            planning_cancel_run.CancelArtifactRunRequest(
                work_slug=work_slug,
                artifact_id=artifact_id,
                run_id=run_id,
            ),
        )
    except (
        planning_cancel_run.WorkNotFound,
        planning_cancel_run.PlanArtifactNotFound,
        planning_cancel_run.PlanArtifactRunNotFound,
        planning_cancel_run.PlanningNotStarted,
    ) as exc:
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail=str(exc)) from exc
    except lifecycle.LoopRunNotCancellable as exc:
        raise HTTPException(
            status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail=str(exc),
        ) from exc
    return _to_plan_detail_response(detail)


@router.post(
    "/works/{work_slug}/plan/artifacts/{artifact_id}/runs/{run_id}/cleanup",
    response_model=PlanArtifactDetailResponse,
)
async def mark_work_plan_run_cleaned_endpoint(
    work_slug: str,
    artifact_id: str,
    run_id: str,
    workstore: WorkStoreDep,
    planningfiles: PlanningFilesDep,
    loop_runs: LoopRunRepositoryDep,
    supervisor: SupervisorDep,
) -> PlanArtifactDetailResponse:
    try:
        detail = await planning_mark_run_cleaned.execute(
            workstore,
            planningfiles,
            loop_runs,
            supervisor,
            planning_mark_run_cleaned.MarkArtifactRunCleanedRequest(
                work_slug=work_slug,
                artifact_id=artifact_id,
                run_id=run_id,
            ),
        )
    except (
        planning_mark_run_cleaned.WorkNotFound,
        planning_mark_run_cleaned.PlanningNotStarted,
        planning_mark_run_cleaned.PlanArtifactNotFound,
        planning_mark_run_cleaned.PlanArtifactRunNotFound,
    ) as e:
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail=str(e)) from e
    except planning_mark_run_cleaned.PlanArtifactRunNotCleanable as e:
        raise HTTPException(
            status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail=str(e),
        ) from e
    return _to_plan_detail_response(detail)


@router.post(
    "/works/{work_slug}/plan/artifacts/{artifact_id}/tracking",
    response_model=PlanArtifactDetailResponse,
)
def link_work_plan_artifact_tracking_endpoint(
    work_slug: str,
    artifact_id: str,
    payload: LinkPlanArtifactTrackingRequest,
    workstore: WorkStoreDep,
    planningfiles: PlanningFilesDep,
    loop_runs: LoopRunRepositoryDep,
) -> PlanArtifactDetailResponse:
    try:
        detail = planning_link_tracking.execute(
            workstore,
            planningfiles,
            loop_runs,
            planning_link_tracking.LinkArtifactTrackingRequest(
                work_slug=work_slug,
                artifact_id=artifact_id,
                kind=payload.kind,
                title=payload.title,
                url=payload.url,
                status=payload.status,
                ref=payload.ref,
                notes=payload.notes,
            ),
        )
    except (
        planning_link_tracking.WorkNotFound,
        planning_link_tracking.PlanningNotStarted,
        planning_link_tracking.PlanArtifactNotFound,
    ) as e:
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail=str(e)) from e
    return _to_plan_detail_response(detail)


@router.post(
    "/works/{work_slug}/plan/artifacts/{artifact_id}/bugs",
    response_model=PlanArtifactDetailResponse,
)
def create_work_plan_bug_endpoint(
    work_slug: str,
    artifact_id: str,
    payload: CreatePlanBugRequest,
    workstore: WorkStoreDep,
    planningfiles: PlanningFilesDep,
    loop_runs: LoopRunRepositoryDep,
) -> PlanArtifactDetailResponse:
    try:
        detail = planning_create_bug.execute(
            workstore,
            planningfiles,
            loop_runs,
            planning_create_bug.CreateArtifactBugRequest(
                work_slug=work_slug,
                artifact_id=artifact_id,
                title=payload.title,
                description=payload.description,
            ),
        )
    except (
        planning_create_bug.WorkNotFound,
        planning_create_bug.PlanningNotStarted,
        planning_create_bug.PlanArtifactNotFound,
    ) as e:
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail=str(e)) from e
    return _to_plan_detail_response(detail)


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
        record = next(
            (item for item in workstore.list_agents_for_work(work) if item.slug == agent),
            None,
        )
        candidate = paths.worktree_dir(
            work, record.worktree_slug if record and record.worktree_slug else agent
        )
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
    return _to_handoff_summary(handoff, agent_slug_by_id, source_slug=payload.source_agent_slug)


@router.get(
    "/works/{work_slug}/handoffs",
    response_model=list[HandoffSummary],
)
def list_work_handoffs_endpoint(work_slug: str, workstore: WorkStoreDep) -> list[HandoffSummary]:
    try:
        handoffs = workstore.list_handoffs_for_work(work_slug)
    except ValueError as e:
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail=str(e)) from e
    agents = workstore.list_agents_for_work(work_slug)
    agent_slug_by_id = {a.id: a.slug for a in agents if a.id is not None}
    return [_to_handoff_summary(h, agent_slug_by_id) for h in handoffs]


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
def delete_work_endpoint(work_slug: str, workstore: WorkStoreDep) -> None:
    try:
        soft_delete.execute(workstore, work_slug)
    except ValueError as e:
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
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, detail=str(e)) from e
    return _to_detail(record, WorkspacePaths(workspace_root=settings.workspace_root))


@router.get("/works/{work_slug}/completion", response_model=CompleteWorkPreviewResponse)
def preview_work_completion_endpoint(
    work_slug: str,
    workstore: WorkStoreDep,
    loop_runs: LoopRunRepositoryDep,
    worktree_manager: WorktreeDep,
) -> CompleteWorkPreviewResponse:
    """Preview completion blockers and managed workspace state."""
    try:
        result = complete.preview(workstore, loop_runs, worktree_manager, work_slug)
    except complete.WorkNotFound as e:
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail=str(e)) from e
    return CompleteWorkPreviewResponse(
        work_slug=result.work_slug,
        agent_count=result.agent_count,
        active_run_count=result.active_run_count,
        workspaces=[
            CompletionWorkspaceResponse(
                owner=state.workdir.name,
                path=str(state.workdir),
                is_git_repo=state.is_git_repo,
                branch=state.branch,
                head=state.head,
                changed_files=list(state.changed_files),
                untracked_files=list(state.untracked_files),
                removable=complete.workspace_is_removable(state),
                error=state.error,
            )
            for state in result.workspaces
        ],
    )


@router.post("/works/{work_slug}/complete", response_model=CompleteWorkResponse)
async def complete_work_endpoint(
    work_slug: str,
    workstore: WorkStoreDep,
    loop_runs: LoopRunRepositoryDep,
    supervisor: SupervisorDep,
    chatstore: ChatStoreDep,
    chat_supervisor: ChatSupervisorDep,
    worktree_manager: WorktreeDep,
    payload: CompleteWorkRequestBody | None = None,
) -> CompleteWorkResponse:
    """Stop provider runtimes and archive a Work, preserving history."""
    try:
        result = await complete.execute(
            workstore,
            loop_runs,
            supervisor,
            chatstore,
            chat_supervisor,
            worktree_manager,
            complete.CompleteWorkRequest(
                work_slug=work_slug,
                remove_workspaces=payload.remove_workspaces if payload else False,
            ),
        )
    except complete.WorkNotFound as e:
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail=str(e)) from e
    except (
        complete.WorkHasActiveRuns,
        complete.WorkNotActive,
        complete.WorkspaceNotClean,
    ) as e:
        raise HTTPException(status.HTTP_409_CONFLICT, detail=str(e)) from e
    return CompleteWorkResponse(
        work_slug=result.work_slug,
        agent_count=result.agent_count,
        workspace_count=result.workspace_count,
        workspaces_removed=result.workspaces_removed,
    )


@router.post("/works/{work_slug}/reveal", status_code=status.HTTP_204_NO_CONTENT)
def reveal_work_endpoint(work_slug: str, workstore: WorkStoreDep, settings: SettingsDep) -> None:
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
        result = workstore.read_work_chat_context_doc(work_slug, folder_name, filename)
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
        mode=payload.mode,
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
        mode=payload.mode,
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
        mode=work.mode,
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
    resolved_source = agent_slug_by_id.get(handoff.source_agent_id) or source_slug or ""
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


def _to_chat_detail(record: ChatRecord) -> ChatDetail:
    chat = record.chat
    summary: dict[str, Any] = {
        "slug": _require_chat_slug(chat),
        "title": chat.title,
        "provider": chat.provider,
        "model": chat.model,
        "options": chat.options or {},
        "grounding": (
            ChatGroundingSchema(kind=chat.grounding_kind, ref=chat.grounding_ref)
            if chat.grounding_kind is not None and chat.grounding_ref is not None
            else None
        ),
        "working_directory": chat.working_directory,
        "created_at": chat.created_at,
        "updated_at": chat.updated_at,
        "promoted_to_work_slug": chat.promoted_to_work_slug,
        "message_count": len(record.transcript),
        "planning_readiness": _planning_readiness_to_schema(planning_readiness_from_record(record)),
    }
    return ChatDetail(
        **summary,
        transcript=[_to_chat_message_schema(m) for m in record.transcript],
    )


def _to_chat_message_schema(message: ChatMessage) -> ChatMessageSchema:
    return ChatMessageSchema(
        role=message.role,
        body=message.body,
        created_at=message.created_at,
    )


def _planning_readiness_to_schema(
    readiness: PlanningChatReadiness | None,
) -> PlanningChatReadinessResponse | None:
    if readiness is None:
        return None
    return PlanningChatReadinessResponse(
        ready=readiness.ready,
        summary=readiness.summary,
    )


def _to_framework_status(
    status_view: PlanningFrameworkStatus,
) -> PlanningFrameworkStatusResponse:
    return PlanningFrameworkStatusResponse(
        framework=status_view.framework,
        label=status_view.label,
        root_path=status_view.root_path,
        ready=status_view.ready,
        markers=status_view.markers,
        setup_command=status_view.setup_command,
        setup_hint=status_view.setup_hint,
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


def _to_plan_response(view: WorkPlanView) -> WorkPlanResponse:
    if view.overview is None:
        raise RuntimeError("WorkPlanView missing overview")
    return WorkPlanResponse(
        work_slug=view.work_slug,
        framework=view.framework,
        profile=view.profile,
        phase=view.phase,
        depth=view.depth,
        root_path=view.root_path,
        planning_path=view.planning_path,
        artifact_root_path=view.artifact_root_path,
        approved_at=view.approved_at,
        stale=view.stale,
        overview=_to_plan_overview(view.overview),
        artifacts=[_to_plan_artifact(a) for a in view.artifacts],
    )


def _to_plan_materialization_status_response(
    view: planning_materialization_status.MaterializationStatus,
) -> PlanMaterializationStatusResponse:
    return PlanMaterializationStatusResponse(
        state=view.state,
        chat_slug=view.chat_slug,
        updated_at=view.updated_at,
        last_seq=view.last_seq,
        last_event_type=view.last_event_type,
        last_event_summary=view.last_event_summary,
        message=view.message,
        tool_name=view.tool_name,
        recent_activity=[
            {
                "kind": item.kind,
                "text": item.text,
                "ts": item.ts,
            }
            for item in view.recent_activity
        ],
        pending_permissions=[
            {
                "request_id": item.request_id,
                "tool_name": item.tool_name,
                "tool_input": item.tool_input,
                "ts": item.ts,
                "seq": item.seq,
                "options": list(item.options),
            }
            for item in view.pending_permissions
        ],
    )


def _to_plan_detail_response(
    detail: PlanArtifactDetail,
) -> PlanArtifactDetailResponse:
    return PlanArtifactDetailResponse(
        artifact=_to_plan_artifact(detail.artifact),
        content=detail.content,
    )


def _to_plan_overview(overview: PlanOverview) -> PlanOverviewResponse:
    return PlanOverviewResponse(
        total=overview.total,
        ready=overview.ready,
        needs_detail=overview.needs_detail,
        approved=overview.approved,
        changed=overview.changed,
        accepted=overview.accepted,
        executable=overview.executable,
        running=overview.running,
        review=overview.review,
        blocked=overview.blocked,
    )


def _to_plan_artifact(artifact: PlanArtifactSummary) -> PlanArtifactResponse:
    return PlanArtifactResponse(
        id=artifact.id,
        kind=artifact.kind,
        title=artifact.title,
        path=artifact.path,
        source_ref=artifact.source_ref,
        source_hash=artifact.source_hash,
        status=artifact.status,
        readiness=artifact.readiness,
        executable=artifact.executable,
        launchable=artifact.launchable,
        launch_blockers=artifact.launch_blockers,
        dependencies=artifact.dependencies,
        runs=[_to_plan_run(run) for run in artifact.runs],
        proposals=[_to_plan_proposal(proposal) for proposal in artifact.proposals],
        tracking=[_to_plan_tracking(link) for link in artifact.tracking],
        accepted_summary_path=artifact.accepted_summary_path,
    )


def _to_plan_run(run: PlanArtifactRun) -> PlanArtifactRunResponse:
    return PlanArtifactRunResponse(
        id=run.id,
        agent_slug=run.agent_slug,
        status=run.status,
        started_at=run.started_at,
        completed_at=run.completed_at,
        cleanup_at=run.cleanup_at,
        report_path=run.report_path,
        summary=run.summary,
        divergences=run.divergences,
        skipped_scope=run.skipped_scope,
        blockers=run.blockers,
        decisions=run.decisions,
        changes=run.changes,
        validation_evidence=run.validation_evidence,
        brief_note=run.brief_note,
        loop_status=run.loop_status,
        loop_status_reason=run.loop_status_reason,
        loop_attempt=run.loop_attempt,
        loop_latest_assessment=run.loop_latest_assessment,
        loop_definition_id=run.loop_definition_id,
        loop_definition_name=run.loop_definition_name,
        loop_definition_revision=run.loop_definition_revision,
        loop_definition=(
            definition_snapshot(run.loop_definition) if run.loop_definition is not None else None
        ),
        loop_current_stage_id=run.loop_current_stage_id,
        loop_stages=[
            PlanLoopStageRunResponse.model_validate(
                {
                    "id": stage.id,
                    "name": stage.name,
                    "kind": stage.kind,
                    "status": stage.status,
                    "attempt": stage.attempt,
                    "max_attempts": stage.max_attempts,
                    "agent_slug": stage.agent_slug,
                    "permissions": stage.permissions,
                    "session": stage.session,
                    "summary": stage.summary,
                    "findings": stage.findings,
                    "changes": stage.changes,
                    "validation_evidence": stage.validation_evidence,
                    "divergences": stage.divergences,
                    "skipped_scope": stage.skipped_scope,
                    "blocker": stage.blocker,
                    "artifact_refs": stage.artifact_refs,
                    "finding_details": [
                        {
                            "text": finding.text,
                            "severity": finding.severity,
                            "location": finding.location,
                        }
                        for finding in stage.finding_details
                    ],
                    "criteria_coverage": [
                        {
                            "text": criterion.text,
                            "met": criterion.met,
                            "note": criterion.note,
                        }
                        for criterion in stage.criteria_coverage
                    ],
                    "changed_files": [
                        {
                            "path": changed.path,
                            "additions": changed.additions,
                            "deletions": changed.deletions,
                        }
                        for changed in stage.changed_files
                    ],
                    "resolved_context": stage.resolved_context,
                    "context_warnings": stage.context_warnings,
                    "reports": [
                        {
                            "outcome": report.outcome,
                            "pass_number": report.pass_number,
                            "agent_slug": report.agent_slug,
                            "summary": report.summary,
                            "findings": report.findings,
                            "changes": report.changes,
                            "validation_evidence": report.validation_evidence,
                            "divergences": report.divergences,
                            "skipped_scope": report.skipped_scope,
                            "blocker": report.blocker,
                            "artifact_refs": report.artifact_refs,
                            "finding_details": [
                                {
                                    "text": finding.text,
                                    "severity": finding.severity,
                                    "location": finding.location,
                                }
                                for finding in report.finding_details
                            ],
                            "criteria_coverage": [
                                {
                                    "text": criterion.text,
                                    "met": criterion.met,
                                    "note": criterion.note,
                                }
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
                            "seq": report.seq,
                            "recorded_at": report.recorded_at,
                            "review_decision": (
                                {
                                    "decision": report.review_decision.decision,
                                    "enforced_findings": report.review_decision.enforced_findings,
                                    "instruction": report.review_decision.instruction,
                                }
                                if report.review_decision is not None
                                else None
                            ),
                            "push_at": report.push_at,
                            "pr": report.pr,
                            "addressed_comments": report.addressed_comments,
                            "feedback_instruction": report.feedback_instruction,
                        }
                        for report in stage.reports
                    ],
                    "push_at": stage.push_at,
                    "pr": stage.pr,
                    "addressed_comments": stage.addressed_comments,
                    "feedback_instruction": stage.feedback_instruction,
                    "approved_command_prefixes": stage.approved_command_prefixes,
                }
            )
            for stage in run.loop_stages
        ],
        loop_review_gate=run.loop_review_gate,
        waived_findings_count=run.waived_findings_count,
        loop_pass_number=run.loop_pass_number,
        loop_passes=run.loop_passes,
        pr=run.pr,
        pr_comments=run.pr_comments,
    )


def _to_work_loop_run(record: LoopRunRecord) -> WorkLoopRunResponse:
    """Project one durable loop run onto its REST response."""
    state = record.state
    raw_loop = state.get("loop")
    loop: dict[str, Any] = raw_loop if isinstance(raw_loop, dict) else {}
    stages: list[PlanLoopStageRunResponse] = []
    raw_stages = loop.get("stages")
    if isinstance(raw_stages, list):
        for row in raw_stages:
            if not isinstance(row, dict):
                continue
            try:
                stages.append(PlanLoopStageRunResponse.model_validate(row))
            except ValueError:
                continue
    changed_by_path = {changed.path: changed for stage in stages for changed in stage.changed_files}
    evidence = list(
        dict.fromkeys(
            stage.validation_evidence for stage in stages if stage.validation_evidence.strip()
        )
    )
    finished_at = record.completed_at or datetime.now(UTC)
    elapsed = max(0.0, (finished_at - record.started_at).total_seconds())
    raw_options = state.get("options")
    options = (
        {str(key): str(value) for key, value in raw_options.items()}
        if isinstance(raw_options, dict)
        else {}
    )
    raw_cost = state.get("cost_usd")
    raw_number = state.get("number")
    raw_kind = state.get("run_kind")
    try:
        run_kind = LoopRunKind(raw_kind) if isinstance(raw_kind, str) else LoopRunKind.INITIAL
    except ValueError:
        run_kind = LoopRunKind.INITIAL
    raw_status = loop.get("status")
    try:
        loop_status = LoopStatus(raw_status) if isinstance(raw_status, str) else record.status
    except ValueError:
        loop_status = record.status
    definition = loop.get("definition_snapshot")
    brief = optional_brief_from_snapshot(state.get("brief"))
    return WorkLoopRunResponse(
        id=str(state.get("id") or record.plan_run_id or record.run_key),
        number=raw_number if isinstance(raw_number, int) else 0,
        goal=record.target_ref,
        status=loop_status,
        status_reason=str(loop.get("status_reason") or ""),
        loop_definition_id=str(loop.get("definition_id") or record.definition_id),
        loop_definition_name=str(loop.get("definition_name") or ""),
        loop_definition_revision=str(loop.get("definition_revision") or record.definition_revision),
        loop_definition=definition if isinstance(definition, dict) else None,
        current_stage_id=str(loop.get("current_stage_id") or "") or None,
        stages=stages,
        root_path=str(state.get("root_path") or ""),
        workspace_path=str(state.get("workspace_path") or ""),
        provider=str(state.get("provider") or ""),
        model=str(state.get("model") or ""),
        options=options,
        started_at=str(state.get("started_at") or record.started_at.isoformat()),
        completed_at=(
            str(state["completed_at"])
            if state.get("completed_at")
            else record.completed_at.isoformat()
            if record.completed_at is not None
            else None
        ),
        accepted_at=(record.accepted_at.isoformat() if record.accepted_at is not None else None),
        cancelled_at=(record.cancelled_at.isoformat() if record.cancelled_at is not None else None),
        cleanup_at=(record.cleanup_at.isoformat() if record.cleanup_at is not None else None),
        elapsed_seconds=elapsed,
        cost_usd=float(raw_cost) if isinstance(raw_cost, int | float) else None,
        summary=str(state.get("summary") or ""),
        changed_files=[
            PlanLoopChangedFileResponse(
                path=changed.path,
                additions=changed.additions,
                deletions=changed.deletions,
            )
            for changed in changed_by_path.values()
        ],
        evidence=evidence,
        source_run_id=(str(state["source_run_id"]) if state.get("source_run_id") else None),
        run_kind=run_kind,
        seed_label=str(state.get("seed_label") or ""),
        brief=_to_loop_brief_schema(brief) if brief is not None else None,
        review_gate=(
            dict(loop["review_gate"]) if isinstance(loop.get("review_gate"), dict) else None
        ),
        waived_findings_count=len(
            [item for item in loop.get("waived_findings", []) if isinstance(item, str)]
            if isinstance(loop.get("waived_findings"), list)
            else []
        ),
        pass_number=max(1, loop_actions.int_or_default(loop.get("pass_number"), 1)),
        passes=[dict(item) for item in loop.get("passes", []) if isinstance(item, dict)]
        if isinstance(loop.get("passes"), list)
        else [],
        pr=(dict(loop["pr"]) if isinstance(loop.get("pr"), dict) else None),
        pr_comments=[dict(item) for item in loop.get("pr_comments", []) if isinstance(item, dict)]
        if isinstance(loop.get("pr_comments"), list)
        else [],
    )


def _to_loop_brief(payload: LoopBriefSchema) -> LoopBrief:
    """Map one validated HTTP brief into framework-free domain values."""
    return LoopBrief(
        goal=payload.goal,
        stages=tuple(
            LoopStageBrief(
                stage_id=stage.stage_id,
                note=stage.note,
                context=tuple(
                    LoopBriefContext(kind=context.kind, value=context.value)
                    for context in stage.context
                ),
                agent=(
                    LoopBriefAgent(
                        provider=stage.agent.provider,
                        model=stage.agent.model,
                        options=dict(stage.agent.options),
                    )
                    if stage.agent is not None
                    else None
                ),
                review_gate=(
                    LoopReviewGateMode(stage.review_gate) if stage.review_gate is not None else None
                ),
                approved_command_prefixes=(
                    tuple(stage.approved_command_prefixes)
                    if stage.approved_command_prefixes is not None
                    else None
                ),
            )
            for stage in payload.stages
        ),
    )


def _to_loop_brief_schema(brief: LoopBrief) -> LoopBriefSchema:
    """Map the canonical domain brief into its shared REST shape."""
    return LoopBriefSchema.model_validate(brief_snapshot(brief))


def _to_plan_proposal(
    proposal: PlanArtifactProposal,
) -> PlanArtifactProposalResponse:
    return PlanArtifactProposalResponse(
        id=proposal.id,
        artifact_id=proposal.artifact_id,
        title=proposal.title,
        path=proposal.path,
        source_hash=proposal.source_hash,
        proposed_content=proposal.proposed_content,
        status=proposal.status,
        created_at=proposal.created_at,
        resolved_at=proposal.resolved_at,
    )


def _to_plan_tracking(link: PlanTrackingLink) -> PlanTrackingLinkResponse:
    return PlanTrackingLinkResponse(
        id=link.id,
        kind=link.kind,
        title=link.title,
        url=link.url,
        status=link.status,
        ref=link.ref,
        notes=link.notes,
        created_at=link.created_at,
    )


def _require_slug(work: Work) -> str:
    if work.slug is None:
        raise RuntimeError("persisted Work has no slug")
    return work.slug


def _require_chat_slug(chat: Chat) -> str:
    if chat.slug is None:
        raise RuntimeError("persisted Chat has no slug")
    return chat.slug
