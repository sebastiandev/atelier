"""Unit tests for permanent work deletion orchestration."""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import pytest

from src.domain.chatstore import ChatRecord
from src.domain.commands.works import delete
from src.domain.models import Chat
from src.domain.workstore import AddAgentRequest, CreateWorkRequest, WorkStoreService
from tests.unit.domain.workstore._stubs import (
    StubFiles,
    StubRepository,
    StubTranscriptLog,
)

NOW = datetime(2026, 5, 7, 12, 0, tzinfo=UTC)


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
    ) -> Path:  # pragma: no cover - not exercised here
        raise NotImplementedError

    def remove(self, work_slug: str, agent_slug: str) -> None:
        self.removed.append((work_slug, agent_slug))


class _StubChatStore:
    def __init__(self, records: list[ChatRecord]) -> None:
        self.records = {r.chat.slug: r for r in records if r.chat.slug is not None}
        self.deleted: list[str] = []

    def list_chats(self) -> list[ChatRecord]:
        return list(self.records.values())

    def delete_chat(self, chat_slug: str) -> None:
        self.deleted.append(chat_slug)
        self.records.pop(chat_slug, None)

    def __getattr__(self, name: str) -> Any:
        raise NotImplementedError(name)


def _make_workstore() -> WorkStoreService:
    return WorkStoreService(
        StubRepository(),
        StubFiles(),
        StubTranscriptLog(),
        clock=lambda: NOW,
    )


def _seed_work(workstore: WorkStoreService, *, agent_count: int) -> str:
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
                folder=Path("/repo"),
                contexts=[],
            )
        )
    return work_slug


def _chat(
    slug: str,
    *,
    grounding_ref: str | None = None,
    promoted_to_work_slug: str | None = None,
) -> ChatRecord:
    return ChatRecord(
        chat=Chat(
            slug=slug,
            title=slug,
            provider="amp",
            model="rush",
            grounding_kind="work" if grounding_ref is not None else None,
            grounding_ref=grounding_ref,
            created_at=NOW,
            updated_at=NOW,
            promoted_to_work_slug=promoted_to_work_slug,
        ),
        transcript=[],
    )


def test_delete_stops_runtime_removes_worktrees_chats_and_work() -> None:
    workstore = _make_workstore()
    work_slug = _seed_work(workstore, agent_count=2)
    chatstore = _StubChatStore(
        [
            _chat("CHT-001", grounding_ref=work_slug),
            _chat("CHT-002", promoted_to_work_slug=work_slug),
            _chat("CHT-003", grounding_ref="WRK-other"),
        ]
    )
    supervisor = _StubSupervisor()
    chat_supervisor = _StubSupervisor()
    worktrees = _StubWorktreeManager()

    result = asyncio.run(
        delete.execute(
            workstore,
            chatstore,
            supervisor,
            chat_supervisor,
            worktrees,
            delete.DeleteWorkRequest(work_slug=work_slug),
        )
    )

    assert workstore.get_work(work_slug) is None
    assert result.work_slug == work_slug
    assert result.agent_count == 2
    assert result.chat_count == 2
    assert sorted(supervisor.stopped) == ["agt-1", "agt-2"]
    assert sorted(chat_supervisor.stopped) == ["CHT-001", "CHT-002"]
    assert sorted(worktrees.removed) == [
        (work_slug, "agt-1"),
        (work_slug, "agt-2"),
    ]
    assert sorted(chatstore.deleted) == ["CHT-001", "CHT-002"]
    assert set(chatstore.records) == {"CHT-003"}


def test_delete_raises_when_work_missing() -> None:
    with pytest.raises(delete.WorkNotFound):
        asyncio.run(
            delete.execute(
                _make_workstore(),
                _StubChatStore([]),
                _StubSupervisor(),
                _StubSupervisor(),
                _StubWorktreeManager(),
                delete.DeleteWorkRequest(work_slug="WRK-999"),
            )
        )
