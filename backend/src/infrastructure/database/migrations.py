"""Forward-only schema migrations for SQLite.

v1 strategy: `metadata.create_all` ensures every table declared in `tables.py`
exists, plus a single-row `schema_version` stamp for future migrations. No
Alembic — for a single-user local app the cost-benefit isn't there yet. When
the first real migration arrives (rename a column, add an index, transform
data) we revisit.

Idempotent: running this on an already-initialized database is a no-op.
"""

from sqlalchemy import Engine, select

from src.infrastructure.database.tables import metadata, schema_version_table

CURRENT_SCHEMA_VERSION = 1


class SchemaMismatchError(RuntimeError):
    """Raised when the on-disk schema version is unrecognised."""


def initialize_database(engine: Engine) -> None:
    """Ensure the schema and the version stamp exist.

    On first run: creates all tables, writes `schema_version=1`.
    On subsequent runs with the same version: no-op.
    On version mismatch: raises until a forward migration is implemented.
    """
    metadata.create_all(engine)
    with engine.begin() as conn:
        existing = conn.execute(select(schema_version_table.c.version)).scalar()
        if existing is None:
            conn.execute(
                schema_version_table.insert().values(version=CURRENT_SCHEMA_VERSION)
            )
            return
        if existing != CURRENT_SCHEMA_VERSION:
            raise SchemaMismatchError(
                f"Database schema version {existing} differs from current "
                f"{CURRENT_SCHEMA_VERSION}; forward migrations not yet implemented."
            )
