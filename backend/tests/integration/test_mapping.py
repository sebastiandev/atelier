"""Round-trip domain entities through SQLAlchemy's imperative mapping.

These verify the mapping wiring (entity ↔ table) works end-to-end. Repository
behaviour (encapsulating the session, generating slugs, hydrating Work with
its children, etc.) is tested in STORY-005.

Tests pass slugs explicitly because the repository (which would auto-generate
them post-flush) doesn't exist yet.
"""

from datetime import UTC, datetime
from pathlib import Path

import pytest
from sqlalchemy import Engine
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from src.domain.models import Agent, Artifact, Connection, Handoff, Work

UTC_NOW = datetime(2026, 4, 30, 12, 0, tzinfo=UTC)


# Helper builders return the persisted entity (with int id assigned).
def _seed_work(session: Session, slug: str = "WRK-001") -> Work:
    work = Work(
        slug=slug,
        name="Fix checkout",
        description="500 spike",
        status="active",
        created_at=UTC_NOW,
    )
    session.add(work)
    session.flush()
    return work


def _seed_agent(
    session: Session, work_id: int, slug: str = "agt-1"
) -> Agent:
    agent = Agent(
        slug=slug,
        work_id=work_id,
        name="Architect",
        persona="architect",
        role="architect",
        provider="claude-code",
        model="claude-opus-4-7",
        folder=Path("/Users/seba/code/shop"),
        status="idle",
        started_at=UTC_NOW,
    )
    session.add(agent)
    session.flush()
    return agent


# ---------------------------------------------------------------------------
# Work
# ---------------------------------------------------------------------------


def test_work_round_trip(isolated_engine: Engine) -> None:
    with Session(isolated_engine) as session:
        work = _seed_work(session)
        session.commit()
        work_id = work.id

    assert work_id is not None
    with Session(isolated_engine) as session:
        loaded = session.get(Work, work_id)
        assert loaded is not None
        assert loaded.id == work_id
        assert loaded.slug == "WRK-001"
        assert loaded.name == "Fix checkout"
        assert loaded.status == "active"


def test_work_id_is_autoincrement_int(isolated_engine: Engine) -> None:
    with Session(isolated_engine) as session:
        a = _seed_work(session, slug="WRK-001")
        b = Work(
            slug="WRK-002",
            name="x",
            description="y",
            status="active",
            created_at=UTC_NOW,
        )
        session.add(b)
        session.flush()
        session.commit()

        assert isinstance(a.id, int)
        assert isinstance(b.id, int)
        assert b.id > a.id  # type: ignore[operator]


def test_work_slug_is_unique(isolated_engine: Engine) -> None:
    with Session(isolated_engine) as session:
        _seed_work(session, slug="WRK-001")
        session.commit()

    with Session(isolated_engine) as session, pytest.raises(IntegrityError):
            duplicate = Work(
                slug="WRK-001",  # collides with the previous insert
                name="other",
                description="other",
                status="active",
                created_at=UTC_NOW,
            )
            session.add(duplicate)
            session.commit()


def test_work_created_at_round_trips_as_tz_aware(isolated_engine: Engine) -> None:
    with Session(isolated_engine) as session:
        work = _seed_work(session)
        session.commit()
        work_id = work.id

    with Session(isolated_engine) as session:
        loaded = session.get(Work, work_id)
        assert loaded is not None
        assert loaded.created_at.tzinfo is not None


# ---------------------------------------------------------------------------
# Agent
# ---------------------------------------------------------------------------


def test_agent_round_trip_with_defaults(isolated_engine: Engine) -> None:
    with Session(isolated_engine) as session:
        work = _seed_work(session)
        agent = _seed_agent(session, work_id=work.id)  # type: ignore[arg-type]
        session.commit()
        agent_id = agent.id

    with Session(isolated_engine) as session:
        loaded = session.get(Agent, agent_id)
        assert loaded is not None
        assert loaded.slug == "agt-1"
        assert loaded.persona == "architect"
        assert loaded.provider == "claude-code"
        assert loaded.stopped_at is None


def test_agent_lifecycle_stop(isolated_engine: Engine) -> None:
    with Session(isolated_engine) as session:
        work = _seed_work(session)
        agent = _seed_agent(session, work_id=work.id)  # type: ignore[arg-type]
        agent.status = "idle"
        agent.stopped_at = UTC_NOW
        session.commit()
        agent_id = agent.id

    with Session(isolated_engine) as session:
        loaded = session.get(Agent, agent_id)
        assert loaded is not None
        assert loaded.status == "idle"
        assert loaded.stopped_at is not None


def test_agent_work_id_is_int_fk(isolated_engine: Engine) -> None:
    with Session(isolated_engine) as session:
        work = _seed_work(session)
        agent = _seed_agent(session, work_id=work.id)  # type: ignore[arg-type]
        session.commit()

        assert isinstance(agent.work_id, int)
        assert agent.work_id == work.id


# ---------------------------------------------------------------------------
# Connection — security: token / keyring_ref must not be persisted
# ---------------------------------------------------------------------------


def test_connection_round_trip(isolated_engine: Engine) -> None:
    """Mapping-level round-trip — bypasses the repo, so ``config`` is the
    dict shape the JsonDict TypeDecorator expects on the bind side."""
    with Session(isolated_engine) as session:
        conn = Connection(
            slug="con-1",
            type="jira",
            name="Acme",
            created_at=UTC_NOW,
            config={"url": "https://acme.atlassian.net", "email": "a@b.com"},
            verified=True,
        )
        session.add(conn)
        session.commit()
        conn_id = conn.id

    with Session(isolated_engine) as session:
        loaded = session.get(Connection, conn_id)
        assert loaded is not None
        assert loaded.slug == "con-1"
        assert loaded.verified is True
        assert loaded.config == {
            "url": "https://acme.atlassian.net",
            "email": "a@b.com",
        }


