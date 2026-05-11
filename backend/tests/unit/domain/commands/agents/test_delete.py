"""Unit tests for ``agents/delete.execute``.

Asserts the orchestration contract:

- the supervisor is stopped before any FS/DB cleanup
- the agent's worktree is removed
- the workstore drops the agent dir + DB row
- a missing agent raises ``AgentNotFound``
- siblings on the same work are untouched
"""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime
from pathlib import Path

import pytest

from src.domain.commands.agents import delete
from src.domain.workstore import AddAgentRequest, CreateWorkRequest, WorkStoreService
from tests.unit.domain.workstore._stubs import (
    StubFiles,
    StubRepository,
    StubTranscriptLog,
)


class _StubSupervisor:
    def __init__(self) -> None:
        self.stopped: list[str] = []

    async def stop_agent(self, agent_slug: str) -> None:
        self.stopped.append(agent_slug)


class _StubWorktreeManager:
    def __init__(self) -> None:
        self.removed: list[tuple[str, str]] = []

    def ensure(
        self, work_slug: str, agent_slug: str, source: Path
    ) -> Path:  # pragma: no cover
        raise NotImplementedError

    def remove(self, work_slug: str, agent_slug: str) -> None:
        self.removed.append((work_slug, agent_slug))


def _make_workstore() -> tuple[WorkStoreService, StubFiles]:
    repo = StubRepository()
    files = StubFiles()
    log = StubTranscriptLog()
    return (
        WorkStoreService(
            repo, files, log, clock=lambda: datetime(2026, 5, 11, 12, 0, tzinfo=UTC)
        ),
        files,
    )


def _seed(workstore: WorkStoreService, *, names: list[str], folder: Path) -> tuple[str, list[str]]:
    record = workstore.create_work(
        CreateWorkRequest(name="W", description="d", contexts=[])
    )
    work_slug = record.work.slug
    assert work_slug is not None
    slugs: list[str] = []
    for name in names:
        agent = workstore.add_agent_to_work(
            AddAgentRequest(
                work_slug=work_slug,
                name=name,
                persona="developer",
                role="dev",
                provider="amp",
                model="rush",
                folder=folder,
                contexts=[],
            )
        )
        assert agent.slug is not None
        slugs.append(agent.slug)
    return work_slug, slugs


def test_delete_stops_supervisor_removes_worktree_and_wipes_data(tmp_path: Path) -> None:
    workstore, files = _make_workstore()
    supervisor = _StubSupervisor()
    worktrees = _StubWorktreeManager()
    work_slug, [target, sibling] = _seed(workstore, names=["a", "b"], folder=tmp_path)

    result = asyncio.run(
        delete.execute(
            workstore,
            supervisor,
            worktrees,
            delete.DeleteAgentRequest(agent_slug=target),
        )
    )

    assert result.agent_slug == target
    assert result.work_slug == work_slug
    assert supervisor.stopped == [target]
    assert worktrees.removed == [(work_slug, target)]
    # Sibling untouched.
    remaining = [a.slug for a in workstore.list_agents_for_work(work_slug)]
    assert remaining == [sibling]
    # FS state for the target is gone, sibling still has agent.json.
    assert (work_slug, target) not in files.agent_jsons
    assert (work_slug, sibling) in files.agent_jsons


def test_delete_raises_when_agent_missing() -> None:
    workstore, _ = _make_workstore()
    with pytest.raises(delete.AgentNotFound):
        asyncio.run(
            delete.execute(
                workstore,
                _StubSupervisor(),
                _StubWorktreeManager(),
                delete.DeleteAgentRequest(agent_slug="agt-999"),
            )
        )


def test_delete_is_idempotent_after_first_call(tmp_path: Path) -> None:
    workstore, _ = _make_workstore()
    supervisor = _StubSupervisor()
    worktrees = _StubWorktreeManager()
    _, [target] = _seed(workstore, names=["a"], folder=tmp_path)

    asyncio.run(
        delete.execute(
            workstore,
            supervisor,
            worktrees,
            delete.DeleteAgentRequest(agent_slug=target),
        )
    )

    # Second call: row is gone, so the command should raise.
    with pytest.raises(delete.AgentNotFound):
        asyncio.run(
            delete.execute(
                workstore,
                supervisor,
                worktrees,
                delete.DeleteAgentRequest(agent_slug=target),
            )
        )
