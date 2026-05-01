"""Database initialization, schema migrations, and SQLite pragmas."""

import pytest
from sqlalchemy import Engine, inspect, select

from src.infrastructure.database import (
    CURRENT_SCHEMA_VERSION,
    SchemaMismatchError,
    initialize_database,
    schema_version_table,
)


def test_initialize_creates_all_expected_tables(isolated_engine: Engine) -> None:
    inspector = inspect(isolated_engine)
    tables = set(inspector.get_table_names())
    expected = {
        "works",
        "agents",
        "artifacts",
        "handoffs",
        "connections",
        "transcript_cursor",
        "schema_version",
    }
    assert expected <= tables


def test_schema_version_stamp_is_current(isolated_engine: Engine) -> None:
    with isolated_engine.connect() as conn:
        version = conn.execute(select(schema_version_table.c.version)).scalar_one()
    assert version == CURRENT_SCHEMA_VERSION


def test_initialize_is_idempotent(isolated_engine: Engine) -> None:
    """Re-running initialize on an already-initialized DB is a no-op."""
    initialize_database(isolated_engine)
    initialize_database(isolated_engine)
    with isolated_engine.connect() as conn:
        rows = conn.execute(select(schema_version_table.c.version)).all()
    assert len(rows) == 1
    assert rows[0].version == CURRENT_SCHEMA_VERSION


def test_initialize_rejects_unknown_schema_version(isolated_engine: Engine) -> None:
    """If someone hand-edits the version stamp to a future value, we refuse to start."""
    with isolated_engine.begin() as conn:
        conn.execute(schema_version_table.delete())
        conn.execute(schema_version_table.insert().values(version=99))

    with pytest.raises(SchemaMismatchError, match="version 99"):
        initialize_database(isolated_engine)


def test_foreign_keys_pragma_enabled(isolated_engine: Engine) -> None:
    """SQLite default is OFF; we depend on ON for ON DELETE CASCADE."""
    with isolated_engine.connect() as conn:
        result = conn.exec_driver_sql("PRAGMA foreign_keys").scalar()
    assert result == 1


def test_journal_mode_is_wal(isolated_engine: Engine) -> None:
    """WAL gives us concurrent readers + a single writer with durable commits."""
    with isolated_engine.connect() as conn:
        result = conn.exec_driver_sql("PRAGMA journal_mode").scalar()
    assert result == "wal"


def test_synchronous_pragma_is_normal(isolated_engine: Engine) -> None:
    """NORMAL is the right balance for a single-user local app on WAL."""
    with isolated_engine.connect() as conn:
        # PRAGMA synchronous returns 1 for NORMAL
        result = conn.exec_driver_sql("PRAGMA synchronous").scalar()
    assert result == 1


def test_db_file_lives_under_workspace_root(
    isolated_engine: Engine, test_settings: object
) -> None:
    """Sanity: the engine pointed at the configured workspace, not somewhere else."""
    settings = test_settings  # cast at runtime; conftest has the real Settings
    expected = settings.workspace_root / "atelier.db"  # type: ignore[attr-defined]
    assert expected.exists()
