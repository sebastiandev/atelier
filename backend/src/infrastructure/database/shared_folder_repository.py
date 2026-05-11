"""SQLAlchemy implementation of ``ShareRepository``.

Mirrors ``SqlProjectRepository``: short-transaction context manager,
two-flush slug derivation (``shr-{id}``).
"""

from __future__ import annotations

import uuid
from collections.abc import Iterator
from contextlib import contextmanager

from sqlalchemy import select
from sqlalchemy.orm import Session, sessionmaker

from src.domain.models import SharedFolder
from src.infrastructure.database.tables import shared_folders_table


class SqlShareRepository:
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

    def add(self, share: SharedFolder) -> SharedFolder:
        with self._txn() as session:
            share.slug = _placeholder_slug()
            session.add(share)
            session.flush()
            assert share.id is not None
            share.slug = f"shr-{share.id}"
        return share

    def update(self, share: SharedFolder) -> SharedFolder:
        if share.slug is None:
            raise ValueError("update requires slug")
        with self._txn() as session:
            existing = session.execute(
                select(SharedFolder).where(
                    shared_folders_table.c.slug == share.slug
                )
            ).scalar_one_or_none()
            if existing is None:
                session.add(share)
            else:
                existing.name = share.name
                # mount_path immutable post-creation by design
                existing.real_path = share.real_path
        return share

    def get_by_slug(self, share_slug: str) -> SharedFolder | None:
        with self._txn() as session:
            return session.execute(
                select(SharedFolder).where(
                    shared_folders_table.c.slug == share_slug
                )
            ).scalar_one_or_none()

    def get_by_mount_path(
        self, project_id: int, mount_path: str
    ) -> SharedFolder | None:
        with self._txn() as session:
            return session.execute(
                select(SharedFolder).where(
                    shared_folders_table.c.project_id == project_id,
                    shared_folders_table.c.mount_path == mount_path,
                )
            ).scalar_one_or_none()

    def list_for_project(self, project_id: int) -> list[SharedFolder]:
        with self._txn() as session:
            return list(
                session.execute(
                    select(SharedFolder)
                    .where(shared_folders_table.c.project_id == project_id)
                    .order_by(shared_folders_table.c.created_at)
                )
                .scalars()
                .all()
            )

    def delete(self, share_slug: str) -> None:
        with self._txn() as session:
            existing = session.execute(
                select(SharedFolder).where(
                    shared_folders_table.c.slug == share_slug
                )
            ).scalar_one_or_none()
            if existing is not None:
                session.delete(existing)


def _placeholder_slug() -> str:
    return f"_pending_{uuid.uuid4().hex}"


__all__ = ["SqlShareRepository"]
