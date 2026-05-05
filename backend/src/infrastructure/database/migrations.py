"""Forward-only schema migrations for SQLite.

`metadata.create_all` ensures every table declared in `tables.py` exists; the
`schema_version` row pins the on-disk version. For schema deltas that
``create_all`` can't apply on an existing database (adding columns, renames,
data transforms), each version step has a hand-rolled migration below.

Idempotent: running this on an already-initialized database is a no-op.
"""

from sqlalchemy import Engine, select, text

from src.infrastructure.database.tables import (
    connections_table,
    metadata,
    schema_version_table,
)

CURRENT_SCHEMA_VERSION = 3


class SchemaMismatchError(RuntimeError):
    """Raised when the on-disk schema version is unrecognised."""


def initialize_database(engine: Engine) -> None:
    """Ensure the schema and the version stamp exist.

    On first run: creates all tables, writes ``schema_version=CURRENT``.
    On subsequent runs at the current version: no-op.
    On older versions: applies the forward migrations in order, then bumps
    the stamp.
    """
    metadata.create_all(engine)
    with engine.begin() as conn:
        existing = conn.execute(select(schema_version_table.c.version)).scalar()
        if existing is None:
            conn.execute(
                schema_version_table.insert().values(version=CURRENT_SCHEMA_VERSION)
            )
            return
        if existing == CURRENT_SCHEMA_VERSION:
            return
        if existing == 1:
            # v1 → v2: agents.session_id (provider thread/session handle).
            conn.execute(text("ALTER TABLE agents ADD COLUMN session_id TEXT"))
            existing = 2
        if existing == 2:
            # v2 → v3: connections table reshaped — wide nullable columns
            # (url, org, region, env, team, email) collapsed into a single
            # JSON ``config`` column whose shape is owned by per-type
            # dataclasses. No data migration: existing rows are wiped (the
            # user accepted this trade-off for the simpler shape).
            conn.execute(text("DROP TABLE IF EXISTS connections"))
            connections_table.create(conn)
            existing = 3
        if existing == CURRENT_SCHEMA_VERSION:
            conn.execute(
                schema_version_table.update().values(version=CURRENT_SCHEMA_VERSION)
            )
            return
        raise SchemaMismatchError(
            f"Database schema version {existing} differs from current "
            f"{CURRENT_SCHEMA_VERSION}; no forward migration registered."
        )
