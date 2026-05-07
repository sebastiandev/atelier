"""Unit tests for ``works/complete.execute``.

The command orchestrates supervisor + worktree-manager + workstore so the
test wires stub callables and asserts on the dispatch contract:

- every agent gets ``supervisor.stop_agent(slug)``
- every agent gets ``worktree_manager.remove(work_slug, agent_slug)``
- the work's status flips to ``completed``
- precondition errors map to ``WorkNotFound`` / ``WorkNotActive``
"""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime
from pathlib import Path

import pytest

from src.domain.commands.works import complete
from src.domain.workstore import (
    AddAgentRequest,
    CreateWorkRequest,
    WorkStoreService,
)
from tests.unit.domain.workstore._stubs import (
    StubFiles,
    StubRepository,
    StubTranscriptLog,
)


class _StubSupervisor:
    """Async stub — records each ``stop_agent`` call's slug."""

    def __init__(self) -> None:
        self.stopped: list[str] = []

    async def stop_agent(self, agent_slug: str) -> None:
        self.stopped.append(agent_slug)


class _StubWorktreeManager:
    """Sync stub — records each ``remove`` call as ``(work_slug, agent_slug)``."""

    def __init__(self) -> None:
        self.removed: list[tuple[str, str]] = []

    def ensure(
        self, work_slug: str, agent_slug: str, source: Path
    ) -> Path:  # pragma: no cover - not exercised here
        raise NotImplementedError

    def remove(self, work_slug: str, agent_slug: str) -> None:
        self.removed.append((work_slug, agent_slug))


def _make_workstore() -> WorkStoreService:
    repo = StubRepository()
    files = StubFiles()
    log = StubTranscriptLog()
    return WorkStoreService(
        repo, files, log, clock=lambda: datetime(2026, 5, 7, 12, 0, tzinfo=UTC)
    )


def _seed_work_with_agents(
    workstore: WorkStoreService, agent_count: int, *, folder: Path
) -> str:
    record = workstore.create_work(
        CreateWorkRequest(name="W", description="d", contexts=[])
    )
    work_slug = record.work.slug
    assert work_slug is not None
    for i in range(agent_count):
        workstore.add_agent_to_work(
            AddAgentRequest(
                work_slug=work_slug,
                name=f"agent-{i}",
                persona="developer",
                role="dev",
                provider="amp",
                model="rush",
                folder=folder,
                contexts=[],
            )
        )
    return work_slug


def test_complete_stops_agents_removes_worktrees_and_flips_status(
    tmp_path: Path,
) -> None:
    workstore = _make_workstore()
    supervisor = _StubSupervisor()
    worktrees = _StubWorktreeManager()
    work_slug = _seed_work_with_agents(workstore, agent_count=2, folder=tmp_path)

    result = asyncio.run(
        complete.execute(
            workstore,
            supervisor,
            worktrees,
            complete.CompleteWorkRequest(work_slug=work_slug),
        )
    )

    expected_agents = sorted(
        a.slug for a in workstore.list_agents_for_work(work_slug) if a.slug is not None
    )
    assert sorted(supervisor.stopped) == expected_agents
    assert sorted(worktrees.removed) == sorted(
        (work_slug, slug) for slug in expected_agents
    )

    record = workstore.get_work(work_slug)
    assert record is not None
    assert record.work.status == "completed"
    assert result.work_slug == work_slug
    assert result.agent_count == 2


def test_complete_handles_work_with_no_agents(tmp_path: Path) -> None:
    workstore = _make_workstore()
    supervisor = _StubSupervisor()
    worktrees = _StubWorktreeManager()
    work_slug = _seed_work_with_agents(workstore, agent_count=0, folder=tmp_path)

    result = asyncio.run(
        complete.execute(
            workstore,
            supervisor,
            worktrees,
            complete.CompleteWorkRequest(work_slug=work_slug),
        )
    )

    assert supervisor.stopped == []
    assert worktrees.removed == []
    assert result.agent_count == 0

    record = workstore.get_work(work_slug)
    assert record is not None
    assert record.work.status == "completed"


def test_complete_raises_when_work_missing() -> None:
    workstore = _make_workstore()
    with pytest.raises(complete.WorkNotFound):
        asyncio.run(
            complete.execute(
                workstore,
                _StubSupervisor(),
                _StubWorktreeManager(),
                complete.CompleteWorkRequest(work_slug="WRK-999"),
            )
        )


def test_complete_raises_when_work_already_completed(tmp_path: Path) -> None:
    workstore = _make_workstore()
    work_slug = _seed_work_with_agents(workstore, agent_count=0, folder=tmp_path)

    asyncio.run(
        complete.execute(
            workstore,
            _StubSupervisor(),
            _StubWorktreeManager(),
            complete.CompleteWorkRequest(work_slug=work_slug),
        )
    )

    with pytest.raises(complete.WorkNotActive):
        asyncio.run(
            complete.execute(
                workstore,
                _StubSupervisor(),
                _StubWorktreeManager(),
                complete.CompleteWorkRequest(work_slug=work_slug),
            )
        )
