"""Unit tests for domain entities — defaults, structural invariants, identity rules.

Entities are plain mutable dataclasses (not frozen, no slots) because SA's
imperative mapping populates them via setattr at load time.

Identity model: each persisted entity has both an integer `id` (the SQL PK)
and a `slug` (the user-visible identifier). Both default to None on the
dataclass; the repository populates them at create/persist time.
"""

from dataclasses import fields
from datetime import UTC, datetime
from pathlib import Path

import pytest

from src.domain.models import (
    Agent,
    Artifact,
    Connection,
    Context,
    Handoff,
    Work,
)

UTC_NOW = datetime(2026, 4, 30, 12, 0, tzinfo=UTC)


# ---------------------------------------------------------------------------
# Context — JSON-only, not mapped to SQL
# ---------------------------------------------------------------------------


def test_context_construction_with_required_fields() -> None:
    ctx = Context(type="text", value="some prose")
    assert ctx.type == "text"
    assert ctx.value == "some prose"
    assert ctx.conn_id is None


def test_context_conn_id_is_a_slug_not_an_int() -> None:
    """Context lives in work.json; cross-references are slugs for human readability."""
    ctx = Context(type="jira", value="PROJ-123", conn_id="con-3")
    assert ctx.conn_id == "con-3"
    assert isinstance(ctx.conn_id, str)


# ---------------------------------------------------------------------------
# Agent
# ---------------------------------------------------------------------------


def _agent(**overrides: object) -> Agent:
    base: dict[str, object] = dict(
        work_id=1,
        name="Architect",
        persona="architect",
        role="architect",
        provider="claude-code",
        model="claude-opus-4-7",
        status="idle",
        started_at=UTC_NOW,
    )
    base.update(overrides)
    return Agent(**base)  # type: ignore[arg-type]


def test_agent_defaults() -> None:
    agent = _agent()
    assert agent.id is None
    assert agent.slug is None
    assert agent.stopped_at is None


def test_agent_has_no_transcript_field() -> None:
    """Architecture rule: transcripts are stream records, not embedded in the entity."""
    field_names = {f.name for f in fields(Agent)}
    assert "transcript" not in field_names


def test_agent_has_no_ui_state_fields() -> None:
    """UI state (pinned tile, windows-mode coords, persona glyph) lives in the
    frontend's session store, not the domain entity. The backend stays
    presentation-agnostic.
    """
    field_names = {f.name for f in fields(Agent)}
    assert "pinned" not in field_names
    assert "x" not in field_names
    assert "y" not in field_names
    assert "glyph" not in field_names


def test_agent_work_id_is_int() -> None:
    """SQL FKs use the integer PK, not the slug."""
    agent = _agent(work_id=42)
    assert agent.work_id == 42
    assert isinstance(agent.work_id, int)


# ---------------------------------------------------------------------------
# Connection
# ---------------------------------------------------------------------------


def test_connection_has_no_token_field() -> None:
    """Security rule: tokens live in the OS keychain, never on the entity or in the DB."""
    field_names = {f.name for f in fields(Connection)}
    assert "token" not in field_names
    assert "keyring_ref" not in field_names


def test_connection_minimal_construction() -> None:
    conn = Connection(
        type="jira",
        name="Acme Jira",
        created_at=UTC_NOW,
    )
    assert conn.id is None
    assert conn.slug is None
    assert conn.verified is False
    assert conn.url is None
    assert conn.last_used is None


# ---------------------------------------------------------------------------
# Artifact
# ---------------------------------------------------------------------------


def test_artifact_construction() -> None:
    art = Artifact(
        work_id=1,
        agent_id=1,
        type="pr",
        title="Fix checkout 500",
        status="open",
        created_at=UTC_NOW,
    )
    assert art.repo is None
    assert art.url is None
    assert art.doc_path is None


def test_artifact_allows_null_agent_id() -> None:
    """Architecture: artifact.agent_id is FK ON DELETE SET NULL."""
    art = Artifact(
        work_id=1,
        agent_id=None,
        type="doc",
        title="t",
        status="draft",
        created_at=UTC_NOW,
    )
    assert art.agent_id is None


# ---------------------------------------------------------------------------
# Handoff
# ---------------------------------------------------------------------------


def test_handoff_to_existing_agent() -> None:
    h = Handoff(
        work_id=1,
        source_agent_id=1,
        doc_path=Path("handoffs/x.md"),
        created_at=UTC_NOW,
        target_agent_id=2,
    )
    assert h.target_agent_id == 2
    assert h.target_dialog is None


def test_handoff_to_new_agent_dialog() -> None:
    h = Handoff(
        work_id=1,
        source_agent_id=1,
        doc_path=Path("handoffs/x.md"),
        created_at=UTC_NOW,
        target_dialog="new-agent",
    )
    assert h.target_dialog == "new-agent"
    assert h.target_agent_id is None


def test_handoff_rejects_no_target() -> None:
    with pytest.raises(ValueError, match="exactly one"):
        Handoff(
            work_id=1,
            source_agent_id=1,
            doc_path=Path("x.md"),
            created_at=UTC_NOW,
        )


def test_handoff_rejects_both_targets() -> None:
    with pytest.raises(ValueError, match="exactly one"):
        Handoff(
            work_id=1,
            source_agent_id=1,
            doc_path=Path("x.md"),
            created_at=UTC_NOW,
            target_agent_id=2,
            target_dialog="new-agent",
        )


# ---------------------------------------------------------------------------
# Work
# ---------------------------------------------------------------------------


def test_work_construction() -> None:
    work = Work(
        name="Fix checkout",
        description="500 spike on /checkout",
        folder=Path("/Users/seba/code/shop"),
        status="active",
        created_at=UTC_NOW,
    )
    assert work.id is None
    assert work.slug is None
    assert work.status == "active"


def test_work_has_no_embedded_children() -> None:
    """Architecture: persisted Work is meta only.

    Children (agents, artifacts, contexts) are fetched via separate repository
    calls and aggregated by the API response schema, not stored on the entity.
    """
    field_names = {f.name for f in fields(Work)}
    assert "agents" not in field_names
    assert "artifacts" not in field_names
    assert "contexts" not in field_names


def test_work_carries_id_and_slug() -> None:
    """Both identifiers are first-class fields on every public entity."""
    field_names = {f.name for f in fields(Work)}
    assert "id" in field_names
    assert "slug" in field_names


def test_work_value_equality() -> None:
    a = Work(
        id=1,
        slug="WRK-001",
        name="n",
        description="d",
        folder=Path("/tmp"),
        status="active",
        created_at=UTC_NOW,
    )
    b = Work(
        id=1,
        slug="WRK-001",
        name="n",
        description="d",
        folder=Path("/tmp"),
        status="active",
        created_at=UTC_NOW,
    )
    assert a == b
