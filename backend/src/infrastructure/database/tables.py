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

from src.domain.models import AgentStatus


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


class AgentStatusType(TypeDecorator[AgentStatus]):
    """Round-trips ``AgentStatus`` between Python enum and TEXT column.

    The column stores the string value (``"idle"``, ``"detached"``, …) so
    the on-disk shape matches what humans see in DB browsers and the
    JSON-serialised agent.json. On read we wrap the string back into the
    enum member; equality with bare strings still works because
    ``AgentStatus`` is a ``StrEnum``.
    """

    impl = String
    cache_ok = True

    def process_bind_param(self, value: Any, dialect: Dialect) -> Any:
        if value is None:
            return None
        if isinstance(value, AgentStatus):
            return value.value
        return AgentStatus(value).value

    def process_result_value(self, value: Any, dialect: Dialect) -> Any:
        if value is None:
            return None
        return AgentStatus(value)


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
    # Optional grouping link. Slug rather than int PK so work.json stays
    # self-contained — same convention as connection refs in contexts.
    # ON DELETE SET NULL: deleting a project demotes its works to "loose".
    Column(
        "project_slug",
        String,
        ForeignKey("projects.slug", ondelete="SET NULL"),
        nullable=True,
        index=True,
    ),
    Column("from_chat_slug", String, nullable=True, index=True),
    Column("from_chat_title", String, nullable=True),
)


projects_table = Table(
    "projects",
    metadata,
    Column("id", Integer, primary_key=True, autoincrement=True),
    Column("slug", String, unique=True, nullable=False, index=True),
    Column("name", String, nullable=False),
    Column("description", String, nullable=False),
    Column("glyph", String, nullable=False),
    # OKLCH hue 0-360. Stored as int; CSS exposes as --proj-h on cards/chips.
    Column("color", Integer, nullable=False),
    Column("pinned", Boolean, nullable=False, default=False),
    # Default Jira/Sentry connections — referenced by slug so they survive
    # int-id renumbering across DB rebuilds. ON DELETE SET NULL: deleting a
    # connection clears the project default rather than dangling.
    Column(
        "default_jira_conn",
        String,
        ForeignKey("connections.slug", ondelete="SET NULL"),
        nullable=True,
    ),
    Column(
        "default_sentry_conn",
        String,
        ForeignKey("connections.slug", ondelete="SET NULL"),
        nullable=True,
    ),
    Column("created_at", UTCDateTime, nullable=False),
)


chats_table = Table(
    "chats",
    metadata,
    Column("id", Integer, primary_key=True, autoincrement=True),
    Column("slug", String, unique=True, nullable=False, index=True),
    Column("title", String, nullable=False),
    Column("provider", String, nullable=False),
    Column("model", String, nullable=False),
    Column("grounding_kind", String, nullable=True),
    Column("grounding_ref", String, nullable=True),
    Column("working_directory", String, nullable=True),
    Column("created_at", UTCDateTime, nullable=False),
    Column("updated_at", UTCDateTime, nullable=False),
    # Provider session/thread ID once a chat stream has established one.
    # Nullable for chats created before the runtime-backed stream existed.
    Column("session_id", String, nullable=True),
    Column(
        "promoted_to_work_slug",
        String,
        ForeignKey("works.slug", ondelete="SET NULL"),
        nullable=True,
        index=True,
    ),
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
    Column("status", AgentStatusType, nullable=False),
    Column("started_at", UTCDateTime, nullable=False),
    Column("stopped_at", UTCDateTime, nullable=True),
    Column("session_id", String, nullable=True),
    # Linked-list lineage for provider sessions that fork on resume
    # (notably Amp's `--execute --stream-json` mode, which spawns a new
    # thread on every continue). Set to the previous session_id when the
    # adapter emits SessionEstablished with a different ID; null on fresh
    # agents and on agents whose provider doesn't fork. Walked at re-attach
    # time to reconstruct the full visual transcript.
    Column("parent_session_id", String, nullable=True),
    # Provider-specific options the user picked at create time
    # (``permission_mode``, ``thinking_effort``, ``custom_allowed_tools``).
    # Lets resume rebuild the same ``AgentConfig`` instead of silently
    # falling back to defaults, and lets detach pass matching CLI flags
    # (``--permission-mode``, ``--effort``, ``--dangerously-allow-all``).
    # Nullable for backward compatibility with rows created before this
    # column existed — those agents continue to resume/detach with the
    # provider's default options.
    Column("options", JsonDict, nullable=True),
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
    # ETag from the last successful GitHub PR fetch. PR-only column;
    # NULL on every other artifact type and on PRs we haven't fetched
    # against yet. Persistence lets the poller send
    # ``If-None-Match: <etag>`` after a restart, so a freshly-booted
    # backend doesn't spend its rate-limit budget re-confirming
    # statuses the last process already confirmed.
    Column("pr_etag", String, nullable=True),
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


shared_folders_table = Table(
    "shared_folders",
    metadata,
    Column("id", Integer, primary_key=True, autoincrement=True),
    Column("slug", String, unique=True, nullable=False, index=True),
    Column(
        "project_id",
        Integer,
        ForeignKey("projects.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    ),
    # Display label — user-editable. Defaults to the mount path on
    # creation but isn't tied to anything on disk.
    Column("name", String, nullable=False),
    # Relative path inside agent worktrees where this share appears.
    # Validated against ``..``, leading ``/``, empty string at the
    # application layer; immutable post-creation.
    Column("mount_path", String, nullable=False),
    # NULL when the share lives at its canonical
    # <workspace>/projects/<PRJ>/shared/<share-slug>/ location;
    # populated when the user pointed Atelier at an existing folder
    # somewhere else, in which case the canonical path is a symlink to
    # this real path.
    Column("real_path", PathType, nullable=True),
    Column("created_at", UTCDateTime, nullable=False),
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


# Singleton table holding the user's presentation preferences (editor,
# terminal, layout, accent hue, theme). One row, ``id=1`` — Atelier is
# single-user, so multi-row identity isn't meaningful here. Every column
# is nullable so the row can exist with defaults applied at the read
# boundary; that lets the FE PATCH individual fields without sending
# the rest, and keeps the migration story to "stamp bump, no data".
user_settings_table = Table(
    "user_settings",
    metadata,
    Column("id", Integer, primary_key=True),
    Column("editor", String, nullable=True),
    Column("terminal", String, nullable=True),
    Column("layout", String, nullable=True),
    Column("accent_hue", Integer, nullable=True),
    Column("theme", String, nullable=True),
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
    "projects_table",
    "schema_version_table",
    "shared_folders_table",
    "transcript_cursor_table",
    "user_settings_table",
    "works_table",
]
