from __future__ import annotations

from collections.abc import AsyncIterator
from datetime import UTC, datetime
from pathlib import Path

import pytest

from src.domain.agents import (
    AgentEvent,
    AgentStartContext,
    MessageComplete,
    PermissionDecisionValue,
    StatusChange,
    ToolCall,
)
from src.infrastructure.agents.compaction_sessions import _send_and_collect_text


@pytest.fixture
def anyio_backend() -> str:
    return "asyncio"


class FakeSummaryAdapter:
    def __init__(self, events: list[AgentEvent]) -> None:
        self._events = events
        self.started = False
        self.closed = False
        self.inputs: list[str] = []

    async def start(self, _context: AgentStartContext) -> None:
        self.started = True

    async def send_input(self, text: str) -> None:
        self.inputs.append(text)

    async def stop_turn(self) -> None:
        pass

    async def resolve_permission(
        self, _request_id: str, _decision: PermissionDecisionValue
    ) -> None:
        pass

    async def events(self) -> AsyncIterator[AgentEvent]:
        for event in self._events:
            yield event

    async def close(self) -> None:
        self.closed = True


@pytest.mark.anyio
async def test_summary_collection_ignores_tool_attempts() -> None:
    now = datetime(2026, 5, 27, 11, 30, tzinfo=UTC)
    adapter = FakeSummaryAdapter(
        [
            ToolCall(
                ts=now,
                tool_id="tool-1",
                name="Bash",
                arguments={"command": "git status"},
            ),
            MessageComplete(ts=now, text="## Goal\nKeep going."),
            StatusChange(ts=now, status="idle"),
        ]
    )

    summary = await _send_and_collect_text(
        adapter,
        AgentStartContext(
            workdir=Path("/tmp/agent"),
            model="smart",
            system_prompt="summarize only",
        ),
        "summarize this transcript",
        timeout=1,
    )

    assert summary == "## Goal\nKeep going."
    assert adapter.inputs == ["summarize this transcript"]
    assert adapter.closed is True
