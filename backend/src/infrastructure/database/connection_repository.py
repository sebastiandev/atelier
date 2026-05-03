"""SQLAlchemy implementation of `ConnectionRepository`.

Same short-transaction discipline as `SqlWorkRepository`. The slug is
derived from the DB-assigned id via the placeholder-then-rewrite pattern
(``con-{id}``).
"""

import uuid
from collections.abc import Iterator
from contextlib import contextmanager

from sqlalchemy import select
from sqlalchemy.orm import Session, sessionmaker

from src.domain.models import Connection
from src.infrastructure.database.tables import connections_table


class SqlConnectionRepository:
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

    def add(self, connection: Connection) -> Connection:
        with self._txn() as session:
            connection.slug = _placeholder_slug()
            session.add(connection)
            session.flush()
            assert connection.id is not None
            connection.slug = f"con-{connection.id}"
        return connection

    def upsert(self, connection: Connection) -> Connection:
        if connection.slug is None:
            raise ValueError("upsert requires slug")
        with self._txn() as session:
            existing = session.execute(
                select(Connection).where(connections_table.c.slug == connection.slug)
            ).scalar_one_or_none()
            if existing is None:
                session.add(connection)
            else:
                existing.type = connection.type
                existing.name = connection.name
                existing.url = connection.url
                existing.org = connection.org
                existing.region = connection.region
                existing.env = connection.env
                existing.team = connection.team
                existing.email = connection.email
                existing.verified = connection.verified
                existing.last_used = connection.last_used
        return connection

    def delete_by_slug(self, slug: str) -> None:
        with self._txn() as session:
            existing = session.execute(
                select(Connection).where(connections_table.c.slug == slug)
            ).scalar_one_or_none()
            if existing is not None:
                session.delete(existing)

    def get_by_slug(self, slug: str) -> Connection | None:
        with self._txn() as session:
            return session.execute(
                select(Connection).where(connections_table.c.slug == slug)
            ).scalar_one_or_none()

    def list_all(self) -> list[Connection]:
        with self._txn() as session:
            return list(session.execute(select(Connection)).scalars().all())


def _placeholder_slug() -> str:
    return f"_pending_{uuid.uuid4().hex}"


__all__ = ["SqlConnectionRepository"]
