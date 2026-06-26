"""Unit tests for WorkStoreService against in-memory port stubs.

These exercise the domain-level policy: slug allocation, FS+DB
composition, parent-existence checks, clock injection. Real SA / FS
behaviour is verified separately in the integration suite.
"""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path

import pytest

from src.domain.artifacts import make_artifact
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


def test_add_agent_persists_options_when_supplied() -> None:
    """Provider-specific options (permission_mode, thinking_effort, …)
    must round-trip onto the entity so resume + detach pick them back
    up. Empty/missing → None (not an empty dict) so legacy rows that
    predate the column round-trip identically."""
    service, _, files, _ = _make_service()
    service.create_work(_new_work_request())
    request = AddAgentRequest(
        work_slug="WRK-001",
        name="Architect",
        persona="architect",
        role="architect",
        provider="claude-code",
        model="claude-opus-4-7",
        folder=Path("/code/foo"),
        options={"permission_mode": "acceptEdits", "thinking_effort": "high"},
    )
    agent = service.add_agent_to_work(request)
    assert agent.options == {
        "permission_mode": "acceptEdits",
        "thinking_effort": "high",
    }
    # And the on-disk agent.json mirrors the entity.
    persisted = files.agent_jsons[("WRK-001", "agt-1")]
    assert persisted["options"] == agent.options


def test_add_agent_normalises_empty_options_to_none() -> None:
    """Empty options dict from the route → ``None`` on the row so the
    column stores SQL NULL (cleaner than a stored ``{}``) and on-disk
    agent.json omits the key entirely (legacy-shape parity)."""
    service, _, files, _ = _make_service()
    service.create_work(_new_work_request())
    agent = service.add_agent_to_work(_agent_request())  # no options
    assert agent.options is None
    persisted = files.agent_jsons[("WRK-001", "agt-1")]
    assert "options" not in persisted


def test_list_agents_for_work_returns_added_agents() -> None:
    service, _, _, _ = _make_service()
    service.create_work(_new_work_request())
    service.add_agent_to_work(_agent_request(name="Architect"))
    service.add_agent_to_work(
        _agent_request(
            name="Developer",
            persona="developer",
            role="developer",
            provider="amp",
            model="smart",
        )
    )

    agents = service.list_agents_for_work("WRK-001")
    assert [a.slug for a in agents] == ["agt-1", "agt-2"]


def test_set_agent_session_id_mirrors_agent_json_only_when_requested() -> None:
    service, repo, files, _ = _make_service()
    service.create_work(_new_work_request())
    service.add_agent_to_work(
        AddAgentRequest(
            work_slug="WRK-001",
            name="Developer",
            persona="developer",
            role="dev",
            provider="codex",
            model="gpt-5.5",
            folder=Path("/code/foo"),
            contexts=(Context(type="text", value="handoff"),),
            options={"reasoning_effort": "high"},
        )
    )

    service.set_agent_session_id("agt-1", "old-session")
    assert files.agent_jsons[("WRK-001", "agt-1")]["session_id"] is None

    service.set_agent_session_id("agt-1", "new-session", mirror_agent_json=True)

    assert repo.agents["agt-1"].session_id == "new-session"
    assert repo.agents["agt-1"].parent_session_id == "old-session"
    persisted = files.agent_jsons[("WRK-001", "agt-1")]
    assert persisted["session_id"] == "new-session"
    assert persisted["parent_session_id"] == "old-session"
    assert persisted["contexts"] == [{"type": "text", "value": "handoff"}]
    assert persisted["options"] == {"reasoning_effort": "high"}


