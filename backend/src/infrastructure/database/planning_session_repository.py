"""SQLAlchemy implementation of PlanningSessionRepository."""

from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager

from sqlalchemy import select
from sqlalchemy.orm import Session, sessionmaker

from src.domain.planning.models import PlanningSession
from src.infrastructure.database.tables import planning_sessions_table


class SqlPlanningSessionRepository:
    """Persist one PlanningSession row per Work."""

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

    def upsert_session(self, session: PlanningSession) -> PlanningSession:
        with self._txn() as db:
            existing = db.execute(
                select(PlanningSession).where(
                    planning_sessions_table.c.work_slug == session.work_slug
                )
            ).scalar_one_or_none()
            if existing is None:
                db.add(session)
                db.flush()
                return session
            existing.planning_chat_slug = session.planning_chat_slug
            existing.root_path = session.root_path
            existing.artifact_root_path = session.artifact_root_path
            existing.framework = session.framework
            existing.profile = session.profile
            existing.provider = session.provider
            existing.model = session.model
            existing.options = session.options
            existing.updated_at = session.updated_at
            return existing

    def get_by_work_slug(self, work_slug: str) -> PlanningSession | None:
        with self._txn() as db:
            return db.execute(
                select(PlanningSession).where(
                    planning_sessions_table.c.work_slug == work_slug
                )
            ).scalar_one_or_none()

    def get_by_chat_slug(self, chat_slug: str) -> PlanningSession | None:
        with self._txn() as db:
            return db.execute(
                select(PlanningSession).where(
                    planning_sessions_table.c.planning_chat_slug == chat_slug
                )
            ).scalar_one_or_none()


__all__ = ["SqlPlanningSessionRepository"]
