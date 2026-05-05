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
    repo = StubRepository()
    _seed_db_work(repo)
    _seed_db_agent(repo, status="live")
    files = StubFiles()
    files.work_jsons["WRK-001"] = _work_json()
    files.agent_jsons[("WRK-001", "agt-1")] = _agent_json(status="idle")

    report = reconcile(repo, files)

    assert report.updated_agents == ["agt-1"]
    assert repo.agents["agt-1"].status == "idle"


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