def test_set_agent_model_updates_repo_and_agent_json() -> None:
    service, repo, files, _ = _make_service()
    service.create_work(_new_work_request())
    service.add_agent_to_work(
        AddAgentRequest(
            work_slug="WRK-001",
            name="Developer",
            persona="developer",
            role="dev",
            provider="opencode",
            model="configured-default",
            folder=Path("/code/foo"),
            contexts=(Context(type="text", value="handoff"),),
        )
    )

    service.set_agent_model("agt-1", "opencode/gpt-5")

    assert repo.agents["agt-1"].model == "opencode/gpt-5"
    persisted = files.agent_jsons[("WRK-001", "agt-1")]
    assert persisted["model"] == "opencode/gpt-5"
    assert persisted["contexts"] == [{"type": "text", "value": "handoff"}]


def test_set_agent_option_updates_existing_repo_row_and_agent_json() -> None:
    service, repo, files, _ = _make_service()
    service.create_work(_new_work_request())
    service.add_agent_to_work(
        AddAgentRequest(
            work_slug="WRK-001",
            name="Developer",
            persona="developer",
            role="dev",
            provider="codex-acp",
            model="gpt-5.5",
            folder=Path("/code/foo"),
            contexts=(Context(type="text", value="handoff"),),
            options={"mode": "auto", "reasoning_effort": "medium"},
        )
    )

    service.set_agent_option("agt-1", "reasoning_effort", "xhigh")

    assert list(repo.agents) == ["agt-1"]
    assert repo.agents["agt-1"].options == {
        "mode": "auto",
        "reasoning_effort": "xhigh",
    }
    persisted = files.agent_jsons[("WRK-001", "agt-1")]
    assert persisted["options"] == repo.agents["agt-1"].options
    assert persisted["contexts"] == [{"type": "text", "value": "handoff"}]


def test_backfill_missing_session_ids_from_transcripts_restores_latest_session() -> None:
    service, repo, _, log = _make_service()
    service.create_work(_new_work_request())
    service.add_agent_to_work(_agent_request(provider="amp", model="deep"))
    log.events[("WRK-001", "agt-1")] = [
        {"seq": 1, "type": "session_established", "session_id": "T-old"},
        {"seq": 2, "type": "session_established", "session_id": "T-old"},
        {"seq": 3, "type": "session_established", "session_id": "T-current"},
    ]

    repaired = service.backfill_missing_session_ids_from_transcripts()

    assert repaired == 1
    assert repo.agents["agt-1"].session_id == "T-current"
    assert repo.agents["agt-1"].parent_session_id == "T-old"


def test_backfill_missing_session_ids_leaves_existing_sql_session_alone() -> None:
    service, repo, _, log = _make_service()
    service.create_work(_new_work_request())
    service.add_agent_to_work(_agent_request(provider="amp", model="smart"))
    service.set_agent_session_id("agt-1", "T-db")
    log.events[("WRK-001", "agt-1")] = [
        {"seq": 1, "type": "session_established", "session_id": "T-transcript"},
    ]

    repaired = service.backfill_missing_session_ids_from_transcripts()

    assert repaired == 0
    assert repo.agents["agt-1"].session_id == "T-db"
    assert repo.agents["agt-1"].parent_session_id is None


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


def test_record_artifact_returns_existing_pr_for_same_work_and_url() -> None:
    service, repo, _, _ = _make_service()
    service.create_work(_new_work_request())
    service.add_agent_to_work(
        _agent_request(name="Dev", persona="developer", role="dev", model="x")
    )
    req = RecordArtifactRequest(
        work_slug="WRK-001",
        agent_slug="agt-1",
        type="pr",
        title="Fix bug",
        status="open",
        url="https://github.com/owner/repo/pull/1",
    )

    first = service.record_artifact(req)
    second = service.record_artifact(
        RecordArtifactRequest(
            work_slug="WRK-001",
            agent_slug="agt-1",
            type="pr",
            title="Fix bug again",
            status="draft",
            url=" https://github.com/owner/repo/pull/1 ",
        )
    )

    assert second is first
    assert len(repo.artifacts) == 1