def test_connections_table_has_no_token_or_keyring_ref_columns(
    isolated_engine: Engine,
) -> None:
    with isolated_engine.connect() as raw:
        cols = raw.exec_driver_sql("PRAGMA table_info(connections)").all()
    col_names = {row[1] for row in cols}
    assert "token" not in col_names
    assert "keyring_ref" not in col_names


# ---------------------------------------------------------------------------
# Artifact
# ---------------------------------------------------------------------------


def test_artifact_round_trip(isolated_engine: Engine) -> None:
    with Session(isolated_engine) as session:
        work = _seed_work(session)
        agent = _seed_agent(session, work_id=work.id)  # type: ignore[arg-type]
        artifact = Artifact(
            slug="art-1",
            work_id=work.id,  # type: ignore[arg-type]
            agent_id=agent.id,
            type="pr",
            title="Fix",
            status="open",
            created_at=UTC_NOW,
            url="https://github.com/owner/repo/pull/42",
        )
        session.add(artifact)
        session.commit()
        artifact_id = artifact.id

    with Session(isolated_engine) as session:
        loaded = session.get(Artifact, artifact_id)
        assert loaded is not None
        assert loaded.slug == "art-1"
        assert loaded.type == "pr"
        assert loaded.url == "https://github.com/owner/repo/pull/42"
        assert loaded.repo is None


# ---------------------------------------------------------------------------
# Handoff
# ---------------------------------------------------------------------------


def test_handoff_round_trip_to_existing_agent(isolated_engine: Engine) -> None:
    with Session(isolated_engine) as session:
        work = _seed_work(session)
        a1 = _seed_agent(session, work_id=work.id, slug="agt-1")  # type: ignore[arg-type]
        a2 = _seed_agent(session, work_id=work.id, slug="agt-2")  # type: ignore[arg-type]
        h = Handoff(
            slug="hnd-1",
            work_id=work.id,  # type: ignore[arg-type]
            source_agent_id=a1.id,  # type: ignore[arg-type]
            doc_path=Path("handoffs/x.md"),
            created_at=UTC_NOW,
            target_agent_id=a2.id,
        )
        session.add(h)
        session.commit()
        h_id = h.id

    with Session(isolated_engine) as session:
        loaded = session.get(Handoff, h_id)
        assert loaded is not None
        assert loaded.target_agent_id is not None
        assert loaded.target_dialog is None
        assert isinstance(loaded.doc_path, Path)


def test_handoff_round_trip_to_new_agent_dialog(isolated_engine: Engine) -> None:
    with Session(isolated_engine) as session:
        work = _seed_work(session)
        agent = _seed_agent(session, work_id=work.id)  # type: ignore[arg-type]
        h = Handoff(
            slug="hnd-1",
            work_id=work.id,  # type: ignore[arg-type]
            source_agent_id=agent.id,  # type: ignore[arg-type]
            doc_path=Path("handoffs/y.md"),
            created_at=UTC_NOW,
            target_dialog="new-agent",
        )
        session.add(h)
        session.commit()
        h_id = h.id

    with Session(isolated_engine) as session:
        loaded = session.get(Handoff, h_id)
        assert loaded is not None
        assert loaded.target_dialog == "new-agent"
        assert loaded.target_agent_id is None


# ---------------------------------------------------------------------------
# Foreign-key cascade — verifies our schema's deletion semantics are wired
# ---------------------------------------------------------------------------


def test_deleting_work_cascades_to_agents_and_artifacts(isolated_engine: Engine) -> None:
    with Session(isolated_engine) as session:
        work = _seed_work(session)
        agent = _seed_agent(session, work_id=work.id)  # type: ignore[arg-type]
        session.add(
            Artifact(
                slug="art-1",
                work_id=work.id,  # type: ignore[arg-type]
                agent_id=agent.id,
                type="doc",
                title="t",
                status="draft",
                created_at=UTC_NOW,
            )
        )
        session.commit()
        work_id = work.id
        agent_id = agent.id

    with Session(isolated_engine) as session:
        w = session.get(Work, work_id)
        assert w is not None
        session.delete(w)
        session.commit()

    with Session(isolated_engine) as session:
        assert session.get(Work, work_id) is None
        assert session.get(Agent, agent_id) is None


def test_deleting_agent_sets_artifact_agent_id_null(isolated_engine: Engine) -> None:
    """ON DELETE SET NULL on artifacts.agent_id keeps the artifact alive."""
    with Session(isolated_engine) as session:
        work = _seed_work(session)
        agent = _seed_agent(session, work_id=work.id)  # type: ignore[arg-type]
        artifact = Artifact(
            slug="art-1",
            work_id=work.id,  # type: ignore[arg-type]
            agent_id=agent.id,
            type="doc",
            title="t",
            status="draft",
            created_at=UTC_NOW,
        )
        session.add(artifact)
        session.commit()
        agent_id = agent.id
        artifact_id = artifact.id

    with Session(isolated_engine) as session:
        a = session.get(Agent, agent_id)
        assert a is not None
        session.delete(a)
        session.commit()

    with Session(isolated_engine) as session:
        loaded = session.get(Artifact, artifact_id)
        assert loaded is not None
        assert loaded.agent_id is None
