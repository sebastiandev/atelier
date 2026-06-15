from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import pytest

from src.domain.agents import AgentStartContext
from src.domain.agents.compactions import (
    BreadcrumbResult,
    CompactionSessionStartResult,
)
from src.domain.commands.agents import compact, read_compaction_summary
from src.domain.models import AgentStatus
from src.domain.workstore.dtos import AddAgentRequest, CreateWorkRequest
from src.domain.workstore.service import WorkStoreService
from src.domain.worktrees import WorktreeState
from src.settings import Settings
from tests.unit.domain.workstore._stubs import (
    StubFiles,
    StubRepository,
    StubTranscriptLog,
)


@pytest.fixture
def anyio_backend() -> str:
    return "asyncio"


class FakeSupervisor:
    def __init__(self) -> None:
        self.stopped: list[str] = []

    async def stop_agent(self, agent_slug: str) -> None:
        self.stopped.append(agent_slug)

    def is_registered(self, agent_slug: str) -> bool:
        return True


class FakeWorktreeManager:
    def __init__(self, workdir: Path) -> None:
        self.workdir = workdir

    def ensure(self, **_: Any) -> Path:
        return self.workdir

    def is_detached(self, _workdir: Path) -> bool:
        return True

    def sandbox_writable_roots(self, _workdir: Path) -> tuple[Path, ...]:
        return ()

    def describe_state(self, workdir: Path) -> WorktreeState:
        assert workdir == self.workdir
        return WorktreeState(
            workdir=workdir,
            is_git_repo=True,
            branch="feature/compact",
            head="abc1234",
            status=" M src/app.py\n?? notes.md",
            changed_files=("src/app.py",),
            untracked_files=("notes.md",),
        )


class FakeShareStore:
    def list_for_project(self, _project_slug: str) -> list[Any]:
        return []


class FakeShareProvisioner:
    pass


class FakeSessionClient:
    def __init__(
        self,
        *,
        summary: str | None = (
            "## Goal\n"
            "provider generated summary\n\n"
            "## Decisions\n"
            "Keep going.\n\n"
            "## Key files\n"
            "No key files.\n\n"
            "## Blockers\n"
            "No blockers."
        ),
    ) -> None:
        self.summary = summary
        self.summary_prompt: str | None = None
        self.seed_message: str | None = None
        self.breadcrumb: str | None = None

    async def summarize_transcript(
        self,
        *,
        config: Any,
        context: AgentStartContext,
        prompt: str,
    ) -> str:
        assert context.session_id is None
        self.summary_prompt = prompt
        if self.summary is None:
            raise RuntimeError("summary failed")
        return self.summary

    async def start_fresh_session(
        self,
        *,
        config: Any,
        context: AgentStartContext,
        seed_message: str,
    ) -> CompactionSessionStartResult:
        assert context.session_id is None
        self.seed_message = seed_message
        return CompactionSessionStartResult(session_id="new-session")

    async def write_breadcrumb(
        self,
        *,
        config: Any,
        context: AgentStartContext,
        old_session_id: str,
        breadcrumb: str,
    ) -> BreadcrumbResult:
        assert old_session_id == "old-session"
        self.breadcrumb = breadcrumb
        return BreadcrumbResult(written=True)


def _workstore() -> tuple[WorkStoreService, StubRepository, StubFiles, StubTranscriptLog]:
    repo = StubRepository()
    files = StubFiles()
    transcript = StubTranscriptLog()
    return WorkStoreService(repo, files, transcript), repo, files, transcript


def _seed_agent(
    service: WorkStoreService, repo: StubRepository, folder: Path
) -> None:
    service.create_work(CreateWorkRequest(name="Ship compaction", description="Do it"))
    agent = service.add_agent_to_work(
        AddAgentRequest(
            work_slug="WRK-001",
            name="Builder",
            persona="developer",
            role="Implement compaction",
            provider="claude-code",
            model="claude-sonnet-4-6",
            folder=folder,
        )
    )
    assert agent.slug == "agt-1"
    service.set_agent_session_id("agt-1", "old-session")
    service.append_transcript_event_with_seq(
        "WRK-001",
        "agt-1",
        {
            "type": "status_change",
            "ts": "2026-05-26T11:00:00+00:00",
            "status": "idle",
        },
    )
    service.append_transcript_event_with_seq(
        "WRK-001",
        "agt-1",
        {
            "type": "user_input",
            "ts": "2026-05-26T11:00:01+00:00",
            "text": "latest instruction",
        },
    )
    assert repo.agents["agt-1"].session_id == "old-session"


