"""Works REST router.

Thin endpoints — parse the pydantic request, build the domain DTO, hand
off to the matching command, format the result. No business logic here;
that lives behind the WorkStore port.
"""

import asyncio
import logging
import subprocess
from datetime import UTC, datetime
from pathlib import Path
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Request, status

from src.application.http.schemas import (
    AcceptPlanArtifactRequest,
    ArtifactSummary,
    ChatDetail,
    ChatGroundingSchema,
    ChatMessageSchema,
    CompleteWorkResponse,
    ContextSchema,
    CreatePlanArtifactProposalRequest,
    CreatePlanBugRequest,
    HandoffSummary,
    LinkPlanArtifactTrackingRequest,
    MoveWorkRequest,
    NewHandoffRequest,
    NewWorkRequest,
    PatchWorkRequest,
    PlanArtifactDetailResponse,
    PlanArtifactProposalResponse,
    PlanArtifactResponse,
    PlanArtifactRunResponse,
    PlanMaterializationStatusResponse,
    PlanOverviewResponse,
    PlanTrackingLinkResponse,
    PlanningChatReadinessResponse,
    PlanningFrameworkStatusRequest,
    PlanningFrameworkStatusResponse,
    RecordPlanArtifactRunRequest,
    StartPlanningChatRequest,
    StartPlanningSetupChatRequest,
    StartWorkPlanRequest,
    StartWorkPlanResponse,
    SubmitPlanArtifactReportRequest,
    UpdatePlanArtifactRequest,
    WorkChatContextDocResponse,
    WorkChatContextFolderSummary,
    WorkChatRef,
    WorkDetail,
    WorkPlanResponse,
    WorkSummary,
)
from src.domain.agents.handoffs import (
    BuildHandoffRequest,
    Summarizer,
    build_handoff,
)
from src.domain.commands.planning import (
    accept_artifact as planning_accept_artifact,
)
from src.domain.commands.planning import (
    approve as planning_approve,
)
from src.domain.commands.planning import (
    create_bug as planning_create_bug,
)
from src.domain.commands.planning import (
    materialize as planning_materialize,
)
from src.domain.commands.planning import (
    finish as planning_finish,
)
from src.domain.commands.planning import (
    get as planning_get,
)
from src.domain.commands.planning import (
    ingest_report as planning_ingest_report,
)
from src.domain.commands.planning import (
    link_tracking as planning_link_tracking,
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
    propose_update as planning_propose_update,
)
from src.domain.commands.planning import (
    record_run as planning_record_run,
)
from src.domain.commands.planning import (
    resolve_proposal as planning_resolve_proposal,
)
from src.domain.commands.planning import (
    setup_chat as planning_setup_chat,
)
from src.domain.commands.planning import (
    start_chat as planning_start_chat,
)
from src.domain.commands.planning import (
    submit_materialization as planning_submit_materialization,
)
from src.domain.commands.planning import (
    submit_report as planning_submit_report,
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
from src.domain.chatstore import ChatRecord, ChatStore
from src.domain.models import Chat, ChatMessage, Context, Handoff, Work
from src.domain.planning.dtos import (
    PlanArtifactDetail,
    PlanArtifactProposal,
    PlanArtifactRun,
    PlanArtifactSummary,
    PlanOverview,
    PlanningFrameworkStatus,
    PlanTrackingLink,
    WorkPlanView,
)
from src.domain.planning.frameworks import check_framework_status
from src.domain.planning.ports import PlanningFiles
from src.domain.planning.readiness import (
    PlanningChatReadiness,
    planning_readiness_from_record,
)
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
_log = logging.getLogger(__name__)


def get_workstore(request: Request) -> WorkStore:
    """FastAPI dependency: pull the WorkStore off the app state set up in lifespan."""
    return request.app.state.workstore  # type: ignore[no-any-return]


def get_projectstore(request: Request) -> ProjectStore:
    return request.app.state.projectstore  # type: ignore[no-any-return]


def get_planningfiles(request: Request) -> PlanningFiles:
    return request.app.state.planningfiles  # type: ignore[no-any-return]


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


WorkStoreDep = Annotated[WorkStore, Depends(get_workstore)]
ProjectStoreDep = Annotated[ProjectStore, Depends(get_projectstore)]
PlanningFilesDep = Annotated[PlanningFiles, Depends(get_planningfiles)]
ChatStoreDep = Annotated[ChatStore, Depends(get_chatstore)]
SettingsDep = Annotated[Settings, Depends(get_settings_dep)]
SupervisorDep = Annotated[AgentSupervisorService, Depends(get_supervisor)]
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
    return _to_framework_status(
        check_framework_status(payload.framework, payload.root_path)
    )


@router.post("/works/{work_slug}/planning-chat", response_model=ChatDetail)
def start_planning_chat_endpoint(
    work_slug: str,
    payload: StartPlanningChatRequest,
    workstore: WorkStoreDep,
    chatstore: ChatStoreDep,
) -> ChatDetail:
    req = planning_start_chat.StartPlanningChatRequest(
        work_slug=work_slug,
        root_path=payload.root_path,
        idea=payload.idea,
        framework=payload.framework,
        profile=payload.profile,
        provider=payload.provider,
        model=payload.model,
        options=payload.options,
    )
    try:
        planning_start_chat.validate_provider_config(req)
        record, _framework_status = planning_start_chat.execute(
            workstore, chatstore, req
        )
    except planning_start_chat.WorkNotFound as e:
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail=str(e)) from e
    except planning_start_chat.PlanningFrameworkNotReady as e:
        raise HTTPException(status.HTTP_409_CONFLICT, detail=str(e)) from e
    except ValueError as e:
        raise HTTPException(
            status.HTTP_422_UNPROCESSABLE_ENTITY, detail=str(e)
        ) from e
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
        record, _framework_status = planning_setup_chat.execute(
            workstore, chatstore, req
        )
    except planning_setup_chat.WorkNotFound as e:
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail=str(e)) from e
    except planning_setup_chat.PlanningFrameworkAlreadyReady as e:
        raise HTTPException(status.HTTP_409_CONFLICT, detail=str(e)) from e
    except ValueError as e:
        raise HTTPException(
            status.HTTP_422_UNPROCESSABLE_ENTITY, detail=str(e)
        ) from e
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
    chat_supervisor: ChatSupervisorDep,
    settings: SettingsDep,
) -> StartWorkPlanResponse:
    req = planning_materialize.MaterializePlanRequest(
        work_slug=work_slug,
        root_path=payload.root_path,
        framework=payload.framework,
        profile=payload.profile,
        provider=payload.provider,
        model=payload.model,
        options=payload.options,
        planning_chat_slug=payload.planning_chat_slug,
    )
    chat_req = planning_materialization_chat.StartPlanningMaterializationChatRequest(
        work_slug=work_slug,
        root_path=payload.root_path,
        framework=payload.framework,
        profile=payload.profile,
        provider=payload.provider,
        model=payload.model,
        options=payload.options,
        planning_chat_slug=payload.planning_chat_slug,
    )
    try:
        planning_materialization_chat.validate_provider_config(chat_req)
        record, _status = planning_materialization_chat.execute(
            workstore, chatstore, chat_req
        )
        if record.chat.slug is None:
            raise ValueError("materialization chat has no slug")
        plan = await planning_materialize.try_finalize_existing(
            workstore,
            chatstore,
            planningfiles,
            chat_supervisor,
            req,
            record.chat.slug,
        )
        if plan is not None:
            return StartWorkPlanResponse(
                plan=_to_plan_response(plan),
                materialization_status=_to_plan_materialization_status_response(
                    planning_materialization_status.execute(
                        workstore, chatstore, planningfiles, work_slug
                    )
                ),
            )
        _ensure_plan_materialization_task(
            request,
            workstore,
            chatstore,
            projectstore,
            planningfiles,
            chat_supervisor,
            settings,
            req,
        )
        return _to_start_work_plan_response(
            workstore, chatstore, planningfiles, work_slug
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
        raise HTTPException(
            status.HTTP_422_UNPROCESSABLE_ENTITY, detail=str(e)
        ) from e


def _ensure_plan_materialization_task(
    request: Request,
    workstore: WorkStore,
    chatstore: ChatStore,
    projectstore: ProjectStore,
    planningfiles: PlanningFiles,
    chat_supervisor: AgentSupervisorService,
    settings: Settings,
    req: planning_materialize.MaterializePlanRequest,
) -> None:
    key = f"{req.work_slug}:{req.root_path}"
    tasks = getattr(request.app.state, "planning_materialization_tasks", None)
    if tasks is None:
        tasks = {}
        request.app.state.planning_materialization_tasks = tasks
    existing = tasks.get(key)
    if existing is not None and not existing.done():
        return
    task = asyncio.create_task(
        planning_materialize.execute(
            workstore,
            chatstore,
            projectstore,
            planningfiles,
            chat_supervisor,
            settings,
            req,
        ),
        name=f"planning-materialize-{req.work_slug}",
    )
    tasks[key] = task

    def _clear(done: asyncio.Task[WorkPlanView]) -> None:
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


def _to_start_work_plan_response(
    workstore: WorkStore,
    chatstore: ChatStore,
    planningfiles: PlanningFiles,
    work_slug: str,
) -> StartWorkPlanResponse:
    materialization = planning_materialization_status.execute(
        workstore, chatstore, planningfiles, work_slug
    )
    plan = None
    if materialization.state == "complete":
        try:
            plan = _to_plan_response(
                planning_get.execute(workstore, planningfiles, work_slug)
            )
        except (planning_get.WorkNotFound, planning_get.PlanNotFound):
            plan = None
    return StartWorkPlanResponse(
        plan=plan,
        materialization_status=_to_plan_materialization_status_response(
            materialization
        ),
    )


@router.get("/works/{work_slug}/plan", response_model=WorkPlanResponse)
def get_work_plan_endpoint(
    work_slug: str,
    workstore: WorkStoreDep,
    planningfiles: PlanningFilesDep,
) -> WorkPlanResponse:
    try:
        view = planning_get.execute(workstore, planningfiles, work_slug)
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
) -> PlanMaterializationStatusResponse:
    try:
        view = planning_materialization_status.execute(
            workstore, chatstore, planningfiles, work_slug
        )
    except planning_materialization_status.WorkNotFound as e:
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail=str(e)) from e
    return _to_plan_materialization_status_response(view)


