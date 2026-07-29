"""Forward-only schema migrations for SQLite.

`metadata.create_all` ensures every table declared in `tables.py` exists; the
`schema_version` row pins the on-disk version. For schema deltas that
``create_all`` can't apply on an existing database (adding columns, renames,
data transforms), each version step has a hand-rolled migration below.

Idempotent: running this on an already-initialized database is a no-op.
"""

import shutil
from pathlib import Path

from sqlalchemy import Engine, select, text
from sqlalchemy.engine import Connection

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

CURRENT_SCHEMA_VERSION = 25


class SchemaMismatchError(RuntimeError):
    """Raised when the on-disk schema version is unrecognised."""


def initialize_database(engine: Engine, workspace_root: Path | None = None) -> None:
    """Ensure the schema and the version stamp exist.

    On first run: creates all tables, writes ``schema_version=CURRENT``.
    On subsequent runs at the current version: no-op.
    On older versions: applies the forward migrations in order, then bumps
    the stamp.

    ``workspace_root`` lets a migration that wipes filesystem-canonical
    state (``v4 → v5``) clear ``<workspace_root>/works/`` alongside the
    SQL-side wipe. Tests pass ``None`` to skip the FS step.
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
        if existing == 3:
            # v3 → v4: SentryConfig dropped its ``region`` field (sentry.io
            # has no region prefix; verifier + fetcher target the org-scoped
            # endpoint). Existing sentry rows would TypeError on hydrate
            # (``cls(**data)`` rejects the stale ``region`` key), so wipe
            # only those — jira/honeycomb rows stay intact.
            conn.execute(text("DELETE FROM connections WHERE type = 'sentry'"))
            existing = 4
        if existing == 4:
            # v4 → v5: ``folder`` moved from Work to Agent so a single
            # work can span multiple repos. The wide impact (works,
            # agents, contexts in JSON, worktrees on disk) plus the
            # cheapness of starting fresh (pre-launch) means we wipe
            # everything work-shaped — connections + schema_version
            # are preserved. ``works.json`` files on disk also stop
            # carrying ``folder``, so the canonical FS state needs
            # the same wipe.
            for table_name in (
                "handoffs",
                "artifacts",
                "transcript_cursor",
                "agents",
                "works",
            ):
                conn.execute(text(f"DROP TABLE IF EXISTS {table_name}"))
            works_table.create(conn)
            agents_table.create(conn)
            artifacts_table.create(conn)
            handoffs_table.create(conn)
            transcript_cursor_table.create(conn)
            if workspace_root is not None:
                works_dir = workspace_root / "works"
                if works_dir.exists():
                    shutil.rmtree(works_dir)
            existing = 5
        if existing == 5:
            # v5 → v6: track provider-session fork lineage. Some providers
            # (Amp's `--execute --stream-json`) spawn a new thread on every
            # resume, leaving the old thread orphaned. parent_session_id is
            # set to the previous session_id when SessionEstablished arrives
            # with a different ID; the chain reconstructs the full visual
            # transcript at re-attach time.
            conn.execute(
                text("ALTER TABLE agents ADD COLUMN parent_session_id TEXT")
            )
            existing = 6
        if existing == 6:
            # v6 → v7: introduce Projects (optional grouping above Work)
            # and the Work→Project soft FK. Both directions of the new
            # graph are by slug — works.project_slug → projects.slug,
            # projects.default_{jira,sentry}_conn → connections.slug —
            # so on-disk JSON stays self-contained and DB rebuilds via
            # reconcile don't have to remap int ids.
            #
            # The ``projects`` table itself is created by the
            # ``metadata.create_all`` call above (it's new, so create_all
            # picks it up); only the ``works.project_slug`` column needs
            # a hand-rolled ALTER because ``works`` already exists and
            # create_all skips existing tables.
            conn.execute(
                text(
                    "ALTER TABLE works ADD COLUMN project_slug TEXT "
                    "REFERENCES projects(slug) ON DELETE SET NULL"
                )
            )
            existing = 7
        if existing == 7:
            # v7 → v8: introduce ``shared_folders`` — persistent, cross-
            # agent folders scoped to a Project. Pure-add: the new
            # ``shared_folders`` table is created by ``metadata.create_all``
            # above (per the migration pattern, never call ``.create(conn)``
            # for tables that ``create_all`` already handles). Nothing to
            # ALTER; this step is a stamp-bump only.
            existing = 8
        if existing == 8:
            # v8 → v9: persist provider-specific options on the agent row
            # (``permission_mode``, ``thinking_effort``, ``custom_allowed_tools``)
            # so resume rebuilds the same ``AgentConfig`` and detach can
            # pass matching CLI flags. Nullable column — existing rows
            # keep NULL, and the resume/detach paths fall back to provider
            # defaults for those, so no behaviour changes for agents
            # created before this version.
            conn.execute(text("ALTER TABLE agents ADD COLUMN options TEXT"))
            existing = 9
        if existing == 9:
            # v9 → v10: doc status vocabulary changed from
            # ``draft|published`` to ``draft|pending|committed``. The
            # new values are derived at list time from observed file
            # state, so the persisted ``status`` for docs now always
            # holds ``draft`` post-record. Existing ``published`` rows
            # — which used to mean "the agent considers this final" —
            # map to ``committed`` (closest semantic neighbour: the
            # doc has been published into the repo). PR / Jira status
            # values weren't reduced, so no migration there.
            conn.execute(
                text(
                    "UPDATE artifacts SET status = 'committed' "
                    "WHERE type = 'doc' AND status = 'published'"
                )
            )
            existing = 10
        if existing == 10:
            # v10 → v11: persist the GitHub response ETag from the
            # last successful PR fetch so the background poller can
            # send ``If-None-Match`` and let 304s skip the rate-limit
            # budget. Nullable column; existing rows stay NULL and
            # the fetcher just sends no header for them on first hit.
            conn.execute(
                text("ALTER TABLE artifacts ADD COLUMN pr_etag TEXT")
            )
            existing = 11
        if existing == 11:
            # v11 → v12: introduce ``user_settings`` (singleton, id=1)
            # to back the FE settings page. Pure-add: ``metadata.create_all``
            # above creates the table, so this step is a stamp bump only
            # (per the established pattern — never call ``.create(conn)``
            # for tables that ``create_all`` already handles).
            existing = 12
        if existing == 12:
            # v12 → v13: introduce exploratory chats and optional Work
            # provenance for works promoted from a chat. ``chats`` is a
            # new table created by metadata.create_all above; existing
            # ``works`` rows only need nullable additive columns.
            conn.execute(text("ALTER TABLE works ADD COLUMN from_chat_slug TEXT"))
            conn.execute(text("ALTER TABLE works ADD COLUMN from_chat_title TEXT"))
            existing = 13
        if existing == 13:
            # v13 → v14: persist the provider session/thread id for
            # runtime-backed exploratory chats. Nullable/additive so chats
            # created before the websocket runtime continue to load as
            # "no provider session yet".
            if not _has_column(conn, "chats", "session_id"):
                conn.execute(text("ALTER TABLE chats ADD COLUMN session_id TEXT"))
            existing = 14
        if existing == 14:
            # v14 → v15: separate chat placement (Project/Work link) from
            # the optional working folder used as the provider cwd.
            if not _has_column(conn, "chats", "working_directory"):
                conn.execute(
                    text("ALTER TABLE chats ADD COLUMN working_directory TEXT")
                )
            existing = 15
        if existing == 15:
            # v15 → v16: persist provider-specific chat options. Nullable and
            # optional in chat.json, so existing chats continue with provider
            # defaults.
            if not _has_column(conn, "chats", "options"):
                conn.execute(text("ALTER TABLE chats ADD COLUMN options TEXT"))
            existing = 16
        if existing == 16:
            # v16 → v17: introduce one persisted PlanningSession per Work.
            # Pure-add: metadata.create_all above creates the table.
            existing = 17
        if existing == 17:
            # v17 → v18: persist generic loop runs and stage snapshots.
            # Both tables are pure-add and are created by metadata.create_all.
            existing = 18
        if existing == 18:
            # v18 → v19: persist a Work's selected execution mode. Nullable so
            # existing Works keep the frontend's content-based fallback.
            if not _has_column(conn, "works", "mode"):
                conn.execute(text("ALTER TABLE works ADD COLUMN mode TEXT"))
            existing = 19
        if existing == 19:
            # v19 → v20: let loop stage agents point at one stable,
            # Work-owned checkout. NULL keeps legacy agents on their existing
            # per-agent worktrees and the on-disk key remains optional.
            if not _has_column(conn, "agents", "worktree_slug"):
                conn.execute(
                    text("ALTER TABLE agents ADD COLUMN worktree_slug TEXT")
                )
            existing = 20
        if existing == 20:
            # v20 → v21: persist whether an exploratory chat is a read-only
            # run discussion. NULL keeps existing chats in their normal mode;
            # chat.json also omits the additive marker unless it is true.
            if not _has_column(conn, "chats", "discussion_only"):
                conn.execute(
                    text("ALTER TABLE chats ADD COLUMN discussion_only BOOLEAN")
                )
            existing = 21
        if existing == 21:
            # v21 -> v22: store hidden seed context for idle run discussions.
            # NULL preserves every existing chat and the chat.json key is
            # omitted unless a caller explicitly supplies a seed.
            if not _has_column(conn, "chats", "context_seed"):
                conn.execute(text("ALTER TABLE chats ADD COLUMN context_seed TEXT"))
            existing = 22
        if existing == 22:
            # v22 -> v23: identify one reusable discussion per run stage. NULL
            # preserves legacy chats and lets them keep their create-only behavior.
            if not _has_column(conn, "chats", "discussion_key"):
                conn.execute(text("ALTER TABLE chats ADD COLUMN discussion_key TEXT"))
            existing = 23
        if existing == 23:
            # v23 -> v24: chats declare an owner-supplied role instead of the
            # runtime inferring one from the title string and the
            # discussion_only flag. Backfill once so existing chats keep the
            # posture they were running under; the read path has no fallback.
            if not _has_column(conn, "chats", "role"):
                conn.execute(
                    text(
                        "ALTER TABLE chats ADD COLUMN role TEXT "
                        "NOT NULL DEFAULT 'explore'"
                    )
                )
                conn.execute(
                    text(
                        "UPDATE chats SET role = 'advisory' "
                        "WHERE discussion_only = 1"
                    )
                )
                conn.execute(
                    text(
                        "UPDATE chats SET role = 'planning' "
                        "WHERE lower(trim(title)) = 'planning' "
                        "AND grounding_kind = 'work'"
                    )
                )
            existing = 24
        if existing == 24:
            # v24 -> v25: `artifact_root_path` held whichever of two things
            # the caller happened to mean -- the folder setting a user typed
            # (relative or absolute) on the session, the resolved absolute
            # path in the manifest. The column is the former, so it takes the
            # former's name. Rename rather than add-and-backfill: there is one
            # column, one meaning, and no reader left on the old spelling.
            if _has_column(conn, "planning_sessions", "artifact_root_path"):
                conn.execute(
                    text(
                        "ALTER TABLE planning_sessions "
                        "RENAME COLUMN artifact_root_path TO plan_artifacts_dir"
                    )
                )
            existing = 25
        if existing == CURRENT_SCHEMA_VERSION:
            conn.execute(
                schema_version_table.update().values(version=CURRENT_SCHEMA_VERSION)
            )
            return
        raise SchemaMismatchError(
            f"Database schema version {existing} differs from current "
            f"{CURRENT_SCHEMA_VERSION}; no forward migration registered."
        )


def _has_column(conn: Connection, table_name: str, column_name: str) -> bool:
    rows = conn.execute(text(f"PRAGMA table_info({table_name})")).mappings()
    return any(row.get("name") == column_name for row in rows)
