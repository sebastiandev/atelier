"""Projects REST router.

Thin endpoints — parse pydantic, build the domain DTO, hand off to the
matching command, format the result. No business logic; that lives
behind the ProjectStore port.
"""

from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Request, status

from src.application.http.schemas import (
    NewProjectRequest,
    ProjectDetail,
    ProjectSummary,
)
from src.domain.commands.projects import create, get, list_all
from src.domain.models import Project
from src.domain.projectstore.dtos import CreateProjectRequest, ProjectRecord
from src.domain.projectstore.ports import ProjectStore

router = APIRouter()


def get_projectstore(request: Request) -> ProjectStore:
    return request.app.state.projectstore  # type: ignore[no-any-return]


ProjectStoreDep = Annotated[ProjectStore, Depends(get_projectstore)]


@router.get("/projects", response_model=list[ProjectSummary])
def list_projects_endpoint(projectstore: ProjectStoreDep) -> list[ProjectSummary]:
    return [_to_summary(p) for p in list_all.execute(projectstore)]


@router.post(
    "/projects",
    response_model=ProjectDetail,
    status_code=status.HTTP_201_CREATED,
)
def create_project_endpoint(
    payload: NewProjectRequest, projectstore: ProjectStoreDep
) -> ProjectDetail:
    record = create.execute(projectstore, _to_create_request(payload))
    return _to_detail(record)


@router.get("/projects/{project_slug}", response_model=ProjectDetail)
def get_project_endpoint(
    project_slug: str, projectstore: ProjectStoreDep
) -> ProjectDetail:
    record = get.execute(projectstore, project_slug)
    if record is None:
        raise HTTPException(
            status.HTTP_404_NOT_FOUND, detail=f"project not found: {project_slug}"
        )
    return _to_detail(record)


def _to_create_request(payload: NewProjectRequest) -> CreateProjectRequest:
    return CreateProjectRequest(
        name=payload.name,
        description=payload.description,
        glyph=payload.glyph,
        color=payload.color,
        pinned=payload.pinned,
        default_jira_conn=payload.default_jira_conn,
        default_sentry_conn=payload.default_sentry_conn,
    )


def _to_summary(project: Project) -> ProjectSummary:
    slug = _require_slug(project)
    return ProjectSummary(
        slug=slug,
        name=project.name,
        description=project.description,
        glyph=project.glyph,
        color=project.color,
        pinned=project.pinned,
        default_jira_conn=project.default_jira_conn,
        default_sentry_conn=project.default_sentry_conn,
        created_at=project.created_at,
    )


def _to_detail(record: ProjectRecord) -> ProjectDetail:
    summary = _to_summary(record.project)
    return ProjectDetail(**summary.model_dump())


def _require_slug(project: Project) -> str:
    if project.slug is None:
        raise RuntimeError("persisted Project has no slug")
    return project.slug
