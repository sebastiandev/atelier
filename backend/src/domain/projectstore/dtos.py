"""Request/response DTOs for the ProjectStore port.

Inputs are frozen so command callers can't mutate them after dispatch.
``ProjectRecord`` mirrors ``WorkRecord``'s shape so the boundary feels
familiar even though Project has no FS-only sidecar today (contexts on
Work live in work.json, hence the bundle).
"""

from dataclasses import dataclass

from src.domain.models import Project


@dataclass(frozen=True)
class CreateProjectRequest:
    name: str
    description: str
    glyph: str
    color: int
    pinned: bool = False
    default_jira_conn: str | None = None
    default_sentry_conn: str | None = None


@dataclass(frozen=True)
class UpdateProjectRequest:
    """Partial update — fields left as ``None`` are not changed."""

    project_slug: str
    name: str | None = None
    description: str | None = None
    glyph: str | None = None
    color: int | None = None
    pinned: bool | None = None
    default_jira_conn: str | None = None
    default_sentry_conn: str | None = None


@dataclass(frozen=True)
class ProjectRecord:
    project: Project


__all__ = [
    "CreateProjectRequest",
    "ProjectRecord",
    "UpdateProjectRequest",
]
