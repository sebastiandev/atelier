"""SQLAlchemy implementation of `ConnectionRepository`.

Same short-transaction discipline as `SqlWorkRepository`. The slug is
derived from the DB-assigned id via the placeholder-then-rewrite pattern
(``con-{id}``).

Config translation: domain code holds a typed ``ConnectionConfig``
(``JiraConfig`` / ``SentryConfig`` / ``HoneycombConfig``); the SA column
is JSON. The repository is the boundary that flips between the two —
typed config in/out of callers, dict in/out of the session.
"""

import uuid
from collections.abc import Iterator
from contextlib import contextmanager

from sqlalchemy import select
from sqlalchemy.orm import Session, sessionmaker

from src.domain.connections.configs import config_to_dict, dict_to_config
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
        # Outer-to-inner: ``_config_as_dict`` restores the typed config
        # AFTER the session commits, so SA's commit-time flush still sees
        # the dict form on the entity. Inverting the order would restore
        # before commit, and SA would try to JSON-encode the typed
        # dataclass and crash.
        with _config_as_dict(connection), self._txn() as session:
            connection.slug = _placeholder_slug()
            session.add(connection)
            session.flush()
            assert connection.id is not None
            connection.slug = f"con-{connection.id}"
        return connection

    def upsert(self, connection: Connection) -> Connection:
        if connection.slug is None:
            raise ValueError("upsert requires slug")
        with _config_as_dict(connection), self._txn() as session:
            existing = session.execute(
                select(Connection).where(connections_table.c.slug == connection.slug)
            ).scalar_one_or_none()
            if existing is None:
                session.add(connection)
            else:
                existing.type = connection.type
                existing.name = connection.name
                existing.config = connection.config  # already a dict in this window
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
            row = session.execute(
                select(Connection).where(connections_table.c.slug == slug)
            ).scalar_one_or_none()
        # Hydrate after commit: replacing config (dict → typed) inside the
        # session window would mark the row dirty and trigger an UPDATE
        # on commit that fails to JSON-encode the typed dataclass.
        # ``expire_on_commit=False`` keeps the attributes alive post-commit.
        if row is None:
            return None
        _hydrate_config(row)
        return row

    def list_all(self) -> list[Connection]:
        with self._txn() as session:
            rows = list(session.execute(select(Connection)).scalars().all())
        for row in rows:
            _hydrate_config(row)
        return rows


@contextmanager
def _config_as_dict(connection: Connection) -> Iterator[None]:
    """Temporarily replace the typed ``config`` with its dict form for
    the duration of a flush, then restore. SA's mapped column expects a
    dict (the JsonDict TypeDecorator does dict ↔ JSON); callers expect
    a typed config. This is the pivot."""
    typed = connection.config
    connection.config = config_to_dict(typed)  # type: ignore[arg-type]
    try:
        yield
    finally:
        connection.config = typed


def _hydrate_config(connection: Connection) -> None:
    if isinstance(connection.config, dict):
        connection.config = dict_to_config(connection.type, connection.config)


def _placeholder_slug() -> str:
    return f"_pending_{uuid.uuid4().hex}"


__all__ = ["SqlConnectionRepository"]
