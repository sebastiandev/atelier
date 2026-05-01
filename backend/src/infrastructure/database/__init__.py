"""Database infrastructure: engine, session, tables, mapping, migrations.

Public surface used by the application layer:
- `create_database_engine(settings)` — build the engine at app startup
- `create_session_factory(engine)` — build the sessionmaker
- `configure_mappings()` — bind domain entities to tables (call once at startup)
- `initialize_database(engine)` — run migrations
- `session_scope(factory)` — FastAPI dependency yielding a Session
"""

from src.infrastructure.database.engine import (
    begin,
    create_database_engine,
    temp_engine_for_path,
)
from src.infrastructure.database.mapping import configure_mappings, mapper_registry
from src.infrastructure.database.migrations import (
    CURRENT_SCHEMA_VERSION,
    SchemaMismatchError,
    initialize_database,
)
from src.infrastructure.database.session import create_session_factory, session_scope
from src.infrastructure.database.tables import (
    agents_table,
    artifacts_table,
    connections_table,
    handoffs_table,
    metadata,
    schema_version_table,
    transcript_cursor_table,
    works_table,
)
from src.infrastructure.database.work_repository import SqlWorkRepository

__all__ = [
    "CURRENT_SCHEMA_VERSION",
    "SchemaMismatchError",
    "SqlWorkRepository",
    "agents_table",
    "artifacts_table",
    "begin",
    "configure_mappings",
    "connections_table",
    "create_database_engine",
    "create_session_factory",
    "handoffs_table",
    "initialize_database",
    "mapper_registry",
    "metadata",
    "schema_version_table",
    "session_scope",
    "temp_engine_for_path",
    "transcript_cursor_table",
    "works_table",
]
