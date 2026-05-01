"""Drive the StubAgentAdapter through a full lifecycle.

Satisfies STORY-006 AC: a scripted event sequence flows from the stub
through the AgentAdapter port end-to-end. Async lifecycle uses
``asyncio.run`` so we don't pull in another pytest plugin just for this.
"""

import asyncio
from collections.abc import Awaitable, Callable, Coroutine
from datetime import UTC, datetime
from pathlib import Path
from typing import TypeVar

from src.domain.agents import (
    AgentAdapter,
    AgentEvent,
    AgentStartContext,
    Error,
    MessageComplete,
    MessageDelta,
    StatusChange,
    ToolCall,
    ToolResult,
)
from src.infrastructure.agents import StubAgentAdapter

T = TypeVar("T")
UTC_NOW = datetime(2026, 5, 1, 13, 49, tzinfo=UTC)


def _run(coro: Coroutine[None, None, T]) -> T:
    return asyncio.run(coro)


def _start_context() -> AgentStartContext:
    return AgentStartContext(
        workdir=Path("/tmp/agent-1"),
        context_md="# brief",
        model="claude-opus-4-7",
        system_prompt="you are an agent",
    )


async def _drain(events: Callable[[], "Awaitable[None] | object"]) -> list[AgentEvent]:
    out: list[AgentEvent] = []
    async for ev in events():  # type: ignore[union-attr]
        out.append(ev)
    return out


def test_stub_adapter_satisfies_protocol() -> None:
    adapter: AgentAdapter = StubAgentAdapter([])
    # Structural protocol check — if anything's missing, this would fail
    # at type-check time. Asserting at runtime is a smoke check.
    assert callable(adapter.start)
    assert callable(adapter.send_input)
    assert callable(adapter.events)
    assert callable(adapter.close)


def test_start_records_context() -> None:
    adapter = StubAgentAdapter([])
    ctx = _start_context()
    _run(adapter.start(ctx))
    assert adapter.start_context is ctx


def test_start_twice_raises() -> None:
    adapter = StubAgentAdapter([])
    ctx = _start_context()
    _run(adapter.start(ctx))
    try:
        _run(adapter.start(ctx))
    except RuntimeError as e:
        assert "twice" in str(e)
    else:
        raise AssertionError("expected RuntimeError on second start")


def test_send_input_accumulates() -> None:
    adapter = StubAgentAdapter([])
    _run(adapter.send_input("hi"))
    _run(adapter.send_input("again"))
    assert adapter.received_inputs == ["hi", "again"]


def test_close_is_idempotent() -> None:
    adapter = StubAgentAdapter([])
    _run(adapter.close())
    _run(adapter.close())
    assert adapter.closed is True


def test_full_scripted_session_end_to_end() -> None:
    """STORY-006 AC: drive a sample event sequence through start → events
    → send_input → close, verifying each side of the contract."""
    scripted: list[AgentEvent] = [
        StatusChange(ts=UTC_NOW, status="thinking"),
        MessageDelta(ts=UTC_NOW, text="hel"),
        MessageDelta(ts=UTC_NOW, text="lo"),
        MessageComplete(ts=UTC_NOW, text="hello"),
        ToolCall(ts=UTC_NOW, tool_id="t-1", name="bash", arguments={"cmd": "ls"}),
        ToolResult(ts=UTC_NOW, tool_id="t-1", content="file1\nfile2"),
        StatusChange(ts=UTC_NOW, status="idle"),
    ]
    adapter = StubAgentAdapter(scripted)

    async def session() -> list[AgentEvent]:
        await adapter.start(_start_context())
        await adapter.send_input("please list files")
        collected: list[AgentEvent] = []
        async for ev in adapter.events():
            collected.append(ev)
        await adapter.close()
        return collected

    received = _run(session())

    assert [type(ev).__name__ for ev in received] == [
        "StatusChange",
        "MessageDelta",
        "MessageDelta",
        "MessageComplete",
        "ToolCall",
        "ToolResult",
        "StatusChange",
    ]
    assert adapter.received_inputs == ["please list files"]
    assert adapter.closed is True
    assert adapter.start_context is not None
    assert adapter.start_context.model == "claude-opus-4-7"


def test_error_event_propagates_through_iterator() -> None:
    adapter = StubAgentAdapter([Error(ts=UTC_NOW, message="upstream failed")])

    async def collect() -> list[AgentEvent]:
        await adapter.start(_start_context())
        out = [ev async for ev in adapter.events()]
        await adapter.close()
        return out

    events = _run(collect())
    assert len(events) == 1
    assert isinstance(events[0], Error)
    assert events[0].message == "upstream failed"