@pytest.mark.anyio
async def test_compact_replaces_session_and_records_boundary(tmp_path: Path) -> None:
    service, repo, files, transcript = _workstore()
    _seed_agent(service, repo, tmp_path)
    session_client = FakeSessionClient()
    supervisor = FakeSupervisor()

    result = await compact.execute(
        service,
        supervisor,  # type: ignore[arg-type]
        FakeWorktreeManager(tmp_path),  # type: ignore[arg-type]
        FakeShareStore(),  # type: ignore[arg-type]
        FakeShareProvisioner(),  # type: ignore[arg-type]
        Settings(workspace_root=tmp_path),
        lambda events, context: (
            f"summary for {context.work_name}: "
            f"{events[-1]['text'] if events and 'text' in events[-1] else 'none'}"
        ),
        session_client,  # type: ignore[arg-type]
        compact.CompactAgentRequest(agent_slug="agt-1"),
        clock=lambda: datetime(2026, 5, 26, 12, 0, 0, tzinfo=UTC),
    )

    assert result.old_session_id == "old-session"
    assert result.new_session_id == "new-session"
    assert result.breadcrumb_written is True
    assert result.summary_path.endswith("/compactions/20260526-120000.md")
    assert supervisor.stopped == ["agt-1", "agt-1"]
    assert repo.agents["agt-1"].session_id == "new-session"
    assert repo.agents["agt-1"].parent_session_id == "old-session"
    assert repo.agents["agt-1"].status == AgentStatus.IDLE
    persisted_agent = files.agent_jsons[("WRK-001", "agt-1")]
    assert persisted_agent["session_id"] == "new-session"
    assert persisted_agent["parent_session_id"] == "old-session"

    summary = files.compaction_docs[
        ("WRK-001", "agt-1", "20260526-120000.md")
    ]
    assert "provider generated summary" in summary
    assert "feature/compact @ abc1234" in summary
    assert session_client.summary_prompt is not None
    assert "[user] latest instruction" in session_client.summary_prompt
    assert session_client.seed_message is not None
    assert "<COMPACTED_CONTEXT>" in session_client.seed_message
    assert session_client.breadcrumb is not None
    assert "new-session" in session_client.breadcrumb

    event_types = [
        event["type"] for event in transcript.events[("WRK-001", "agt-1")]
    ]
    assert event_types[-7:] == [
        "compaction_requested",
        "compaction_progress",
        "compaction_summary_created",
        "compaction_progress",
        "compaction_progress",
        "compaction_old_session_breadcrumb",
        "context_compacted",
    ]
    progress_phases = [
        event["phase"]
        for event in transcript.events[("WRK-001", "agt-1")]
        if event.get("type") == "compaction_progress"
    ]
    assert progress_phases == [
        "summarizing",
        "starting_session",
        "linking_session",
    ]


@pytest.mark.anyio
async def test_compact_removes_legacy_open_questions_placeholder(
    tmp_path: Path,
) -> None:
    service, repo, files, _transcript = _workstore()
    _seed_agent(service, repo, tmp_path)

    await compact.execute(
        service,
        FakeSupervisor(),  # type: ignore[arg-type]
        FakeWorktreeManager(tmp_path),  # type: ignore[arg-type]
        FakeShareStore(),  # type: ignore[arg-type]
        FakeShareProvisioner(),  # type: ignore[arg-type]
        Settings(workspace_root=tmp_path),
        lambda _events, _context: (
            "## Goal\nKeep going.\n\n"
            "## Open questions\n"
            "_The structural summarizer can't infer open questions. "
            "Edit this section before the new agent reads it._\n\n"
            "## Blockers\nNone."
        ),
        FakeSessionClient(summary=None),  # type: ignore[arg-type]
        compact.CompactAgentRequest(agent_slug="agt-1"),
        clock=lambda: datetime(2026, 5, 26, 12, 0, 0, tzinfo=UTC),
    )

    summary = files.compaction_docs[
        ("WRK-001", "agt-1", "20260526-120000.md")
    ]
    assert "Edit this section before the new agent reads it" not in summary
    assert "## Goal\nKeep going." in summary
    assert "## Blockers\nNone." in summary


@pytest.mark.anyio
async def test_compact_falls_back_when_provider_summary_fails(
    tmp_path: Path,
) -> None:
    service, repo, files, _transcript = _workstore()
    _seed_agent(service, repo, tmp_path)

    await compact.execute(
        service,
        FakeSupervisor(),  # type: ignore[arg-type]
        FakeWorktreeManager(tmp_path),  # type: ignore[arg-type]
        FakeShareStore(),  # type: ignore[arg-type]
        FakeShareProvisioner(),  # type: ignore[arg-type]
        Settings(workspace_root=tmp_path),
        lambda events, context: (
            f"fallback summary for {context.work_name}: {events[-1]['text']}"
        ),
        FakeSessionClient(summary=None),  # type: ignore[arg-type]
        compact.CompactAgentRequest(agent_slug="agt-1"),
        clock=lambda: datetime(2026, 5, 26, 12, 0, 0, tzinfo=UTC),
    )

    summary = files.compaction_docs[
        ("WRK-001", "agt-1", "20260526-120000.md")
    ]
    assert "fallback summary for Ship compaction: latest instruction" in summary


