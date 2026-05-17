"""Unit tests for `reconcile` against in-memory port stubs.

Each scenario seeds the repo and files independently so we can assert
exactly what reconcile does for each (FS-only / DB-only / both-differ /
both-match) shape.
"""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from src.domain.models import Agent, Work
from src.domain.workstore import reconcile
from tests.unit.domain.workstore._stubs import StubFiles, StubRepository

UTC_NOW = datetime(2026, 5, 1, 13, 49, tzinfo=UTC)


def _work_json(
    slug: str = "WRK-001",
    *,
    work_id: int = 1,
    name: str = "Migration",
    description: str = "brief",
    status: str = "active",
    created_at: datetime = UTC_NOW,
    contexts: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    return {
        "id": work_id,
        "slug": slug,
        "name": name,
        "description": description,
        "status": status,
        "created_at": created_at.isoformat(),
        "contexts": contexts or [],
    }


def _agent_json(
    slug: str = "agt-1",
    *,
    agent_id: int = 1,
    work_id: int = 1,
    name: str = "Architect",
    persona: str = "architect",
    role: str = "architect",
    provider: str = "claude-code",
    model: str = "claude-opus-4-7",
    folder: str = "/code/foo",
    status: str = "idle",
    started_at: datetime = UTC_NOW,
    stopped_at: datetime | None = None,
) -> dict[str, Any]:
    return {
        "id": agent_id,
        "slug": slug,
        "work_id": work_id,
        "name": name,
        "persona": persona,
        "role": role,
        "provider": provider,
        "model": model,
        "folder": folder,
        "status": status,
        "started_at": started_at.isoformat(),
        "stopped_at": stopped_at.isoformat() if stopped_at else None,
    }


def _seed_db_work(repo: StubRepository, **overrides: Any) -> Work:
    work = Work(
        id=overrides.get("id", 1),
        slug=overrides.get("slug", "WRK-001"),
        name=overrides.get("name", "Migration"),
        description=overrides.get("description", "brief"),
        status=overrides.get("status", "active"),
        created_at=overrides.get("created_at", UTC_NOW),
    )
    assert work.slug is not None
    repo.works[work.slug] = work
    repo._next_work_id = max(repo._next_work_id, (work.id or 0) + 1)
    return work


def _seed_db_agent(repo: StubRepository, **overrides: Any) -> Agent:
    agent = Agent(
        id=overrides.get("id", 1),
        slug=overrides.get("slug", "agt-1"),
        work_id=overrides.get("work_id", 1),
        name=overrides.get("name", "Architect"),
        persona=overrides.get("persona", "architect"),
        role=overrides.get("role", "architect"),
        provider=overrides.get("provider", "claude-code"),
        model=overrides.get("model", "claude-opus-4-7"),
        folder=Path(overrides.get("folder", "/code/foo")),
        status=overrides.get("status", "idle"),
        started_at=overrides.get("started_at", UTC_NOW),
        stopped_at=overrides.get("stopped_at"),
        session_id=overrides.get("session_id"),
        parent_session_id=overrides.get("parent_session_id"),
    )
    assert agent.slug is not None
    repo.agents[agent.slug] = agent
    repo._next_agent_id = max(repo._next_agent_id, (agent.id or 0) + 1)
    return agent


# ---------------------------------------------------------------------------
# Work-level reconciliation
# ---------------------------------------------------------------------------


def test_reconcile_inserts_work_missing_from_db() -> None:
    repo = StubRepository()
    files = StubFiles()
    files.work_jsons["WRK-001"] = _work_json()

    report = reconcile(repo, files)

    assert report.inserted_works == ["WRK-001"]
    assert "WRK-001" in repo.works
    assert repo.works["WRK-001"].name == "Migration"


def test_reconcile_updates_work_when_db_differs_from_fs() -> None:
    repo = StubRepository()
    _seed_db_work(repo, name="OldName")
    files = StubFiles()
    files.work_jsons["WRK-001"] = _work_json(name="NewName")

    report = reconcile(repo, files)

    assert report.updated_works == ["WRK-001"]
    assert report.inserted_works == []
    assert repo.works["WRK-001"].name == "NewName"


def test_reconcile_no_op_when_synchronized() -> None:
    repo = StubRepository()
    _seed_db_work(repo)
    files = StubFiles()
    files.work_jsons["WRK-001"] = _work_json()

    report = reconcile(repo, files)

    assert report.inserted_works == []
    assert report.updated_works == []
    assert report.deleted_works == []


def test_reconcile_deletes_orphan_db_work() -> None:
    repo = StubRepository()
    _seed_db_work(repo)
    files = StubFiles()  # no FS dirs

    report = reconcile(repo, files)

    assert report.deleted_works == ["WRK-001"]
    assert "WRK-001" not in repo.works


def test_reconcile_handles_empty_workspace() -> None:
    repo = StubRepository()
    files = StubFiles()
    report = reconcile(repo, files)
    assert report == type(report)()  # all empty lists


# ---------------------------------------------------------------------------
# Agent-level reconciliation
# ---------------------------------------------------------------------------


def test_reconcile_inserts_missing_agent_under_existing_work() -> None:
    repo = StubRepository()
    _seed_db_work(repo)
    files = StubFiles()
    files.work_jsons["WRK-001"] = _work_json()
    files.agent_jsons[("WRK-001", "agt-1")] = _agent_json()

    report = reconcile(repo, files)

    assert report.inserted_agents == ["agt-1"]
    assert "agt-1" in repo.agents
    assert repo.agents["agt-1"].persona == "architect"


def test_reconcile_updates_agent_on_conflict() -> None:
    """When a definition field (here: role) differs between FS and
    DB, reconcile upserts the FS-canonical row. Runtime fields
    (status / session_id) are preserved separately — see the merge
    tests further down."""
    repo = StubRepository()
    _seed_db_work(repo)
    _seed_db_agent(repo, role="architect")
    files = StubFiles()
    files.work_jsons["WRK-001"] = _work_json()
    files.agent_jsons[("WRK-001", "agt-1")] = _agent_json(role="reviewer")

    report = reconcile(repo, files)

    assert report.updated_agents == ["agt-1"]
    assert repo.agents["agt-1"].role == "reviewer"


def test_reconcile_deletes_orphan_agent() -> None:
    repo = StubRepository()
    _seed_db_work(repo)
    _seed_db_agent(repo)
    files = StubFiles()
    files.work_jsons["WRK-001"] = _work_json()
    # No agent.json for agt-1 → orphan

    report = reconcile(repo, files)

    assert report.deleted_agents == ["agt-1"]
    assert "agt-1" not in repo.agents


# ---------------------------------------------------------------------------
# Robustness
# ---------------------------------------------------------------------------


def test_reconcile_skips_unreadable_work_json() -> None:
    repo = StubRepository()
    files = StubFiles()
    # Simulate "dir exists but work.json is missing/corrupt" by putting the
    # slug in the dir list without a corresponding json blob.
    files.work_jsons.pop("WRK-001", None)

    # Force list_work_slugs to mention WRK-001 anyway:
    class _Files(StubFiles):
        def list_work_slugs(self) -> list[str]:
            return ["WRK-001"]

    files = _Files()
    report = reconcile(repo, files)

    assert report.skipped_unreadable == ["work:WRK-001"]
    assert report.inserted_works == []


def test_reconcile_skips_unreadable_agent_json() -> None:
    repo = StubRepository()
    _seed_db_work(repo)
    files = StubFiles()
    files.work_jsons["WRK-001"] = _work_json()

    class _Files(StubFiles):
        def __init__(self) -> None:
            super().__init__()
            self.work_jsons["WRK-001"] = _work_json()

        def list_agent_slugs(self, work_slug: str) -> list[str]:
            return ["agt-1"] if work_slug == "WRK-001" else []

    files = _Files()
    report = reconcile(repo, files)

    assert "agent:WRK-001/agt-1" in report.skipped_unreadable


def test_reconcile_is_idempotent() -> None:
    repo = StubRepository()
    files = StubFiles()
    files.work_jsons["WRK-001"] = _work_json()
    files.agent_jsons[("WRK-001", "agt-1")] = _agent_json()

    first = reconcile(repo, files)
    second = reconcile(repo, files)

    assert first.inserted_works == ["WRK-001"]
    assert first.inserted_agents == ["agt-1"]
    assert second.inserted_works == []
    assert second.updated_works == []
    assert second.inserted_agents == []
    assert second.updated_agents == []


def test_reconcile_combined_scenario_returns_full_report() -> None:
    """One scenario covering insert, update, delete simultaneously."""
    repo = StubRepository()
    # WRK-001: FS up to date, no change
    _seed_db_work(repo, slug="WRK-001", id=1)
    # WRK-002: DB-only orphan, should be deleted
    _seed_db_work(repo, slug="WRK-002", id=2, name="Stale")
    # WRK-003: DB lags FS, should be updated
    _seed_db_work(repo, slug="WRK-003", id=3, name="OldDesc")
    # WRK-004 will be inserted from FS

    files = StubFiles()
    files.work_jsons["WRK-001"] = _work_json(slug="WRK-001", work_id=1)
    files.work_jsons["WRK-003"] = _work_json(slug="WRK-003", work_id=3, name="NewDesc")
    files.work_jsons["WRK-004"] = _work_json(slug="WRK-004", work_id=4, name="Brand new")

    report = reconcile(repo, files)

    assert report.inserted_works == ["WRK-004"]
    assert report.updated_works == ["WRK-003"]
    assert report.deleted_works == ["WRK-002"]


def test_reconcile_preserves_db_session_id_when_fs_is_null() -> None:
    """``set_agent_session_id`` writes straight to SQL on
    SessionEstablished and intentionally never touches agent.json
    (the file is authoritative for definition fields only). Before
    this fix, reconcile compared the entire row and re-upserted
    agent.json's stale ``session_id: null`` over the DB value on
    every backend restart — leaving the next detach with no resume
    handle. The merge must keep the DB-canonical runtime fields."""
    repo = StubRepository()
    files = StubFiles()
    _seed_db_work(repo)
    _seed_db_agent(
        repo,
        session_id="bf242c12-b1e0-4964-a2d4-5d17bbea9b79",
        parent_session_id="prev-sid",
        status="idle",
    )
    files.work_jsons["WRK-001"] = _work_json()
    # agent.json has the runtime fields as null — that's the on-
    # disk reality because nothing writes them through the FS path.
    fs_agent = _agent_json()
    fs_agent["session_id"] = None
    fs_agent["parent_session_id"] = None
    files.agent_jsons[("WRK-001", "agt-1")] = fs_agent

    report = reconcile(repo, files)

    assert report.updated_agents == [], (
        "FS agent matches the FS-canonical fields of the DB row "
        "(after preserving DB-canonical runtime fields), so reconcile "
        "must NOT upsert."
    )
    db_after = repo.agents["agt-1"]
    assert db_after.session_id == "bf242c12-b1e0-4964-a2d4-5d17bbea9b79"
    assert db_after.parent_session_id == "prev-sid"


def test_reconcile_updates_definition_fields_but_keeps_db_session() -> None:
    """When a definition field (name, role, model, …) actually
    differs between FS and DB, reconcile upserts — but still
    preserves the DB-canonical runtime fields on the merged row."""
    repo = StubRepository()
    files = StubFiles()
    _seed_db_work(repo)
    _seed_db_agent(
        repo,
        name="Architect",
        session_id="bf242c12",
        status="idle",
    )
    files.work_jsons["WRK-001"] = _work_json()
    fs_agent = _agent_json(name="Renamed Architect")  # definition drift
    fs_agent["session_id"] = None
    files.agent_jsons[("WRK-001", "agt-1")] = fs_agent

    report = reconcile(repo, files)

    assert report.updated_agents == ["agt-1"]
    db_after = repo.agents["agt-1"]
    assert db_after.name == "Renamed Architect"
    assert db_after.session_id == "bf242c12"  # NOT wiped
