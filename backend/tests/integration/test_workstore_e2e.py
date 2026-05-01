"""End-to-end tests for the WorkStore boundary.

Wires real `SqlWorkRepository` + `FsWorkspaceFiles` + `FsTranscriptLog`
through `WorkStoreService` and exercises the STORY-005 acceptance
criterion: deleting a SQLite row and restarting (re-running reconcile)
restores it from the canonical filesystem.
"""

from __future__ import annotations

from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import Engine, text
from sqlalchemy.orm import Session, sessionmaker

from src.domain.models import Context
from src.domain.workstore import (
    AddAgentRequest,
    CreateWorkRequest,
    WorkStoreService,
    reconcile,
)
from src.infrastructure.database import SqlWorkRepository
from src.infrastructure.filesystem import (
    FsTranscriptLog,
    FsWorkspaceFiles,
    WorkspacePaths,
)
from src.main import create_app
from src.settings import Settings


def _build_service(
    settings: Settings, session_factory: sessionmaker[Session]
) -> tuple[WorkStoreService, SqlWorkRepository, FsWorkspaceFiles]:
    paths = WorkspacePaths(workspace_root=settings.workspace_root)
    repo = SqlWorkRepository(session_factory)
    files = FsWorkspaceFiles(paths)
    log = FsTranscriptLog(paths)
    return WorkStoreService(repo, files, log), repo, files


# ---------------------------------------------------------------------------
# Round-trip
# ---------------------------------------------------------------------------


def test_create_work_writes_db_and_filesystem(
    test_settings: Settings, session_factory: sessionmaker[Session], tmp_path: Path
) -> None:
    service, repo, files = _build_service(test_settings, session_factory)

    record = service.create_work(
        CreateWorkRequest(
            name="Migration",
            description="brief\nbody",
            folder=Path("/code/foo"),
            contexts=[Context(type="text", value="see deck")],
        )
    )

    assert record.work.slug == "WRK-001"
    assert repo.get_work_by_slug("WRK-001") is not None
    work_json = files.read_work_json("WRK-001")
    assert work_json is not None
    assert work_json["name"] == "Migration"

    brief_path = test_settings.workspace_root / "works" / "WRK-001" / "brief.md"
    assert brief_path.read_text() == "brief\nbody"


def test_get_work_returns_combined_record(
    test_settings: Settings, session_factory: sessionmaker[Session]
) -> None:
    service, _, _ = _build_service(test_settings, session_factory)
    contexts = [Context(type="jira", value="FOO-1", conn_id="con-1")]
    service.create_work(
        CreateWorkRequest(
            name="X",
            description="d",
            folder=Path("/code/x"),
            contexts=contexts,
        )
    )

    got = service.get_work("WRK-001")
    assert got is not None
    assert got.work.name == "X"
    assert got.contexts == contexts


def test_add_agent_creates_dir_and_db_row(
    test_settings: Settings, session_factory: sessionmaker[Session]
) -> None:
    service, repo, _ = _build_service(test_settings, session_factory)
    service.create_work(CreateWorkRequest(name="X", description="d", folder=Path("/x")))
    agent = service.add_agent_to_work(
        AddAgentRequest(
            work_slug="WRK-001",
            name="Architect",
            persona="architect",
            role="architect",
            provider="claude-code",
            model="claude-opus-4-7",
        )
    )
    assert agent.slug == "agt-1"
    assert repo.get_agent_by_slug("agt-1") is not None
    agent_dir = test_settings.workspace_root / "works" / "WRK-001" / "agents" / "agt-1"
    assert (agent_dir / "agent.json").exists()


# ---------------------------------------------------------------------------
# Acceptance criterion
# ---------------------------------------------------------------------------


