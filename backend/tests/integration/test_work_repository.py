"""Integration tests for SqlWorkRepository against a real SQLite engine."""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path

import pytest
from sqlalchemy.orm import Session, sessionmaker

from src.domain.models import Agent, Artifact, Handoff, Work
from src.infrastructure.database import SqlWorkRepository

UTC_NOW = datetime(2026, 5, 1, 13, 49, tzinfo=UTC)


@pytest.fixture
def repo(session_factory: sessionmaker[Session]) -> SqlWorkRepository:
    return SqlWorkRepository(session_factory)


def _new_work(name: str = "Migration") -> Work:
    return Work(
        name=name,
        description=f"brief for {name}",
        folder=Path("/code/foo"),
        status="active",
        created_at=UTC_NOW,
    )


def _new_agent(work_id: int, name: str = "Architect") -> Agent:
    return Agent(
        work_id=work_id,
        name=name,
        persona="architect",
        role="architect",
        provider="claude-code",
        model="claude-opus-4-7",
        status="idle",
        started_at=UTC_NOW,
    )


# ---------------------------------------------------------------------------
# Work
# ---------------------------------------------------------------------------


def test_add_work_assigns_id_and_slug(repo: SqlWorkRepository) -> None:
    work = repo.add_work(_new_work())
    assert work.id == 1
    assert work.slug == "WRK-001"


def test_add_work_increments(repo: SqlWorkRepository) -> None:
    a = repo.add_work(_new_work(name="A"))
    b = repo.add_work(_new_work(name="B"))
    assert a.slug == "WRK-001"
    assert b.slug == "WRK-002"


def test_get_work_by_slug_returns_persisted_work(repo: SqlWorkRepository) -> None:
    repo.add_work(_new_work(name="Plan"))
    fetched = repo.get_work_by_slug("WRK-001")
    assert fetched is not None
    assert fetched.name == "Plan"


def test_get_work_by_slug_returns_none_when_missing(repo: SqlWorkRepository) -> None:
    assert repo.get_work_by_slug("WRK-404") is None


def test_list_works_returns_all(repo: SqlWorkRepository) -> None:
    repo.add_work(_new_work(name="A"))
    repo.add_work(_new_work(name="B"))
    works = repo.list_works()
    assert {w.slug for w in works} == {"WRK-001", "WRK-002"}


def test_upsert_work_inserts_when_absent(repo: SqlWorkRepository) -> None:
    work = Work(
        id=42,
        slug="WRK-042",
        name="From FS",
        description="recovered",
        folder=Path("/code/recovered"),
        status="active",
        created_at=UTC_NOW,
    )
    repo.upsert_work(work)
    fetched = repo.get_work_by_slug("WRK-042")
    assert fetched is not None
    assert fetched.id == 42
    assert fetched.name == "From FS"


def test_upsert_work_updates_when_present(repo: SqlWorkRepository) -> None:
    repo.add_work(_new_work(name="Old"))
    fresh = Work(
        id=1,
        slug="WRK-001",
        name="New",
        description="brief",
        folder=Path("/code/foo"),
        status="completed",
        created_at=UTC_NOW,
    )
    repo.upsert_work(fresh)
    fetched = repo.get_work_by_slug("WRK-001")
    assert fetched is not None
    assert fetched.name == "New"
    assert fetched.status == "completed"


def test_delete_work_removes_row(repo: SqlWorkRepository) -> None:
    repo.add_work(_new_work())
    repo.delete_work("WRK-001")
    assert repo.get_work_by_slug("WRK-001") is None


def test_delete_work_cascades_to_agents(repo: SqlWorkRepository) -> None:
    work = repo.add_work(_new_work())
    assert work.id is not None
    agent = repo.add_agent(_new_agent(work_id=work.id))
    assert agent.slug == "agt-1"

    repo.delete_work("WRK-001")
    assert repo.get_agent_by_slug("agt-1") is None


def test_delete_work_is_idempotent(repo: SqlWorkRepository) -> None:
    repo.delete_work("WRK-nope")  # does not raise


