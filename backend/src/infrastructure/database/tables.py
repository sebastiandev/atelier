"""SQLAlchemy table definitions.

Tables are declared imperatively against a shared `MetaData()`. The mapping
between these tables and the domain entities lives in `mapping.py` so this
module stays purely about schema.

Identity model: integer PKs (rowid-aliased on SQLite) for tight indexes and
fast joins. Each public-facing table additionally carries a `slug TEXT UNIQUE
NOT NULL` column that holds the user-visible identifier ("WRK-001", "agt-7",
"con-3", ...). URLs, folder names, and JSON cross-references use slugs;
SQL FKs use the int PK.

Schema is intentionally narrow — the architecture's "filesystem is canonical,
SQLite is index" rule means we only persist what we want to query cheaply.
Conventional paths (work brief, agent worktree, keyring ref) are derived from
slugs at access time, not stored as columns.
"""

from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from sqlalchemy import (
    Boolean,
    Column,
    DateTime,
    Dialect,
    ForeignKey,
    Integer,
    MetaData,
    String,
    Table,
)
from sqlalchemy.types import TypeDecorator


class PathType(TypeDecorator[Path]):
    """Stores `pathlib.Path` as a String, round-trips correctly on read.

    Without this, `Path` columns serialise to text on write (via str())
    but come back as `str` on read, breaking the entity's type annotation.
    """

    impl = String
    cache_ok = True

    def process_bind_param(self, value: Any, dialect: Dialect) -> Any:
        if value is None:
            return None
        return str(value)

    def process_result_value(self, value: Any, dialect: Dialect) -> Any:
        if value is None:
            return None
        return Path(value)


class UTCDateTime(TypeDecorator[datetime]):
    """Stores tz-aware UTC datetimes, returns tz-aware UTC on read.

    SQLite stores datetimes as text and SA's default DateTime deserializer
    drops the timezone, returning naive datetimes. This decorator enforces
    that callers pass tz-aware values and reattaches UTC on the read side.
    """

    impl = DateTime(timezone=True)
    cache_ok = True

    def process_bind_param(self, value: Any, dialect: Dialect) -> Any:
        if value is None:
            return None
        if value.tzinfo is None:
            raise ValueError(
                "naive datetime rejected; pass a tz-aware value (UTC preferred)"
            )
        return value.astimezone(UTC)

    def process_result_value(self, value: Any, dialect: Dialect) -> Any:
        if value is None:
            return None
        if value.tzinfo is None:
            return value.replace(tzinfo=UTC)
        return value


class JsonDict(TypeDecorator[dict[str, Any]]):
    """Stores a Python dict as JSON text. Round-trips through ``json``,
    so leaf values must be JSON-serialisable (str/int/float/bool/None/list/dict).

    Used for ``connections.config`` — the per-type config travels as a
    typed dataclass in the domain layer, gets converted to a dict at the
    repository boundary, and is stored as JSON here. Keeping the SA
    column type narrow (vs. SA's vendor-specific JSON column) means we
    don't depend on SQLite's json1 extension being available.
    """

    impl = String
    cache_ok = True

    def process_bind_param(self, value: Any, dialect: Dialect) -> Any:
        if value is None:
            return None
        import json

        return json.dumps(value, separators=(",", ":"))

    def process_result_value(self, value: Any, dialect: Dialect) -> Any:
        if value is None:
            return None
        import json

        return json.loads(value)


metadata = MetaData()


works_table = Table(
    "works",
    metadata,
    Column("id", Integer, primary_key=True, autoincrement=True),
    Column("slug", String, unique=True, nullable=False, index=True),
    Column("name", String, nullable=False),
    Column("description", String, nullable=False),
    Column("status", String, nullable=False),
    Column("created_at", UTCDateTime, nullable=False),
)