def test_ac_deleting_db_row_then_reconcile_restores_from_filesystem(
    test_settings: Settings,
    isolated_engine: Engine,
    session_factory: sessionmaker[Session],
) -> None:
    """STORY-005 AC: filesystem is canonical. A deleted SQL row is restored
    from `work.json` on next startup."""
    service, repo, files = _build_service(test_settings, session_factory)
    service.create_work(
        CreateWorkRequest(
            name="Migration",
            description="b",
            folder=Path("/code/foo"),
            contexts=[Context(type="text", value="ctx")],
        )
    )
    assert repo.get_work_by_slug("WRK-001") is not None

    # Simulate "DB lost the row" by issuing raw SQL DELETE.
    with isolated_engine.begin() as conn:
        conn.execute(text("DELETE FROM works WHERE slug = 'WRK-001'"))
    assert repo.get_work_by_slug("WRK-001") is None

    report = reconcile(repo, files)

    assert report.inserted_works == ["WRK-001"]
    restored = repo.get_work_by_slug("WRK-001")
    assert restored is not None
    assert restored.id == 1
    assert restored.name == "Migration"
    assert restored.folder == Path("/code/foo")


def test_ac_filesystem_wins_on_conflict(
    test_settings: Settings,
    session_factory: sessionmaker[Session],
) -> None:
    """STORY-005 AC: when DB and FS disagree, reconcile updates DB to match FS."""
    service, repo, files = _build_service(test_settings, session_factory)
    service.create_work(
        CreateWorkRequest(name="Original", description="b", folder=Path("/code/foo"))
    )

    # Edit the FS-side work.json to a new name.
    fs_data = files.read_work_json("WRK-001")
    assert fs_data is not None
    fs_data["name"] = "RenamedOnDisk"
    files.write_work_json("WRK-001", fs_data)

    report = reconcile(repo, files)

    assert report.updated_works == ["WRK-001"]
    refreshed = repo.get_work_by_slug("WRK-001")
    assert refreshed is not None
    assert refreshed.name == "RenamedOnDisk"


def test_reconcile_deletes_orphan_db_work(
    test_settings: Settings,
    session_factory: sessionmaker[Session],
) -> None:
    """A Work in DB without a corresponding work.json on disk is deleted."""
    service, repo, files = _build_service(test_settings, session_factory)
    service.create_work(CreateWorkRequest(name="X", description="d", folder=Path("/x")))

    # Remove the FS dir entirely (simulating manual delete or restore from
    # an older backup). Then reconcile.
    work_dir = test_settings.workspace_root / "works" / "WRK-001"
    import shutil

    shutil.rmtree(work_dir)
    assert files.list_work_slugs() == []

    report = reconcile(repo, files)

    assert report.deleted_works == ["WRK-001"]
    assert repo.get_work_by_slug("WRK-001") is None


def test_reconcile_runs_at_app_startup(
    test_settings: Settings,
    isolated_engine: Engine,
    session_factory: sessionmaker[Session],
) -> None:
    """Spinning up a fresh app from a workspace with FS-only data populates DB."""
    # Seed the workspace via a service, then wipe DB rows to simulate a
    # restart against an existing FS but a freshly-zeroed DB.
    service, _, _ = _build_service(test_settings, session_factory)
    service.create_work(
        CreateWorkRequest(
            name="Persisted",
            description="x",
            folder=Path("/code/p"),
            contexts=[Context(type="url", value="https://x.test")],
        )
    )

    with isolated_engine.begin() as conn:
        conn.execute(text("DELETE FROM works"))

    # Boot the FastAPI app, which runs reconcile in lifespan.
    app = create_app(test_settings)
    with TestClient(app) as _client:
        # The app's reconcile reads work.json and re-inserts.
        # Use a fresh repo against the same DB to verify.
        fresh_repo = SqlWorkRepository(session_factory)
        restored = fresh_repo.get_work_by_slug("WRK-001")
        assert restored is not None
        assert restored.name == "Persisted"


@pytest.mark.parametrize("count", [1, 3, 5])
def test_creating_many_works_assigns_distinct_slugs(
    test_settings: Settings, session_factory: sessionmaker[Session], count: int
) -> None:
    service, _, _ = _build_service(test_settings, session_factory)
    slugs = [
        service.create_work(
            CreateWorkRequest(name=f"W{i}", description="d", folder=Path("/x"))
        ).work.slug
        for i in range(count)
    ]
    assert len(slugs) == len(set(slugs))
    assert all(s and s.startswith("WRK-") for s in slugs)
