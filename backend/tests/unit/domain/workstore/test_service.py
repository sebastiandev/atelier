"""Unit tests for WorkStoreService against in-memory port stubs.

These exercise the domain-level policy: slug allocation, FS+DB
composition, parent-existence checks, clock injection. Real SA / FS
behaviour is verified separately in the integration suite.
"""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path

import pytest

from src.domain.models import Context
from src.domain.workstore import (
    AddAgentRequest,
    CreateWorkRequest,
    RecordArtifactRequest,
    RecordHandoffRequest,
    UpdateWorkRequest,
    WorkStoreService,
)
from tests.unit.domain.workstore._stubs import (
    StubFiles,
    StubRepository,
    StubTranscriptLog,
)

FIXED_NOW = datetime(2026, 5, 1, 13, 49, tzinfo=UTC)


def _make_service(
    *, clock_value: datetime = FIXED_NOW
) -> tuple[WorkStoreService, StubRepository, StubFiles, StubTranscriptLog]:
    repo = StubRepository()
    files = StubFiles()
    log = StubTranscriptLog()
    service = WorkStoreService(repo, files, log, clock=lambda: clock_value)
    return service, repo, files, log


def _new_work_request(
    name: str = "Migration", contexts: list[Context] | None = None
) -> CreateWorkRequest:
    return CreateWorkRequest(
        name=name,
        description=f"Brief for {name}",
        contexts=contexts or [],
    )


def _agent_request(
    work_slug: str = "WRK-001",
    name: str = "Architect",
    persona: str = "architect",
    role: str = "architect",
    provider: str = "claude-code",
    model: str = "claude-opus-4-7",
    folder: str = "/code/foo",
) -> AddAgentRequest:
    return AddAgentRequest(
        work_slug=work_slug,
        name=name,
        persona=persona,
        role=role,
        provider=provider,
        model=model,
        folder=Path(folder),
    )


# ---------------------------------------------------------------------------
# create_work
# ---------------------------------------------------------------------------


def test_create_work_returns_record_with_id_and_slug() -> None:
    service, _, _, _ = _make_service()
    record = service.create_work(_new_work_request())
    assert record.work.id == 1
    assert record.work.slug == "WRK-001"
    assert record.work.status == "active"
    assert record.work.created_at == FIXED_NOW


def test_create_work_writes_to_repo_and_files() -> None:
    service, repo, files, _ = _make_service()
    service.create_work(_new_work_request(name="Migration"))

    assert "WRK-001" in repo.works
    assert "WRK-001" in files.work_dirs
    assert "WRK-001" in files.work_jsons
    assert files.briefs["WRK-001"] == "Brief for Migration"

    work_data = files.work_jsons["WRK-001"]
    assert work_data["slug"] == "WRK-001"
    assert work_data["name"] == "Migration"
    assert "folder" not in work_data
    assert work_data["contexts"] == []


def test_create_work_persists_contexts_to_work_json() -> None:
    service, _, files, _ = _make_service()
    contexts = [
        Context(type="jira", value="FOO-123", conn_id="con-1"),
        Context(type="url", value="https://example.test/x"),
    ]
    record = service.create_work(_new_work_request(contexts=contexts))

    assert record.contexts == contexts
    written = files.work_jsons["WRK-001"]["contexts"]
    assert written == [
        {"type": "jira", "value": "FOO-123", "conn_id": "con-1"},
        {"type": "url", "value": "https://example.test/x"},
    ]


def test_create_work_increments_slug_per_call() -> None:
    service, _, _, _ = _make_service()
    a = service.create_work(_new_work_request(name="A"))
    b = service.create_work(_new_work_request(name="B"))
    assert a.work.slug == "WRK-001"
    assert b.work.slug == "WRK-002"


# ---------------------------------------------------------------------------
# get_work / list_works
# ---------------------------------------------------------------------------


def test_get_work_returns_record_combining_repo_and_files() -> None:
    service, _, _, _ = _make_service()
    contexts = [Context(type="text", value="see deck")]
    service.create_work(_new_work_request(name="Plan", contexts=contexts))

    fetched = service.get_work("WRK-001")
    assert fetched is not None
    assert fetched.work.name == "Plan"
    assert fetched.contexts == contexts


def test_get_work_returns_none_when_repo_lacks_row() -> None:
    service, _, _, _ = _make_service()
    assert service.get_work("WRK-999") is None


def test_get_work_returns_empty_contexts_when_work_json_missing() -> None:
    """If FS metadata is missing for a DB row, degrade gracefully — reconcile
    will eventually delete the orphan row."""
    service, repo, _, _ = _make_service()
    service.create_work(_new_work_request())
    # Simulate FS loss after the create.
    repo.works["WRK-001"]  # confirm row still present
    files = service._files  # type: ignore[attr-defined]
    files.work_jsons.clear()

    fetched = service.get_work("WRK-001")
    assert fetched is not None
    assert fetched.contexts == []


def test_list_works_returns_all() -> None:
    service, _, _, _ = _make_service()
    service.create_work(_new_work_request(name="A"))
    service.create_work(_new_work_request(name="B"))
    works = service.list_works()
    assert {w.slug for w in works} == {"WRK-001", "WRK-002"}