@router.post("/works/{work_slug}/plan/approve", response_model=WorkPlanResponse)
def approve_work_plan_endpoint(
    work_slug: str,
    workstore: WorkStoreDep,
    planningfiles: PlanningFilesDep,
) -> WorkPlanResponse:
    try:
        view = planning_approve.execute(workstore, planningfiles, work_slug)
    except (planning_approve.WorkNotFound, planning_approve.PlanningNotStarted) as e:
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail=str(e)) from e
    return _to_plan_response(view)


@router.post("/works/{work_slug}/plan/finish", response_model=WorkPlanResponse)
def finish_work_plan_conversation_endpoint(
    work_slug: str,
    workstore: WorkStoreDep,
    planningfiles: PlanningFilesDep,
) -> WorkPlanResponse:
    try:
        view = planning_finish.execute(workstore, planningfiles, work_slug)
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
) -> PlanArtifactDetailResponse:
    try:
        detail = planning_get.artifact(
            workstore, planningfiles, work_slug, artifact_id
        )
    except (
        planning_get.WorkNotFound,
        planning_get.PlanNotFound,
        planning_get.ArtifactNotFound,
    ) as e:
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail=str(e)) from e
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
) -> PlanArtifactDetailResponse:
    try:
        detail = planning_update_artifact.execute(
            workstore,
            planningfiles,
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
) -> PlanArtifactDetailResponse:
    try:
        detail = planning_propose_update.execute(
            workstore,
            planningfiles,
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
) -> PlanArtifactDetailResponse:
    try:
        detail = planning_resolve_proposal.execute(
            workstore,
            planningfiles,
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
) -> PlanArtifactDetailResponse:
    try:
        detail = planning_resolve_proposal.execute(
            workstore,
            planningfiles,
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
def record_work_plan_artifact_run_endpoint(
    work_slug: str,
    artifact_id: str,
    payload: RecordPlanArtifactRunRequest,
    workstore: WorkStoreDep,
    planningfiles: PlanningFilesDep,
) -> PlanArtifactDetailResponse:
    try:
        detail = planning_record_run.execute(
            workstore,
            planningfiles,
            planning_record_run.RecordArtifactRunRequest(
                work_slug=work_slug,
                artifact_id=artifact_id,
                agent_slug=payload.agent_slug,
            ),
        )
    except (
        planning_record_run.WorkNotFound,
        planning_record_run.PlanningNotStarted,
        planning_record_run.PlanArtifactNotFound,
    ) as e:
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail=str(e)) from e
    except planning_record_run.PlanArtifactNotExecutable as e:
        raise HTTPException(
            status.HTTP_422_UNPROCESSABLE_ENTITY, detail=str(e)
        ) from e
    return _to_plan_detail_response(detail)


