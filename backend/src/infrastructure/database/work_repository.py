"""SQLAlchemy implementation of `WorkRepository`.

Each method opens a short transaction (commit on success, rollback on
exception). `add_*` methods derive the slug from the DB-assigned id by
inserting under a unique-uuid placeholder slug, flushing to allocate the
id, and replacing the slug before commit. The two-flush pattern is
isolated to this module so the rest of the codebase doesn't see it.

Slug formats follow the architecture convention:
  Work     → ``WRK-{id:03d}``
  Agent    → ``agt-{id}``
  Artifact → ``art-{id}``
  Handoff  → ``hnd-{id}``
"""

import uuid
from collections.abc import Iterator
from contextlib import contextmanager

from sqlalchemy import case, func, select, update
from sqlalchemy.orm import Session, sessionmaker

from src.domain.models import Agent, Artifact, Handoff, Work
from src.infrastructure.database.tables import (
    agents_table,
    artifacts_table,
    works_table,
)


class SqlWorkRepository:
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

    # -- Work ---------------------------------------------------------

    def add_work(self, work: Work) -> Work:
        with self._txn() as session:
            work.slug = _placeholder_slug()
            session.add(work)
            session.flush()
            assert work.id is not None
            work.slug = f"WRK-{work.id:03d}"
        return work

    def upsert_work(self, work: Work) -> Work:
        if work.slug is None:
            raise ValueError("upsert_work requires slug")
        with self._txn() as session:
            existing = session.execute(
                select(Work).where(works_table.c.slug == work.slug)
            ).scalar_one_or_none()
            if existing is None:
                session.add(work)
            else:
                existing.name = work.name
                existing.description = work.description
                existing.status = work.status
                existing.created_at = work.created_at
                existing.project_slug = work.project_slug
        return work

    def delete_work(self, work_slug: str) -> None:
        with self._txn() as session:
            existing = session.execute(
                select(Work).where(works_table.c.slug == work_slug)
            ).scalar_one_or_none()
            if existing is not None:
                session.delete(existing)

    def get_work_by_slug(self, slug: str) -> Work | None:
        with self._txn() as session:
            return session.execute(
                select(Work).where(works_table.c.slug == slug)
            ).scalar_one_or_none()

    def list_works(self) -> list[Work]:
        with self._txn() as session:
            return list(session.execute(select(Work)).scalars().all())

    def count_children_by_work_id(self) -> dict[int, dict[str, int]]:
        with self._txn() as session:
            agent_rows = session.execute(
                select(agents_table.c.work_id, func.count())
                .group_by(agents_table.c.work_id)
            ).all()
            artifact_rows = session.execute(
                select(artifacts_table.c.work_id, func.count())
                .group_by(artifacts_table.c.work_id)
            ).all()
        out: dict[int, dict[str, int]] = {}
        for wid, cnt in agent_rows:
            out.setdefault(wid, {"agents": 0, "artifacts": 0})["agents"] = cnt
        for wid, cnt in artifact_rows:
            out.setdefault(wid, {"agents": 0, "artifacts": 0})["artifacts"] = cnt
        return out

    # -- Agent --------------------------------------------------------

    def add_agent(self, agent: Agent) -> Agent:
        with self._txn() as session:
            agent.slug = _placeholder_slug()
            session.add(agent)
            session.flush()
            assert agent.id is not None
            agent.slug = f"agt-{agent.id}"
        return agent

    def upsert_agent(self, agent: Agent) -> Agent:
        if agent.slug is None:
            raise ValueError("upsert_agent requires slug")
        with self._txn() as session:
            existing = session.execute(
                select(Agent).where(agents_table.c.slug == agent.slug)
            ).scalar_one_or_none()
            if existing is None:
                session.add(agent)
            else:
                existing.work_id = agent.work_id
                existing.name = agent.name
                existing.persona = agent.persona
                existing.role = agent.role
                existing.provider = agent.provider
                existing.model = agent.model
                existing.folder = agent.folder
                existing.status = agent.status
                existing.started_at = agent.started_at
                existing.stopped_at = agent.stopped_at
                existing.session_id = agent.session_id
                existing.parent_session_id = agent.parent_session_id
        return agent

    def set_agent_session_id(self, agent_slug: str, session_id: str) -> None:
        # Atomic: when the new session_id differs from the current one,
        # capture the current as parent_session_id (linked-list lineage
        # for providers that fork on resume). Same sid or no prior sid →
        # parent stays as-is. Single UPDATE keeps the read+write race-free
        # without an explicit transaction round-trip.
        with self._txn() as session:
            session.execute(
                update(agents_table)
                .where(agents_table.c.slug == agent_slug)
                .values(
                    parent_session_id=case(
                        (
                            agents_table.c.session_id.is_not(None)
                            & (agents_table.c.session_id != session_id),
                            agents_table.c.session_id,
                        ),
                        else_=agents_table.c.parent_session_id,
                    ),
                    session_id=session_id,
                )
            )

    def set_agent_status(self, agent_slug: str, status: str) -> None:
        with self._txn() as session:
            session.execute(
                update(agents_table)
                .where(agents_table.c.slug == agent_slug)
                .values(status=status)
            )

    def delete_agent(self, agent_slug: str) -> None:
        with self._txn() as session:
            existing = session.execute(
                select(Agent).where(agents_table.c.slug == agent_slug)
            ).scalar_one_or_none()
            if existing is not None:
                session.delete(existing)

    def get_agent_by_slug(self, slug: str) -> Agent | None:
        with self._txn() as session:
            return session.execute(
                select(Agent).where(agents_table.c.slug == slug)
            ).scalar_one_or_none()

    def list_agents_for_work(self, work_slug: str) -> list[Agent]:
        with self._txn() as session:
            return list(
                session.execute(
                    select(Agent)
                    .join(works_table, agents_table.c.work_id == works_table.c.id)
                    .where(works_table.c.slug == work_slug)
                )
                .scalars()
                .all()
            )

    def get_work_slug_for_agent(self, agent_slug: str) -> str | None:
        with self._txn() as session:
            return session.execute(
                select(works_table.c.slug)
                .join(agents_table, agents_table.c.work_id == works_table.c.id)
                .where(agents_table.c.slug == agent_slug)
            ).scalar_one_or_none()

    # -- Artifact / Handoff -------------------------------------------

    def add_artifact(self, artifact: Artifact) -> Artifact:
        with self._txn() as session:
            artifact.slug = _placeholder_slug()
            session.add(artifact)
            session.flush()
            assert artifact.id is not None
            artifact.slug = f"art-{artifact.id}"
        return artifact

    def list_artifacts_for_work(self, work_slug: str) -> list[Artifact]:
        with self._txn() as session:
            work_id = session.execute(
                select(works_table.c.id).where(works_table.c.slug == work_slug)
            ).scalar_one_or_none()
            if work_id is None:
                return []
            return list(
                session.execute(
                    select(Artifact)
                    .where(artifacts_table.c.work_id == work_id)
                    .order_by(artifacts_table.c.created_at.asc())
                ).scalars()
            )

    def get_artifact_by_slug(self, slug: str) -> Artifact | None:
        with self._txn() as session:
            return session.execute(
                select(Artifact).where(artifacts_table.c.slug == slug)
            ).scalar_one_or_none()

    def add_handoff(self, handoff: Handoff) -> Handoff:
        with self._txn() as session:
            handoff.slug = _placeholder_slug()
            session.add(handoff)
            session.flush()
            assert handoff.id is not None
            handoff.slug = f"hnd-{handoff.id}"
        return handoff


def _placeholder_slug() -> str:
    """Unique sentinel that satisfies UNIQUE NOT NULL during the brief window
    between INSERT (which allocates the id) and the slug rewrite that follows."""
    return f"_pending_{uuid.uuid4().hex}"


__all__ = ["SqlWorkRepository"]
