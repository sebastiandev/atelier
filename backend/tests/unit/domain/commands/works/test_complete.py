"""Unit tests for ``works/complete.execute``.

The command stops runtimes and archives by default. Workspace removal is an
explicit clean-only option.
"""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime
from pathlib import Path
from types import SimpleNamespace

import pytest

from src.domain.commands.works import complete
from src.domain.loop.dtos import LoopStatus
from src.domain.workstore import (
    AddAgentRequest,
    CreateWorkRequest,
    WorkStoreService,
)
from src.domain.worktrees import WorktreeState
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
    """Sync stub for completion workspace inspection and removal."""

    def __init__(self, states: tuple[WorktreeState, ...] = ()) -> None:
        self.states = states
        self.removed: list[tuple[str, str, bool]] = []

    def ensure(
        self, work_slug: str, agent_slug: str, source: Path
    ) -> Path:  # pragma: no cover - not exercised here
        raise NotImplementedError

    def list_states(self, work_slug: str) -> tuple[WorktreeState, ...]:
        return self.states

    def remove(
        self, work_slug: str, agent_slug: str, *, force: bool = True
    ) -> None:
        self.removed.append((work_slug, agent_slug, force))


class _StubLoopRuns:
    """Return configured run statuses through the repository read contract."""

    def __init__(self, statuses: tuple[LoopStatus, ...] = ()) -> None:
        self._runs = tuple(type("Run", (), {"status": status})() for status in statuses)

    def list_for_work(self, work_slug: str) -> tuple[object, ...]:
        return self._runs


class _StubChatStore:
    """Return optional Work-linked chat records for runtime release."""

    def __init__(self, work_slug: str | None = None) -> None:
        self._records = (
            [
                SimpleNamespace(
                    chat=SimpleNamespace(
                        slug="CHT-001",
                        grounding_kind="work",
                        grounding_ref=work_slug,
                        promoted_to_work_slug=None,
                    )
                )
            ]
            if work_slug is not None
            else []
        )

    def list_chats(self) -> list[object]:
        return self._records


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


def test_complete_stops_agents_preserves_worktrees_and_flips_status(
    tmp_path: Path,
) -> None:
    workstore = _make_workstore()
    supervisor = _StubSupervisor()
    chat_supervisor = _StubSupervisor()
    worktrees = _StubWorktreeManager()
    work_slug = _seed_work_with_agents(workstore, agent_count=2, folder=tmp_path)

    result = asyncio.run(
        complete.execute(
            workstore,
            _StubLoopRuns(),
            supervisor,
            _StubChatStore(work_slug),
            chat_supervisor,
            worktrees,
            complete.CompleteWorkRequest(work_slug=work_slug),
        )
    )

    expected_agents = sorted(
        a.slug for a in workstore.list_agents_for_work(work_slug) if a.slug is not None
    )
    assert sorted(supervisor.stopped) == expected_agents
    assert chat_supervisor.stopped == ["CHT-001"]
    assert worktrees.removed == []

    record = workstore.get_work(work_slug)
    assert record is not None
    assert record.work.status == "completed"
    assert result.work_slug == work_slug
    assert result.agent_count == 2
    assert result.workspaces_removed == 0


def test_complete_handles_work_with_no_agents(tmp_path: Path) -> None:
    workstore = _make_workstore()
    supervisor = _StubSupervisor()
    worktrees = _StubWorktreeManager()
    work_slug = _seed_work_with_agents(workstore, agent_count=0, folder=tmp_path)

    result = asyncio.run(
        complete.execute(
            workstore,
            _StubLoopRuns(),
            supervisor,
            _StubChatStore(),
            _StubSupervisor(),
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
                _StubLoopRuns(),
                _StubSupervisor(),
                _StubChatStore(),
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
            _StubLoopRuns(),
            _StubSupervisor(),
            _StubChatStore(),
            _StubSupervisor(),
            _StubWorktreeManager(),
            complete.CompleteWorkRequest(work_slug=work_slug),
        )
    )

    with pytest.raises(complete.WorkNotActive):
        asyncio.run(
            complete.execute(
                workstore,
                _StubLoopRuns(),
                _StubSupervisor(),
                _StubChatStore(),
                _StubSupervisor(),
                _StubWorktreeManager(),
                complete.CompleteWorkRequest(work_slug=work_slug),
            )
        )


def test_complete_explicitly_removes_clean_shared_workspace(tmp_path: Path) -> None:
    workstore = _make_workstore()
    supervisor = _StubSupervisor()
    workspace = tmp_path / "worktrees" / "loop"
    worktrees = _StubWorktreeManager(
        (WorktreeState(workdir=workspace, is_git_repo=True, head="abc123"),)
    )
    work_slug = _seed_work_with_agents(workstore, agent_count=1, folder=tmp_path)

    result = asyncio.run(
        complete.execute(
            workstore,
            _StubLoopRuns(),
            supervisor,
            _StubChatStore(),
            _StubSupervisor(),
            worktrees,
            complete.CompleteWorkRequest(
                work_slug=work_slug, remove_workspaces=True
            ),
        )
    )

    assert worktrees.removed == [(work_slug, "loop", False)]
    assert result.workspace_count == 1
    assert result.workspaces_removed == 1


def test_complete_rejects_dirty_workspace_before_side_effects(tmp_path: Path) -> None:
    workstore = _make_workstore()
    supervisor = _StubSupervisor()
    workspace = tmp_path / "worktrees" / "loop"
    worktrees = _StubWorktreeManager(
        (
            WorktreeState(
                workdir=workspace,
                is_git_repo=True,
                status=" M src/app.py",
                changed_files=("src/app.py",),
            ),
        )
    )
    work_slug = _seed_work_with_agents(workstore, agent_count=1, folder=tmp_path)

    with pytest.raises(complete.WorkspaceNotClean):
        asyncio.run(
            complete.execute(
                workstore,
                _StubLoopRuns(),
                supervisor,
                _StubChatStore(),
                _StubSupervisor(),
                worktrees,
                complete.CompleteWorkRequest(
                    work_slug=work_slug, remove_workspaces=True
                ),
            )
        )

    assert supervisor.stopped == []
    assert worktrees.removed == []
    record = workstore.get_work(work_slug)
    assert record is not None and record.work.status == "active"
