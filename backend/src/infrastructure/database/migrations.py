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

CURRENT_SCHEMA_VERSION = 7


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
        if existing == CURRENT_SCHEMA_VERSION:
            conn.execute(
                schema_version_table.update().values(version=CURRENT_SCHEMA_VERSION)
            )
            return
        raise SchemaMismatchError(
            f"Database schema version {existing} differs from current "
            f"{CURRENT_SCHEMA_VERSION}; no forward migration registered."
        )
