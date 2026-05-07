"""SQLAlchemy implementation of ``ProjectRepository``.

Mirrors ``SqlWorkRepository``: short-transaction context manager, two-flush
slug derivation (insert with placeholder slug → flush to allocate id →
rewrite slug), and the same ``PRJ-{id:03d}`` zero-pad as Work.
"""

import uuid
from collections.abc import Iterator
from contextlib import contextmanager

from sqlalchemy import select
from sqlalchemy.orm import Session, sessionmaker

from src.domain.models import Project
from src.infrastructure.database.tables import projects_table


class SqlProjectRepository:
    def __init__(self, factory: sessionmaker[Session]) -> None:
        self._factory = factory

    @contextmanager
    def _txn(self) -> Iterator[Session]:
        session = self._factory()
        try:
            yield session
            session.commit()
        except Exception:
            session.rollback()
            raise
        finally:
            session.close()

    def add_project(self, project: Project) -> Project:
        with self._txn() as session:
            project.slug = _placeholder_slug()
            session.add(project)
            session.flush()
            assert project.id is not None
            project.slug = f"PRJ-{project.id:03d}"
        return project

    def upsert_project(self, project: Project) -> Project:
        if project.slug is None:
            raise ValueError("upsert_project requires slug")
        with self._txn() as session:
            existing = session.execute(
                select(Project).where(projects_table.c.slug == project.slug)
            ).scalar_one_or_none()
            if existing is None:
                session.add(project)
            else:
                existing.name = project.name
                existing.description = project.description
                existing.glyph = project.glyph
                existing.color = project.color
                existing.pinned = project.pinned
                existing.default_jira_conn = project.default_jira_conn
                existing.default_sentry_conn = project.default_sentry_conn
                existing.created_at = project.created_at
        return project

    def delete_project(self, project_slug: str) -> None:
        with self._txn() as session:
            existing = session.execute(
                select(Project).where(projects_table.c.slug == project_slug)
            ).scalar_one_or_none()
            if existing is not None:
                session.delete(existing)

    def get_project_by_slug(self, slug: str) -> Project | None:
        with self._txn() as session:
            return session.execute(
                select(Project).where(projects_table.c.slug == slug)
            ).scalar_one_or_none()

    def list_projects(self) -> list[Project]:
        with self._txn() as session:
            return list(session.execute(select(Project)).scalars().all())


def _placeholder_slug() -> str:
    return f"_pending_{uuid.uuid4().hex}"


__all__ = ["SqlProjectRepository"]
