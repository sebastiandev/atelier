"""ProjectStoreService — pure-domain implementation of the ProjectStore port.

Composes ``ProjectRepository`` (SQL) + ``ProjectFiles`` (atomic FS
metadata) under a process-local ``RLock``. Slug allocation and FS↔DB
ordering live here so the policy is testable with stub ports.

Ordering: persist DB first (the repo commits per call) and then write
FS. A crash between the two leaves an orphan DB row, which the next
startup ``reconcile`` reconciles against the canonical filesystem.
"""

from __future__ import annotations

from collections.abc import Callable
from datetime import UTC, datetime
from threading import RLock

from src.domain.models import Project
from src.domain.projectstore._serde import serialize_project_record
from src.domain.projectstore.dtos import (
    CreateProjectRequest,
    ProjectRecord,
    UpdateProjectRequest,
)
from src.domain.projectstore.ports import ProjectFiles, ProjectRepository

Clock = Callable[[], datetime]


def _utc_now() -> datetime:
    return datetime.now(UTC)


class ProjectStoreService:
    def __init__(
        self,
        repo: ProjectRepository,
        files: ProjectFiles,
        *,
        lock: RLock | None = None,
        clock: Clock = _utc_now,
    ) -> None:
        self._repo = repo
        self._files = files
        self._lock = lock if lock is not None else RLock()
        self._clock = clock

    def create_project(self, req: CreateProjectRequest) -> ProjectRecord:
        with self._lock:
            project = Project(
                name=req.name,
                description=req.description,
                glyph=req.glyph,
                color=req.color,
                pinned=req.pinned,
                default_jira_conn=req.default_jira_conn,
                default_sentry_conn=req.default_sentry_conn,
                created_at=self._clock(),
            )
            project = self._repo.add_project(project)
            slug = _require_slug(project)
            self._files.ensure_project_dir(slug)
            self._files.write_project_json(slug, serialize_project_record(project))
        return ProjectRecord(project=project)

    def get_project(self, project_slug: str) -> ProjectRecord | None:
        with self._lock:
            project = self._repo.get_project_by_slug(project_slug)
            if project is None:
                return None
        return ProjectRecord(project=project)

    def list_projects(self) -> list[Project]:
        with self._lock:
            return self._repo.list_projects()

    def update_project(self, req: UpdateProjectRequest) -> ProjectRecord:
        with self._lock:
            existing = self._repo.get_project_by_slug(req.project_slug)
            if existing is None:
                raise ValueError(f"project not found: {req.project_slug}")

            if req.name is not None:
                existing.name = req.name
            if req.description is not None:
                existing.description = req.description
            if req.glyph is not None:
                existing.glyph = req.glyph
            if req.color is not None:
                existing.color = req.color
            if req.pinned is not None:
                existing.pinned = req.pinned
            if req.default_jira_conn is not None:
                existing.default_jira_conn = req.default_jira_conn
            if req.default_sentry_conn is not None:
                existing.default_sentry_conn = req.default_sentry_conn

            self._repo.upsert_project(existing)
            self._files.write_project_json(
                req.project_slug, serialize_project_record(existing)
            )
        return ProjectRecord(project=existing)

    def delete_project(self, project_slug: str) -> None:
        with self._lock:
            existing = self._repo.get_project_by_slug(project_slug)
            if existing is None:
                raise ValueError(f"project not found: {project_slug}")
            # SQL FK with ON DELETE SET NULL demotes attached works to "loose"
            # automatically. Filesystem cleanup is best-effort: project.json
            # vanishes, but anything else under the project dir (future
            # contexts, agent roles) sticks around for the user to inspect.
            self._repo.delete_project(project_slug)
            self._files.delete_project_dir(project_slug)


def _require_slug(project: Project) -> str:
    if project.slug is None:
        raise RuntimeError(
            "repository returned Project without slug — "
            "the adapter must populate it during add_project"
        )
    return project.slug


__all__ = ["ProjectStoreService"]