@pytest.mark.anyio
async def test_compact_preserves_unstructured_provider_summary(
    tmp_path: Path,
) -> None:
    service, repo, files, _transcript = _workstore()
    _seed_agent(service, repo, tmp_path)

    await compact.execute(
        service,
        FakeSupervisor(),  # type: ignore[arg-type]
        FakeWorktreeManager(tmp_path),  # type: ignore[arg-type]
        FakeShareStore(),  # type: ignore[arg-type]
        FakeShareProvisioner(),  # type: ignore[arg-type]
        Settings(workspace_root=tmp_path),
        lambda events, context: (
            f"fallback summary for {context.work_name}: {events[-1]['text']}"
        ),
        FakeSessionClient(summary="I'm caught up; tell me what to do next."),  # type: ignore[arg-type]
        compact.CompactAgentRequest(agent_slug="agt-1"),
        clock=lambda: datetime(2026, 5, 26, 12, 0, 0, tzinfo=UTC),
    )

    summary = files.compaction_docs[
        ("WRK-001", "agt-1", "20260526-120000.md")
    ]
    assert "I'm caught up; tell me what to do next." in summary
    assert "fallback summary for Ship compaction: latest instruction" not in summary


@pytest.mark.anyio
async def test_compact_falls_back_when_provider_summary_is_empty(
    tmp_path: Path,
) -> None:
    service, repo, files, _transcript = _workstore()
    _seed_agent(service, repo, tmp_path)

    await compact.execute(
        service,
        FakeSupervisor(),  # type: ignore[arg-type]
        FakeWorktreeManager(tmp_path),  # type: ignore[arg-type]
        FakeShareStore(),  # type: ignore[arg-type]
        FakeShareProvisioner(),  # type: ignore[arg-type]
        Settings(workspace_root=tmp_path),
        lambda events, context: (
            f"fallback summary for {context.work_name}: {events[-1]['text']}"
        ),
        FakeSessionClient(summary="   \n"),  # type: ignore[arg-type]
        compact.CompactAgentRequest(agent_slug="agt-1"),
        clock=lambda: datetime(2026, 5, 26, 12, 0, 0, tzinfo=UTC),
    )

    summary = files.compaction_docs[
        ("WRK-001", "agt-1", "20260526-120000.md")
    ]
    assert "fallback summary for Ship compaction: latest instruction" in summary


@pytest.mark.anyio
async def test_compact_after_compaction_summarizes_previous_summary_plus_delta(
    tmp_path: Path,
) -> None:
    service, repo, files, _transcript = _workstore()
    _seed_agent(service, repo, tmp_path)
    files.compaction_docs[
        ("WRK-001", "agt-1", "20260526-100000.md")
    ] = "# Prior summary\n\nImportant old decision."
    service.append_transcript_event_with_seq(
        "WRK-001",
        "agt-1",
        {
            "type": "context_compacted",
            "ts": "2026-05-26T10:00:00+00:00",
            "old_session_id": "older-session",
            "new_session_id": "old-session",
            "summary_path": (
                "/stub/works/WRK-001/agents/agt-1/"
                "compactions/20260526-100000.md"
            ),
            "reason": "manual",
            "provider": "claude-code",
        },
    )
    service.append_transcript_event_with_seq(
        "WRK-001",
        "agt-1",
        {
            "type": "user_input",
            "ts": "2026-05-26T11:30:00+00:00",
            "text": "new post-compaction instruction",
        },
    )
    session_client = FakeSessionClient()

    await compact.execute(
        service,
        FakeSupervisor(),  # type: ignore[arg-type]
        FakeWorktreeManager(tmp_path),  # type: ignore[arg-type]
        FakeShareStore(),  # type: ignore[arg-type]
        FakeShareProvisioner(),  # type: ignore[arg-type]
        Settings(workspace_root=tmp_path),
        lambda _events, _context: "fallback summary",
        session_client,  # type: ignore[arg-type]
        compact.CompactAgentRequest(agent_slug="agt-1"),
        clock=lambda: datetime(2026, 5, 26, 12, 0, 0, tzinfo=UTC),
    )

    assert session_client.summary_prompt is not None
    assert "[previous_compaction_summary]" in session_client.summary_prompt
    assert "Important old decision." in session_client.summary_prompt
    assert "new post-compaction instruction" in session_client.summary_prompt
    assert "latest instruction" not in session_client.summary_prompt