@router.post(
    "/works/{work_slug}/plan/artifacts/{artifact_id}/runs/{agent_slug}/ingest-report",
    response_model=PlanArtifactDetailResponse,
)
def ingest_work_plan_artifact_report_endpoint(
    work_slug: str,
    artifact_id: str,
    agent_slug: str,
    workstore: WorkStoreDep,
    planningfiles: PlanningFilesDep,
) -> PlanArtifactDetailResponse:
    try:
        detail = planning_ingest_report.execute(
            workstore,
            planningfiles,
            planning_ingest_report.IngestArtifactReportRequest(
                work_slug=work_slug,
                artifact_id=artifact_id,
                agent_slug=agent_slug,
            ),
        )
    except (
        planning_ingest_report.WorkNotFound,
        planning_ingest_report.AgentNotFound,
        planning_ingest_report.PlanningNotStarted,
        planning_ingest_report.PlanArtifactNotFound,
    ) as e:
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail=str(e)) from e
    except (
        planning_ingest_report.PlanArtifactNotExecutable,
        planning_ingest_report.TranscriptReportNotFound,
    ) as e:
        raise HTTPException(
            status.HTTP_422_UNPROCESSABLE_ENTITY, detail=str(e)
        ) from e
    return _to_plan_detail_response(detail)


