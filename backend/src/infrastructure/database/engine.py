"""SQLite engine factory.

Sync engine, sync sessions everywhere. The async surface (FastAPI WS, agent
supervisor) calls into the database via `asyncio.to_thread(...)` rather than
forcing async SQLAlchemy on the entire stack.
"""

from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path
from typing import Any

from sqlalchemy import Engine, create_engine, event
from sqlalchemy.engine import Connection

from src.settings import Settings


def create_database_engine(settings: Settings) -> Engine:
    """Build the SQLite engine for `~/Atelier/atelier.db`.

    Ensures the workspace directory exists. Registers a connect listener that
    sets the SQLite pragmas we depend on (WAL journaling, NORMAL synchronous,
    foreign-key enforcement on — SQLite's default is OFF).
    """
    settings.workspace_root.mkdir(parents=True, exist_ok=True)
    db_path = settings.workspace_root / "atelier.db"
    engine = create_engine(
        f"sqlite:///{db_path}",
        echo=False,
        future=True,
    )
    event.listen(engine, "connect", _set_sqlite_pragmas)
    return engine


def _set_sqlite_pragmas(dbapi_connection: Any, _connection_record: Any) -> None:
    cursor = dbapi_connection.cursor()
    try:
        cursor.execute("PRAGMA journal_mode=WAL")
        cursor.execute("PRAGMA synchronous=NORMAL")
        cursor.execute("PRAGMA foreign_keys=ON")
    finally:
        cursor.close()


@contextmanager
def begin(engine: Engine) -> Iterator[Connection]:
    """Convenience for short-lived transactional work outside a Session.

    Used by `migrations.initialize_database` and tests. Routes/commands should
    use a Session via the dependency in `session.py`, not this.
    """
    with engine.begin() as conn:
        yield conn


def temp_engine_for_path(db_path: Path) -> Engine:
    """Build an engine pointed at an explicit path. Used by tests."""
    db_path.parent.mkdir(parents=True, exist_ok=True)
    engine = create_engine(f"sqlite:///{db_path}", echo=False, future=True)
    event.listen(engine, "connect", _set_sqlite_pragmas)
    return engine