@pytest.mark.anyio
async def test_compact_after_compaction_uses_summary_body_without_nested_context(
    tmp_path: Path,
) -> None:
    service, repo, files, _transcript = _workstore()
    _seed_agent(service, repo, tmp_path)
    files.compaction_docs[
        ("WRK-001", "agt-1", "20260526-100000.md")
    ] = (
        "# Compacted Context for Builder\n\n"
        "Created: 2026-05-26T10:00:00+00:00\n\n"
        "## Work Description\n"
        "Do it\n\n"
        "## Compacted Summary\n"
        "# Handoff from Builder\n\n"
        "## Goal\n"
        "Keep building.\n\n"
        "## Decisions\n"
        "Previous compacted context:\n"
        "# Compacted Context for Builder Created: old nested wrapper...\n\n"
        "Latest user instructions to the source agent:\n"
        "- Rename the Branch DTOs.\n\n"
        "## Key files\n"
        "- `src/app.py`\n\n"
        "## Blockers\n"
        "None.\n\n"
        "## Repository State\n"
        "Status: clean\n"
    )
    service.append_transcript_event_with_seq(
        "WRK-001",
        "agt-1",
        {
            "type": "context_compacted",
            "ts": "2026-05-26T10:00:00+00:00",
            "old_session_id": "older-session",
            "new_session_id": "old-session",
            "summary_path": (
                "/stub/works/WRK-001/agents/agt-1/"
                "compactions/20260526-100000.md"
            ),
            "reason": "manual",
            "provider": "claude-code",
        },
    )
    service.append_transcript_event_with_seq(
        "WRK-001",
        "agt-1",
        {
            "type": "user_input",
            "ts": "2026-05-26T11:30:00+00:00",
            "text": "continue after compact",
        },
    )
    session_client = FakeSessionClient()

    await compact.execute(
        service,
        FakeSupervisor(),  # type: ignore[arg-type]
        FakeWorktreeManager(tmp_path),  # type: ignore[arg-type]
        FakeShareStore(),  # type: ignore[arg-type]
        FakeShareProvisioner(),  # type: ignore[arg-type]
        Settings(workspace_root=tmp_path),
        lambda _events, _context: "fallback summary",
        session_client,  # type: ignore[arg-type]
        compact.CompactAgentRequest(agent_slug="agt-1"),
        clock=lambda: datetime(2026, 5, 26, 12, 0, 0, tzinfo=UTC),
    )

    assert session_client.summary_prompt is not None
    assert "Keep building." in session_client.summary_prompt
    assert "Rename the Branch DTOs." in session_client.summary_prompt
    assert "Repository State" not in session_client.summary_prompt
    assert "Created: 2026-05-26" not in session_client.summary_prompt
    assert "old nested wrapper" not in session_client.summary_prompt


@pytest.mark.anyio
async def test_compact_rejects_busy_agent(tmp_path: Path) -> None:
    service, repo, _files, _transcript = _workstore()
    _seed_agent(service, repo, tmp_path)
    service.append_transcript_event_with_seq(
        "WRK-001",
        "agt-1",
        {
            "type": "status_change",
            "ts": "2026-05-26T11:10:00+00:00",
            "status": "thinking",
        },
    )

    with pytest.raises(compact.AgentBusy):
        await compact.execute(
            service,
            FakeSupervisor(),  # type: ignore[arg-type]
            FakeWorktreeManager(tmp_path),  # type: ignore[arg-type]
            FakeShareStore(),  # type: ignore[arg-type]
            FakeShareProvisioner(),  # type: ignore[arg-type]
            Settings(workspace_root=tmp_path),
            lambda _events, _context: "summary",
            FakeSessionClient(),  # type: ignore[arg-type]
            compact.CompactAgentRequest(agent_slug="agt-1"),
        )


def test_read_compaction_summary_returns_saved_doc(tmp_path: Path) -> None:
    service, repo, files, _transcript = _workstore()
    _seed_agent(service, repo, tmp_path)
    files.compaction_docs[
        ("WRK-001", "agt-1", "20260526-120000.md")
    ] = "summary body"

    result = read_compaction_summary.execute(
        service,
        read_compaction_summary.ReadCompactionSummaryRequest(
            agent_slug="agt-1",
            filename="20260526-120000.md",
        ),
    )

    assert result.agent_slug == "agt-1"
    assert result.work_slug == "WRK-001"
    assert result.summary_path.endswith("/compactions/20260526-120000.md")
    assert result.content == "summary body"


def test_read_compaction_summary_rejects_missing_doc(tmp_path: Path) -> None:
    service, repo, _files, _transcript = _workstore()
    _seed_agent(service, repo, tmp_path)

    with pytest.raises(read_compaction_summary.CompactionSummaryNotFound):
        read_compaction_summary.execute(
            service,
            read_compaction_summary.ReadCompactionSummaryRequest(
                agent_slug="agt-1",
                filename="missing.md",
            ),
        )
