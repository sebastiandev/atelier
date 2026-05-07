"""Works REST router.

Thin endpoints — parse the pydantic request, build the domain DTO, hand
off to the matching command, format the result. No business logic here;
that lives behind the WorkStore port.
"""

import subprocess
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Request, status

from src.application.http.schemas import (
    ContextSchema,
    NewWorkRequest,
    PatchWorkRequest,
    WorkDetail,
    WorkSummary,
)
from src.domain.commands.projects import get as projects_get
from src.domain.commands.works import create, get, list_all, soft_delete, update
from src.domain.models import Context, Work
from src.domain.projectstore.ports import ProjectStore
from src.domain.workstore.dtos import (
    CreateWorkRequest,
    UpdateWorkRequest,
    WorkRecord,
)
from src.domain.workstore.ports import WorkStore
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


WorkStoreDep = Annotated[WorkStore, Depends(get_workstore)]
ProjectStoreDep = Annotated[ProjectStore, Depends(get_projectstore)]
SettingsDep = Annotated[Settings, Depends(get_settings_dep)]


@router.get("/works", response_model=list[WorkSummary])
def list_works_endpoint(
    workstore: WorkStoreDep, settings: SettingsDep
) -> list[WorkSummary]:
    works = list_all.execute(workstore)
    paths = WorkspacePaths(workspace_root=settings.workspace_root)
    return [_to_summary(w, paths) for w in works]


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


# ---------------------------------------------------------------------------
# Translators between pydantic schemas and domain DTOs/entities
# ---------------------------------------------------------------------------


def _to_create_request(payload: NewWorkRequest) -> CreateWorkRequest:
    return CreateWorkRequest(
        name=payload.name,
        description=payload.description,
        contexts=[_to_domain_context(c) for c in payload.contexts],
        project_slug=payload.project_slug,
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


def _to_summary(work: Work, paths: WorkspacePaths) -> WorkSummary:
    slug = _require_slug(work)
    return WorkSummary(
        slug=slug,
        name=work.name,
        description=work.description,
        status=work.status,
        created_at=work.created_at,
        atelier_path=str(paths.work_dir(slug)),
        project_slug=work.project_slug,
    )


def _to_detail(record: WorkRecord, paths: WorkspacePaths) -> WorkDetail:
    summary = _to_summary(record.work, paths)
    return WorkDetail(
        **summary.model_dump(),
        contexts=[_to_schema_context(c) for c in record.contexts],
    )


def _require_slug(work: Work) -> str:
    if work.slug is None:
        raise RuntimeError("persisted Work has no slug")
    return work.slug