def test_record_artifact_returns_existing_doc_for_same_work_and_path() -> None:
    service, repo, _, _ = _make_service()
    service.create_work(_new_work_request())
    service.add_agent_to_work(
        _agent_request(name="Dev", persona="developer", role="dev", model="x")
    )
    req = RecordArtifactRequest(
        work_slug="WRK-001",
        agent_slug="agt-1",
        type="doc",
        title="Plan",
        status="draft",
        doc_path="/tmp/plan.md",
    )

    first = service.record_artifact(req)
    second = service.record_artifact(
        RecordArtifactRequest(
            work_slug="WRK-001",
            agent_slug="agt-1",
            type="doc",
            title="Plan again",
            status="pending",
            doc_path=" /tmp/plan.md ",
        )
    )

    assert second is first
    assert len(repo.artifacts) == 1


def test_list_artifacts_for_work_hides_legacy_duplicate_rows() -> None:
    service, repo, _, _ = _make_service()
    service.create_work(_new_work_request())
    service.add_agent_to_work(
        _agent_request(name="Dev", persona="developer", role="dev", model="x")
    )
    first = service.record_artifact(
        RecordArtifactRequest(
            work_slug="WRK-001",
            agent_slug="agt-1",
            type="pr",
            title="Fix bug",
            status="open",
            url="https://github.com/owner/repo/pull/1",
        )
    )
    repo.add_artifact(
        make_artifact(
            type="pr",
            work_id=1,
            agent_id=1,
            title="Fix bug duplicate",
            status="open",
            created_at=FIXED_NOW,
            url="https://github.com/owner/repo/pull/1",
        )
    )

    artifacts = service.list_artifacts_for_work("WRK-001")

    assert artifacts == [first]
    assert len(repo.artifacts) == 2


def test_record_artifact_without_agent_has_null_agent_id() -> None:
    service, _, _, _ = _make_service()
    service.create_work(_new_work_request())
    artifact = service.record_artifact(
        RecordArtifactRequest(
            work_slug="WRK-001",
            type="doc",
            title="Notes",
            status="draft",
            doc_path="/tmp/notes.md",
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
                doc_path="/tmp/x.md",
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
                doc_path="/tmp/x.md",
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


# ---------------------------------------------------------------------------
# is_session_ingested
# ---------------------------------------------------------------------------


def test_is_session_ingested_true_for_session_established() -> None:
    service, _, _, log = _make_service()
    log.events[("WRK-001", "agt-1")] = [
        {"seq": 1, "type": "session_established", "session_id": "sess-A"},
    ]
    assert service.is_session_ingested("WRK-001", "agt-1", "sess-A") is True


def test_is_session_ingested_true_for_sdk_session_merged_marker() -> None:
    service, _, _, log = _make_service()
    log.events[("WRK-001", "agt-1")] = [
        {
            "seq": 1,
            "type": "sdk_session_merged",
            "session_id": "sess-B",
            "events_merged": 7,
        },
    ]
    assert service.is_session_ingested("WRK-001", "agt-1", "sess-B") is True


def test_is_session_ingested_false_when_session_id_doesnt_match() -> None:
    service, _, _, log = _make_service()
    log.events[("WRK-001", "agt-1")] = [
        {"seq": 1, "type": "session_established", "session_id": "sess-A"},
    ]
    assert service.is_session_ingested("WRK-001", "agt-1", "sess-other") is False


def test_is_session_ingested_false_for_other_event_types_carrying_session_id() -> None:
    service, _, _, log = _make_service()
    log.events[("WRK-001", "agt-1")] = [
        {"seq": 1, "type": "user_input", "session_id": "sess-A", "text": "hi"},
    ]
    assert service.is_session_ingested("WRK-001", "agt-1", "sess-A") is False


def test_is_session_ingested_false_for_empty_log() -> None:
    service, _, _, _ = _make_service()
    assert service.is_session_ingested("WRK-001", "agt-1", "sess-A") is False