@router.post(
    "/works/{work_slug}/plan/artifacts/{artifact_id}/runs/{agent_slug}/cleanup",
    response_model=PlanArtifactDetailResponse,
)
def mark_work_plan_run_cleaned_endpoint(
    work_slug: str,
    artifact_id: str,
    agent_slug: str,
    workstore: WorkStoreDep,
    planningfiles: PlanningFilesDep,
) -> PlanArtifactDetailResponse:
    try:
        detail = planning_mark_run_cleaned.execute(
            workstore,
            planningfiles,
            planning_mark_run_cleaned.MarkArtifactRunCleanedRequest(
                work_slug=work_slug,
                artifact_id=artifact_id,
                agent_slug=agent_slug,
            ),
        )
    except (
        planning_mark_run_cleaned.WorkNotFound,
        planning_mark_run_cleaned.PlanningNotStarted,
        planning_mark_run_cleaned.PlanArtifactNotFound,
        planning_mark_run_cleaned.PlanArtifactRunNotFound,
    ) as e:
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail=str(e)) from e
    return _to_plan_detail_response(detail)


@router.post(
    "/works/{work_slug}/plan/artifacts/{artifact_id}/report",
    response_model=PlanArtifactDetailResponse,
)
def submit_work_plan_artifact_report_endpoint(
    work_slug: str,
    artifact_id: str,
    payload: SubmitPlanArtifactReportRequest,
    workstore: WorkStoreDep,
    planningfiles: PlanningFilesDep,
) -> PlanArtifactDetailResponse:
    try:
        detail = planning_submit_report.execute(
            workstore,
            planningfiles,
            planning_submit_report.SubmitArtifactReportRequest(
                work_slug=work_slug,
                artifact_id=artifact_id,
                agent_slug=payload.agent_slug,
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
        planning_submit_report.WorkNotFound,
        planning_submit_report.PlanningNotStarted,
        planning_submit_report.PlanArtifactNotFound,
    ) as e:
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail=str(e)) from e
    except planning_submit_report.PlanArtifactNotExecutable as e:
        raise HTTPException(
            status.HTTP_422_UNPROCESSABLE_ENTITY, detail=str(e)
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
) -> PlanArtifactDetailResponse:
    try:
        detail = planning_link_tracking.execute(
            workstore,
            planningfiles,
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
) -> PlanArtifactDetailResponse:
    try:
        detail = planning_create_bug.execute(
            workstore,
            planningfiles,
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


@router.post(
    "/works/{work_slug}/plan/artifacts/{artifact_id}/accept",
    response_model=PlanArtifactDetailResponse,
)
def accept_work_plan_artifact_endpoint(
    work_slug: str,
    artifact_id: str,
    payload: AcceptPlanArtifactRequest,
    workstore: WorkStoreDep,
    planningfiles: PlanningFilesDep,
) -> PlanArtifactDetailResponse:
    try:
        detail = planning_accept_artifact.execute(
            workstore,
            planningfiles,
            planning_accept_artifact.AcceptArtifactRequest(
                work_slug=work_slug,
                artifact_id=artifact_id,
                summary=payload.summary,
                agent_slug=payload.agent_slug,
                divergences=payload.divergences,
                skipped_scope=payload.skipped_scope,
                blockers=payload.blockers,
                decisions=payload.decisions,
                changes=payload.changes,
                validation_evidence=payload.validation_evidence,
            ),
        )
    except (
        planning_accept_artifact.WorkNotFound,
        planning_accept_artifact.PlanningNotStarted,
        planning_accept_artifact.PlanArtifactNotFound,
    ) as e:
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail=str(e)) from e
    except planning_accept_artifact.PlanArtifactNotExecutable as e:
        raise HTTPException(
            status.HTTP_422_UNPROCESSABLE_ENTITY, detail=str(e)
        ) from e
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


def _to_chat_detail(record: ChatRecord) -> ChatDetail:
    chat = record.chat
    summary = {
        "slug": _require_chat_slug(chat),
        "title": chat.title,
        "provider": chat.provider,
        "model": chat.model,
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
        "planning_readiness": _planning_readiness_to_schema(
            planning_readiness_from_record(record)
        ),
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
        loop_status=run.loop_status,
        loop_status_reason=run.loop_status_reason,
        loop_attempt=run.loop_attempt,
        loop_latest_assessment=run.loop_latest_assessment,
    )


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