# ---------------------------------------------------------------------------
# add_agent_to_work
# ---------------------------------------------------------------------------


def test_add_agent_persists_row_and_writes_agent_json() -> None:
    service, repo, files, _ = _make_service()
    service.create_work(_new_work_request())

    agent = service.add_agent_to_work(_agent_request())

    assert agent.id == 1
    assert agent.slug == "agt-1"
    assert agent.work_id == 1
    assert "agt-1" in repo.agents
    assert ("WRK-001", "agt-1") in files.agent_dirs
    assert files.agent_jsons[("WRK-001", "agt-1")]["persona"] == "architect"
    assert files.agent_jsons[("WRK-001", "agt-1")]["folder"] == "/code/foo"


def test_add_agent_raises_when_work_not_found() -> None:
    service, _, _, _ = _make_service()
    with pytest.raises(ValueError, match="work not found"):
        service.add_agent_to_work(_agent_request(work_slug="WRK-999"))


def test_list_agents_for_work_returns_added_agents() -> None:
    service, _, _, _ = _make_service()
    service.create_work(_new_work_request())
    service.add_agent_to_work(_agent_request(name="Architect"))
    service.add_agent_to_work(
        _agent_request(name="Developer", persona="developer", role="developer", provider="amp", model="smart")
    )

    agents = service.list_agents_for_work("WRK-001")
    assert [a.slug for a in agents] == ["agt-1", "agt-2"]


def test_list_agents_for_work_raises_when_work_not_found() -> None:
    service, _, _, _ = _make_service()
    with pytest.raises(ValueError, match="work not found"):
        service.list_agents_for_work("WRK-999")


# ---------------------------------------------------------------------------
# transcript
# ---------------------------------------------------------------------------


def test_append_transcript_event_delegates_to_log() -> None:
    service, _, _, log = _make_service()
    service.append_transcript_event("WRK-001", "agt-1", {"seq": 1, "type": "user"})
    assert log.events[("WRK-001", "agt-1")] == [{"seq": 1, "type": "user"}]


def test_read_transcript_from_cursor_filters_by_seq() -> None:
    service, _, _, log = _make_service()
    log.events[("WRK-001", "agt-1")] = [
        {"seq": 1, "v": "a"},
        {"seq": 2, "v": "b"},
        {"seq": 3, "v": "c"},
    ]
    out = list(service.read_transcript_from_cursor("WRK-001", "agt-1", cursor=1))
    assert [e["seq"] for e in out] == [2, 3]


# ---------------------------------------------------------------------------
# record_artifact
# ---------------------------------------------------------------------------


def test_record_artifact_with_agent_links_both_ids() -> None:
    service, repo, _, _ = _make_service()
    service.create_work(_new_work_request())
    service.add_agent_to_work(
        _agent_request(name="Dev", persona="developer", role="dev", model="x")
    )

    artifact = service.record_artifact(
        RecordArtifactRequest(
            work_slug="WRK-001",
            agent_slug="agt-1",
            type="pr",
            title="Fix bug",
            status="open",
            url="https://github.com/owner/repo/pull/1",
        )
    )
    assert artifact.id == 1
    assert artifact.slug == "art-1"
    assert artifact.work_id == 1
    assert artifact.agent_id == 1
    assert artifact.url == "https://github.com/owner/repo/pull/1"
    assert "art-1" in repo.artifacts


def test_record_artifact_without_agent_has_null_agent_id() -> None:
    service, _, _, _ = _make_service()
    service.create_work(_new_work_request())
    artifact = service.record_artifact(
        RecordArtifactRequest(
            work_slug="WRK-001",
            type="doc",
            title="Notes",
            status="draft",
        )
    )
    assert artifact.agent_id is None


def test_record_artifact_raises_when_work_missing() -> None:
    service, _, _, _ = _make_service()
    with pytest.raises(ValueError, match="work not found"):
        service.record_artifact(
            RecordArtifactRequest(
                work_slug="WRK-404",
                type="doc",
                title="x",
                status="draft",
            )
        )


def test_record_artifact_raises_when_agent_missing() -> None:
    service, _, _, _ = _make_service()
    service.create_work(_new_work_request())
    with pytest.raises(ValueError, match="agent not found"):
        service.record_artifact(
            RecordArtifactRequest(
                work_slug="WRK-001",
                agent_slug="agt-404",
                type="doc",
                title="x",
                status="draft",
            )
        )


# ---------------------------------------------------------------------------
# record_handoff
# ---------------------------------------------------------------------------


def test_record_handoff_writes_doc_and_persists_row() -> None:
    service, repo, files, _ = _make_service()
    service.create_work(_new_work_request())
    service.add_agent_to_work(_agent_request(name="A", role="r", model="x"))
    service.add_agent_to_work(
        _agent_request(name="D", persona="developer", role="r", model="x")
    )

    handoff = service.record_handoff(
        RecordHandoffRequest(
            work_slug="WRK-001",
            source_agent_slug="agt-1",
            target_agent_slug="agt-2",
            doc_text="# Decisions\n...",
            doc_filename="agt-1-to-agt-2.md",
        )
    )

    assert handoff.id == 1
    assert handoff.slug == "hnd-1"
    assert handoff.source_agent_id == 1
    assert handoff.target_agent_id == 2
    assert handoff.target_dialog is None
    assert files.handoff_docs[("WRK-001", "agt-1-to-agt-2.md")] == "# Decisions\n..."
    assert "hnd-1" in repo.handoffs


