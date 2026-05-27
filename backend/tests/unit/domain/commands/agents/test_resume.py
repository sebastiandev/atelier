from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path
from typing import Any
from unittest.mock import patch

import pytest

from src.domain.commands.agents import resume
from src.domain.models import AgentStatus
from src.domain.workstore import AddAgentRequest, CreateWorkRequest, WorkStoreService
from src.settings import Settings
from tests.unit.domain.workstore._stubs import (
    StubFiles,
    StubRepository,
    StubTranscriptLog,
)


@pytest.fixture
def anyio_backend() -> str:
    return "asyncio"


class FakeAdapter:
    def __init__(self) -> None:
        self.closed = False

    async def close(self) -> None:
        self.closed = True


class RacingSupervisor:
    def __init__(self) -> None:
        self.registered = False
        self.refreshes: list[str] = []

    def is_registered(self, _agent_slug: str) -> bool:
        return self.registered

    async def register_agent(
        self,
        _work_slug: str,
        _agent_slug: str,
        _adapter: Any,
        _context: Any,
        *,
        lazy: bool = False,
    ) -> None:
        assert lazy is True
        self.registered = True
        raise RuntimeError("agent already registered: agt-1")

    async def refresh_seq_from_disk(self, agent_slug: str) -> None:
        self.refreshes.append(agent_slug)


class FakeWorktreeManager:
    def __init__(self, workdir: Path) -> None:
        self.workdir = workdir

    def ensure(self, **_: Any) -> Path:
        return self.workdir

    def is_detached(self, _workdir: Path) -> bool:
        return True

    def sandbox_writable_roots(self, _workdir: Path) -> tuple[Path, ...]:
        return ()


class FakeShareStore:
    def list_for_project(self, _project_slug: str) -> list[Any]:
        return []


class FakeShareProvisioner:
    pass


def _workstore() -> tuple[WorkStoreService, StubTranscriptLog]:
    repo = StubRepository()
    files = StubFiles()
    transcript = StubTranscriptLog()
    return (
        WorkStoreService(
            repo,  # type: ignore[arg-type]
            files,
            transcript,
            clock=lambda: datetime(2026, 5, 27, 14, 0, tzinfo=UTC),
        ),
        transcript,
    )


@pytest.mark.anyio
async def test_resume_registration_race_refreshes_winner_seq(tmp_path: Path) -> None:
    workstore, transcript = _workstore()
    work = workstore.create_work(CreateWorkRequest(name="Work", description="Do it"))
    assert work.work.slug is not None
    agent = workstore.add_agent_to_work(
        AddAgentRequest(
            work_slug=work.work.slug,
            name="Developer",
            persona="developer",
            role="dev",
            provider="amp",
            model="smart",
            folder=tmp_path,
        )
    )
    assert agent.slug is not None
    workstore.set_agent_session_id(agent.slug, "T-current")
    workstore.set_agent_status(agent.slug, AgentStatus.DETACHED)
    workstore.append_transcript_event_with_seq(
        work.work.slug,
        agent.slug,
        {
            "type": "user_detached",
            "ts": "2026-05-27T14:00:00+00:00",
            "sdk_cursor": {"provider": "amp", "message_count": 5},
        },
    )

    supervisor = RacingSupervisor()
    adapter = FakeAdapter()
    with (
        patch("src.domain.commands.agents.resume.build_adapter", return_value=adapter),
        patch(
            "src.domain.commands.agents.resume.merge_cli_transcript",
            return_value=[],
        ),
        patch(
            "src.domain.commands.agents.resume.sdk_cursor_at_detach",
            return_value={"provider": "amp", "message_count": 5},
        ),
    ):
        await resume.execute(
            workstore,
            supervisor,  # type: ignore[arg-type]
            FakeWorktreeManager(tmp_path),  # type: ignore[arg-type]
            FakeShareStore(),  # type: ignore[arg-type]
            FakeShareProvisioner(),  # type: ignore[arg-type]
            Settings(workspace_root=tmp_path / "ws"),
            resume.ResumeAgentRequest(work_slug=work.work.slug, agent_slug=agent.slug),
        )

    events = transcript.events[(work.work.slug, agent.slug)]
    assert [event["seq"] for event in events] == [1, 2]
    assert events[-1]["type"] == "user_reattached"
    assert supervisor.refreshes == [agent.slug]
    assert adapter.closed is True
