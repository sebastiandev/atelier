"""Adapter contract test suite.

A single parametrised module that runs against any `AgentAdapter`
implementation and asserts the invariants the supervisor depends on:

  - Every yielded event is one of the `AgentEvent` variants.
  - Event timestamps are non-decreasing.
  - `close()` is idempotent.
  - `send_input` is accepted (does not raise) before, between, or after
    iterating events.
  - The supervisor-required lifecycle (start before events before close)
    completes without error.

Sprint 1 parametrises over the stub adapter only. The Claude and Amp
adapters wrap external SDKs with their own translation layers and are
exempt: each ships dedicated ``_convert`` unit tests plus a manual
smoke test from a developer machine. Codex (STORY-021) will follow the
same pattern.

The interpretation note: STORY-007's spec mentions a monotonic ``seq``,
but ``seq`` is owned by the supervisor (STORY-009), not the adapter — at
the adapter layer the corresponding invariant is monotonic ``ts``.
"""

import asyncio
from collections.abc import Callable, Coroutine
from datetime import UTC, datetime, timedelta
from itertools import pairwise
from pathlib import Path
from typing import Any, TypeVar

import pytest

from src.domain.agents import (
    AgentAdapter,
    AgentEvent,
    AgentStartContext,
    ArtifactMarker,
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

EXPECTED_VARIANTS: tuple[type, ...] = (
    MessageDelta,
    MessageComplete,
    ToolCall,
    ToolResult,
    StatusChange,
    ArtifactMarker,
    Error,
)


AdapterFactory = Callable[[list[AgentEvent]], AgentAdapter]


@pytest.fixture(
    params=[
        pytest.param(lambda events: StubAgentAdapter(events), id="stub"),
    ]
)
def adapter_factory(request: pytest.FixtureRequest) -> AdapterFactory:
    return request.param  # type: ignore[no-any-return]


def _scripted_events() -> list[AgentEvent]:
    """Canonical contract-test sequence: covers every variant, monotonic ts."""
    base = UTC_NOW
    return [
        StatusChange(ts=base + timedelta(seconds=0), status="thinking"),
        MessageDelta(ts=base + timedelta(seconds=1), text="hel"),
        MessageDelta(ts=base + timedelta(seconds=2), text="lo"),
        MessageComplete(ts=base + timedelta(seconds=3), text="hello"),
        ToolCall(
            ts=base + timedelta(seconds=4),
            tool_id="t-1",
            name="bash",
            arguments={"cmd": "ls"},
        ),
        ToolResult(ts=base + timedelta(seconds=5), tool_id="t-1", content="ok"),
        ArtifactMarker(
            ts=base + timedelta(seconds=6),
            payload={"type": "pr", "url": "https://example.test/pr/1"},
        ),
        StatusChange(ts=base + timedelta(seconds=7), status="idle"),
    ]


def _start_context() -> AgentStartContext:
    return AgentStartContext(
        workdir=Path("/tmp/contract-agent"),
        model="m",
        system_prompt="you are an agent",
    )


def _run(coro: Coroutine[Any, Any, T]) -> T:
    return asyncio.run(coro)


# ---------------------------------------------------------------------------
# Contract assertions
# ---------------------------------------------------------------------------


def test_emits_only_agent_event_variants(adapter_factory: AdapterFactory) -> None:
    adapter = adapter_factory(_scripted_events())

    async def session() -> list[AgentEvent]:
        await adapter.start(_start_context())
        events = [ev async for ev in adapter.events()]
        await adapter.close()
        return events

    events = _run(session())
    assert events  # the stub script is non-empty
    for ev in events:
        assert isinstance(ev, EXPECTED_VARIANTS), f"unexpected event type: {type(ev)}"


def test_timestamps_are_non_decreasing(adapter_factory: AdapterFactory) -> None:
    adapter = adapter_factory(_scripted_events())

    async def session() -> list[AgentEvent]:
        await adapter.start(_start_context())
        events = [ev async for ev in adapter.events()]
        await adapter.close()
        return events

    events = _run(session())
    for prev, curr in pairwise(events):
        assert curr.ts >= prev.ts, f"non-monotonic ts: {prev.ts} → {curr.ts}"


def test_close_is_idempotent(adapter_factory: AdapterFactory) -> None:
    adapter = adapter_factory([])

    async def session() -> None:
        await adapter.start(_start_context())
        await adapter.close()
        await adapter.close()  # must not raise

    _run(session())


def test_send_input_does_not_raise(adapter_factory: AdapterFactory) -> None:
    adapter = adapter_factory([])

    async def session() -> None:
        await adapter.start(_start_context())
        await adapter.send_input("first message")
        await adapter.send_input("follow-up")
        await adapter.close()

    _run(session())


def test_stop_turn_does_not_raise(adapter_factory: AdapterFactory) -> None:
    """stop_turn is callable any time after start — including when no
    turn is in flight. Adapters whose SDK can't interrupt mid-turn no-op
    silently rather than raising; the supervisor relies on this."""
    adapter = adapter_factory([])

    async def session() -> None:
        await adapter.start(_start_context())
        await adapter.stop_turn()  # no in-flight turn
        await adapter.stop_turn()  # idempotent
        await adapter.close()

    _run(session())


def test_full_lifecycle_completes(adapter_factory: AdapterFactory) -> None:
    """start → consume events → send_input → close, all in order."""
    adapter = adapter_factory(_scripted_events())

    async def session() -> int:
        await adapter.start(_start_context())
        count = 0
        async for _ in adapter.events():
            count += 1
        await adapter.send_input("after stream")
        await adapter.close()
        return count

    assert _run(session()) == len(_scripted_events())


def test_empty_event_stream_is_valid(adapter_factory: AdapterFactory) -> None:
    """An adapter that has nothing to say must still complete its lifecycle cleanly."""
    adapter = adapter_factory([])

    async def session() -> list[AgentEvent]:
        await adapter.start(_start_context())
        events = [ev async for ev in adapter.events()]
        await adapter.close()
        return events

    assert _run(session()) == []