def test_record_handoff_with_dialog_target_has_no_target_agent_id() -> None:
    service, _, _, _ = _make_service()
    service.create_work(_new_work_request())
    service.add_agent_to_work(_agent_request(name="A", role="r", model="x"))

    handoff = service.record_handoff(
        RecordHandoffRequest(
            work_slug="WRK-001",
            source_agent_slug="agt-1",
            target_dialog="new-agent",
            doc_text="# Context\n...",
            doc_filename="agt-1-to-new.md",
        )
    )
    assert handoff.target_agent_id is None
    assert handoff.target_dialog == "new-agent"


def test_record_handoff_raises_when_source_agent_missing() -> None:
    service, _, _, _ = _make_service()
    service.create_work(_new_work_request())
    with pytest.raises(ValueError, match="source agent not found"):
        service.record_handoff(
            RecordHandoffRequest(
                work_slug="WRK-001",
                source_agent_slug="agt-999",
                target_dialog="new-agent",
                doc_text="x",
                doc_filename="x.md",
            )
        )


# ---------------------------------------------------------------------------
# clock injection
# ---------------------------------------------------------------------------


def test_injected_clock_drives_created_at() -> None:
    moment = datetime(2030, 1, 1, tzinfo=UTC)
    service, _, _, _ = _make_service(clock_value=moment)
    record = service.create_work(_new_work_request())
    assert record.work.created_at == moment


# ---------------------------------------------------------------------------
# update_work / soft_delete_work / list_works filtering
# ---------------------------------------------------------------------------


def test_update_work_changes_individual_fields() -> None:
    service, _, files, _ = _make_service()
    service.create_work(_new_work_request(name="Old"))
    record = service.update_work(UpdateWorkRequest(work_slug="WRK-001", name="New"))

    assert record.work.name == "New"
    assert files.work_jsons["WRK-001"]["name"] == "New"


def test_update_work_rewrites_brief_md_when_description_changes() -> None:
    service, _, files, _ = _make_service()
    service.create_work(_new_work_request())
    service.update_work(UpdateWorkRequest(work_slug="WRK-001", description="brand new brief"))
    assert files.briefs["WRK-001"] == "brand new brief"


def test_update_work_replaces_contexts_when_provided() -> None:
    service, _, files, _ = _make_service()
    service.create_work(_new_work_request(contexts=[Context(type="text", value="old")]))
    new_contexts = [Context(type="url", value="https://x.test/new")]
    record = service.update_work(UpdateWorkRequest(work_slug="WRK-001", contexts=new_contexts))
    assert record.contexts == new_contexts
    assert files.work_jsons["WRK-001"]["contexts"] == [
        {"type": "url", "value": "https://x.test/new"}
    ]


def test_update_work_leaves_contexts_alone_when_not_provided() -> None:
    service, _, _, _ = _make_service()
    original = [Context(type="text", value="keep")]
    service.create_work(_new_work_request(contexts=original))
    record = service.update_work(UpdateWorkRequest(work_slug="WRK-001", name="New"))
    assert record.contexts == original


def test_update_work_raises_for_unknown_slug() -> None:
    service, _, _, _ = _make_service()
    with pytest.raises(ValueError, match="work not found"):
        service.update_work(UpdateWorkRequest(work_slug="WRK-404", name="x"))


def test_soft_delete_work_marks_status_in_repo_and_files() -> None:
    service, repo, files, _ = _make_service()
    service.create_work(_new_work_request())
    service.soft_delete_work("WRK-001")

    assert repo.works["WRK-001"].status == "deleted"
    assert files.work_jsons["WRK-001"]["status"] == "deleted"


def test_soft_delete_then_list_excludes_work() -> None:
    service, _, _, _ = _make_service()
    service.create_work(_new_work_request(name="A"))
    service.create_work(_new_work_request(name="B"))
    service.soft_delete_work("WRK-001")

    works = service.list_works()
    assert {w.slug for w in works} == {"WRK-002"}


def test_soft_delete_then_get_returns_none() -> None:
    service, _, _, _ = _make_service()
    service.create_work(_new_work_request())
    service.soft_delete_work("WRK-001")
    assert service.get_work("WRK-001") is None


def test_soft_delete_makes_subsequent_update_404() -> None:
    service, _, _, _ = _make_service()
    service.create_work(_new_work_request())
    service.soft_delete_work("WRK-001")
    with pytest.raises(ValueError, match="work not found"):
        service.update_work(UpdateWorkRequest(work_slug="WRK-001", name="x"))


def test_soft_delete_unknown_raises() -> None:
    service, _, _, _ = _make_service()
    with pytest.raises(ValueError, match="work not found"):
        service.soft_delete_work("WRK-404")
