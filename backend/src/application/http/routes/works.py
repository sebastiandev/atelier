"""Works REST router.

Thin endpoints — parse the pydantic request, build the domain DTO, hand
off to the matching command, format the result. No business logic here;
that lives behind the WorkStore port.
"""

from pathlib import Path
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Request, status

from src.application.http.schemas import (
    ContextSchema,
    NewWorkRequest,
    PatchWorkRequest,
    WorkDetail,
    WorkSummary,
)
from src.domain.commands.works import create, get, list_all, soft_delete, update
from src.domain.models import Context, Work
from src.domain.workstore.dtos import (
    CreateWorkRequest,
    UpdateWorkRequest,
    WorkRecord,
)
from src.domain.workstore.ports import WorkStore

router = APIRouter()


def get_workstore(request: Request) -> WorkStore:
    """FastAPI dependency: pull the WorkStore off the app state set up in lifespan."""
    return request.app.state.workstore  # type: ignore[no-any-return]


WorkStoreDep = Annotated[WorkStore, Depends(get_workstore)]


@router.get("/works", response_model=list[WorkSummary])
def list_works_endpoint(workstore: WorkStoreDep) -> list[WorkSummary]:
    works = list_all.execute(workstore)
    return [_to_summary(w) for w in works]


@router.post("/works", response_model=WorkDetail, status_code=status.HTTP_201_CREATED)
def create_work_endpoint(payload: NewWorkRequest, workstore: WorkStoreDep) -> WorkDetail:
    record = create.execute(workstore, _to_create_request(payload))
    return _to_detail(record)


@router.get("/works/{work_slug}", response_model=WorkDetail)
def get_work_endpoint(work_slug: str, workstore: WorkStoreDep) -> WorkDetail:
    record = get.execute(workstore, work_slug)
    if record is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail=f"work not found: {work_slug}")
    return _to_detail(record)


@router.patch("/works/{work_slug}", response_model=WorkDetail)
def patch_work_endpoint(
    work_slug: str, payload: PatchWorkRequest, workstore: WorkStoreDep
) -> WorkDetail:
    try:
        record = update.execute(workstore, _to_update_request(work_slug, payload))
    except ValueError as e:
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail=str(e)) from e
    return _to_detail(record)


@router.delete("/works/{work_slug}", status_code=status.HTTP_204_NO_CONTENT)
def delete_work_endpoint(work_slug: str, workstore: WorkStoreDep) -> None:
    try:
        soft_delete.execute(workstore, work_slug)
    except ValueError as e:
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail=str(e)) from e


# ---------------------------------------------------------------------------
# Translators between pydantic schemas and domain DTOs/entities
# ---------------------------------------------------------------------------


def _to_create_request(payload: NewWorkRequest) -> CreateWorkRequest:
    # Expand ~ here so the persisted folder is canonical — the rest of
    # the stack (worktree manager, adapter cwd, mkdir-on-start) sees a
    # real absolute path instead of a tilde-relative string.
    return CreateWorkRequest(
        name=payload.name,
        description=payload.description,
        folder=Path(payload.folder).expanduser(),
        contexts=[_to_domain_context(c) for c in payload.contexts],
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


def _to_summary(work: Work) -> WorkSummary:
    return WorkSummary(
        slug=_require_slug(work),
        name=work.name,
        description=work.description,
        folder=str(work.folder),
        status=work.status,
        created_at=work.created_at,
    )


def _to_detail(record: WorkRecord) -> WorkDetail:
    summary = _to_summary(record.work)
    return WorkDetail(
        **summary.model_dump(),
        contexts=[_to_schema_context(c) for c in record.contexts],
    )


def _require_slug(work: Work) -> str:
    if work.slug is None:
        raise RuntimeError("persisted Work has no slug")
    return work.slug
