"""Ports for the ProjectStore boundary.

Mirrors the WorkStore split. ``ProjectStore`` is the public port the
application layer depends on; ``ProjectRepository`` and ``ProjectFiles``
decompose it into testable pieces.

Project is metadata-only — it owns no children today (no agents/artifacts
under a project), so the store stays small. The user's intention is for
projects to grow into a home for shared contexts, agent roles, and
project-level artifacts; this module keeps the seam where those would
hang off.

Domain stays framework-free — these Protocols expose only stdlib + domain
types.
"""

from typing import Any, Protocol

from src.domain.models import Project
from src.domain.projectstore.dtos import (
    CreateProjectRequest,
    ProjectRecord,
    UpdateProjectRequest,
)


class ProjectStore(Protocol):
    """Public persistence boundary for Project metadata."""

    def create_project(self, req: CreateProjectRequest) -> ProjectRecord: ...

    def get_project(self, project_slug: str) -> ProjectRecord | None: ...

    def list_projects(self) -> list[Project]: ...

    def update_project(self, req: UpdateProjectRequest) -> ProjectRecord: ...

    def delete_project(self, project_slug: str) -> None: ...


class ProjectRepository(Protocol):
    """SQL-side row operations.

    ``add_project`` allocates the slug from the DB-assigned id (e.g.
    ``"PRJ-001"``) and returns the entity with both ``id`` and ``slug``
    populated. ``upsert_project`` takes a slug-bearing entity and
    insert-or-updates.
    """

    def add_project(self, project: Project) -> Project: ...
    def upsert_project(self, project: Project) -> Project: ...
    def delete_project(self, project_slug: str) -> None: ...
    def get_project_by_slug(self, slug: str) -> Project | None: ...
    def list_projects(self) -> list[Project]: ...


class ProjectFiles(Protocol):
    """Atomic-replace filesystem metadata under ``<workspace>/projects/``."""

    def ensure_project_dir(self, project_slug: str) -> None: ...

    def write_project_json(self, project_slug: str, data: dict[str, Any]) -> None: ...
    def read_project_json(self, project_slug: str) -> dict[str, Any] | None: ...

    def list_project_slugs(self) -> list[str]: ...

    def delete_project_dir(self, project_slug: str) -> None: ...


__all__ = ["ProjectFiles", "ProjectRepository", "ProjectStore"]