agents_table = Table(
    "agents",
    metadata,
    Column("id", Integer, primary_key=True, autoincrement=True),
    Column("slug", String, unique=True, nullable=False, index=True),
    Column(
        "work_id",
        Integer,
        ForeignKey("works.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    ),
    Column("name", String, nullable=False),
    Column("persona", String, nullable=False),
    Column("role", String, nullable=False),
    Column("provider", String, nullable=False),
    Column("model", String, nullable=False),
    # Working directory the agent's adapter spawns in. Per-agent (not
    # per-work) so a single Work can span multiple repos — e.g. a FE
    # agent in one checkout + a BE agent in another collaborating on
    # one cross-cutting goal. WorktreeManager.ensure(source=this) is
    # what turns it into a per-agent git worktree when it's a repo.
    Column("folder", PathType, nullable=False),
    Column("status", String, nullable=False),
    Column("started_at", UTCDateTime, nullable=False),
    Column("stopped_at", UTCDateTime, nullable=True),
    Column("session_id", String, nullable=True),
)


artifacts_table = Table(
    "artifacts",
    metadata,
    Column("id", Integer, primary_key=True, autoincrement=True),
    Column("slug", String, unique=True, nullable=False, index=True),
    Column(
        "work_id",
        Integer,
        ForeignKey("works.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    ),
    Column(
        "agent_id",
        Integer,
        ForeignKey("agents.id", ondelete="SET NULL"),
        nullable=True,
    ),
    Column("type", String, nullable=False),
    Column("title", String, nullable=False),
    Column("status", String, nullable=False),
    Column("created_at", UTCDateTime, nullable=False),
    Column("repo", String, nullable=True),
    Column("url", String, nullable=True),
    Column("doc_path", String, nullable=True),
)


handoffs_table = Table(
    "handoffs",
    metadata,
    Column("id", Integer, primary_key=True, autoincrement=True),
    Column("slug", String, unique=True, nullable=False, index=True),
    Column(
        "work_id",
        Integer,
        ForeignKey("works.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    ),
    Column(
        "source_agent_id",
        Integer,
        ForeignKey("agents.id", ondelete="CASCADE"),
        nullable=False,
    ),
    Column("doc_path", PathType, nullable=False),
    Column("created_at", UTCDateTime, nullable=False),
    Column(
        "target_agent_id",
        Integer,
        ForeignKey("agents.id", ondelete="SET NULL"),
        nullable=True,
    ),
    Column("target_dialog", String, nullable=True),
)


connections_table = Table(
    "connections",
    metadata,
    Column("id", Integer, primary_key=True, autoincrement=True),
    Column("slug", String, unique=True, nullable=False, index=True),
    Column("type", String, nullable=False),
    Column("name", String, nullable=False),
    Column("created_at", UTCDateTime, nullable=False),
    # Per-type config (e.g. Jira's url+email). Shape is enforced by the
    # typed dataclass in domain/connections/configs.py; the repository
    # serialises it to JSON here so adding a new connection type doesn't
    # require a schema migration.
    Column("config", JsonDict, nullable=False),
    Column("verified", Boolean, nullable=False, default=False),
    Column("last_used", UTCDateTime, nullable=True),
)


# Tracks per-agent transcript replay cursor. Updated periodically by the
# supervisor (not on every event) — events themselves are NDJSON on disk.
# Internal-only — keyed by int agent_id, no slug.
transcript_cursor_table = Table(
    "transcript_cursor",
    metadata,
    Column(
        "agent_id",
        Integer,
        ForeignKey("agents.id", ondelete="CASCADE"),
        primary_key=True,
    ),
    Column("last_seq", Integer, nullable=False),
    Column("last_seen_at", UTCDateTime, nullable=False),
)


# Single-row table used as a database-level schema-version stamp. Migrations
# read this on startup; mismatch with the application's CURRENT_SCHEMA_VERSION
# triggers a forward migration (or a hard error until one is written).
schema_version_table = Table(
    "schema_version",
    metadata,
    Column("version", Integer, primary_key=True),
)


__all__ = [
    "JsonDict",
    "PathType",
    "UTCDateTime",
    "agents_table",
    "artifacts_table",
    "connections_table",
    "handoffs_table",
    "metadata",
    "schema_version_table",
    "transcript_cursor_table",
    "works_table",
]