# ---------------------------------------------------------------------------
# Agent
# ---------------------------------------------------------------------------


def test_add_agent_assigns_id_and_slug(repo: SqlWorkRepository) -> None:
    work = repo.add_work(_new_work())
    assert work.id is not None
    agent = repo.add_agent(_new_agent(work_id=work.id))
    assert agent.id == 1
    assert agent.slug == "agt-1"
    assert agent.work_id == work.id


def test_list_agents_for_work_filters_by_parent(repo: SqlWorkRepository) -> None:
    w1 = repo.add_work(_new_work(name="W1"))
    w2 = repo.add_work(_new_work(name="W2"))
    assert w1.id is not None and w2.id is not None
    repo.add_agent(_new_agent(work_id=w1.id, name="A"))
    repo.add_agent(_new_agent(work_id=w2.id, name="B"))
    repo.add_agent(_new_agent(work_id=w1.id, name="C"))

    a1 = repo.list_agents_for_work("WRK-001")
    a2 = repo.list_agents_for_work("WRK-002")

    assert {a.name for a in a1} == {"A", "C"}
    assert {a.name for a in a2} == {"B"}


def test_upsert_agent_inserts_with_explicit_id(repo: SqlWorkRepository) -> None:
    work = repo.add_work(_new_work())
    assert work.id is not None
    agent = Agent(
        id=99,
        slug="agt-99",
        work_id=work.id,
        name="Recovered",
        persona="developer",
        role="developer",
        provider="amp",
        model="x",
        status="idle",
        started_at=UTC_NOW,
    )
    repo.upsert_agent(agent)
    fetched = repo.get_agent_by_slug("agt-99")
    assert fetched is not None
    assert fetched.name == "Recovered"


def test_upsert_agent_updates_when_present(repo: SqlWorkRepository) -> None:
    work = repo.add_work(_new_work())
    assert work.id is not None
    repo.add_agent(_new_agent(work_id=work.id))
    fresh = Agent(
        id=1,
        slug="agt-1",
        work_id=work.id,
        name="Updated",
        persona="developer",
        role="dev",
        provider="claude-code",
        model="claude-opus-4-7",
        status="live",
        started_at=UTC_NOW,
    )
    repo.upsert_agent(fresh)
    fetched = repo.get_agent_by_slug("agt-1")
    assert fetched is not None
    assert fetched.persona == "developer"
    assert fetched.status == "live"


def test_delete_agent(repo: SqlWorkRepository) -> None:
    work = repo.add_work(_new_work())
    assert work.id is not None
    repo.add_agent(_new_agent(work_id=work.id))
    repo.delete_agent("agt-1")
    assert repo.get_agent_by_slug("agt-1") is None


# ---------------------------------------------------------------------------
# Artifact / Handoff
# ---------------------------------------------------------------------------


def test_add_artifact_assigns_id_and_slug(repo: SqlWorkRepository) -> None:
    work = repo.add_work(_new_work())
    assert work.id is not None
    artifact = repo.add_artifact(
        Artifact(
            work_id=work.id,
            agent_id=None,
            type="pr",
            title="PR-1",
            status="open",
            created_at=UTC_NOW,
            url="https://github.com/x/y/pull/1",
        )
    )
    assert artifact.id == 1
    assert artifact.slug == "art-1"


def test_add_handoff_assigns_id_and_slug(repo: SqlWorkRepository) -> None:
    work = repo.add_work(_new_work())
    assert work.id is not None
    a1 = repo.add_agent(_new_agent(work_id=work.id, name="A"))
    a2 = repo.add_agent(_new_agent(work_id=work.id, name="B"))
    assert a1.id is not None and a2.id is not None

    handoff = repo.add_handoff(
        Handoff(
            work_id=work.id,
            source_agent_id=a1.id,
            doc_path=Path("/Atelier/works/WRK-001/handoffs/x.md"),
            created_at=UTC_NOW,
            target_agent_id=a2.id,
        )
    )
    assert handoff.id == 1
    assert handoff.slug == "hnd-1"
