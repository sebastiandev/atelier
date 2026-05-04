"""Unit tests for AgentSupervisorService against the stub adapter + a fake log.

Async lifecycles use ``asyncio.run`` per test to keep the suite plugin-free.
The stub adapter's events() iterator is finite, so ``await state.task``
inside the supervisor is a deterministic synchronisation point.
"""

import asyncio
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any, TypeVar

import pytest

from src.domain.agents import (
    AgentEvent,
    AgentStartContext,
    MessageComplete,
    MessageDelta,
    SessionEstablished,
    StatusChange,
)
from src.domain.supervisor import SUBSCRIBER_QUEUE_MAX, AgentSupervisorService
from src.infrastructure.agents import StubAgentAdapter
from tests.unit.domain.workstore._stubs import StubTranscriptLog

T = TypeVar("T")
UTC_NOW = datetime(2026, 5, 1, 13, 49, tzinfo=UTC)


def _run(coro: Any) -> Any:
    return asyncio.run(coro)


def _start_context() -> AgentStartContext:
    return AgentStartContext(
        workdir=Path("/tmp/agent"),
        context_md="brief",
        model="m",
        system_prompt="s",
    )


def _scripted() -> list[AgentEvent]:
    base = UTC_NOW
    return [
        StatusChange(ts=base + timedelta(seconds=0), status="thinking"),
        MessageDelta(ts=base + timedelta(seconds=1), text="hel"),
        MessageDelta(ts=base + timedelta(seconds=2), text="lo"),
        MessageComplete(ts=base + timedelta(seconds=3), text="hello"),
        StatusChange(ts=base + timedelta(seconds=4), status="idle"),
    ]


async def _await_agent(supervisor: AgentSupervisorService, agent_slug: str) -> None:
    """Wait for the agent's adapter task to drain (test helper)."""
    state = supervisor._states[agent_slug]
    if state.task is not None:
        await state.task


# ---------------------------------------------------------------------------
# Happy path: events flow with monotonic seq
# ---------------------------------------------------------------------------


def test_start_agent_emits_events_with_monotonic_seq() -> None:
    async def run() -> list[dict[str, Any]]:
        log = StubTranscriptLog()
        supervisor = AgentSupervisorService(log)
        adapter = StubAgentAdapter(_scripted())
        await supervisor.start_agent("WRK-001", "agt-1", adapter, _start_context())

        async with supervisor.subscribe("agt-1") as (from_seq, sub):
            queue = sub.queue
            assert from_seq == 0
            events = [await queue.get() for _ in range(5)]

        await supervisor.shutdown()
        return events

    received = _run(run())
    assert [e["seq"] for e in received] == [1, 2, 3, 4, 5]
    assert received[0]["type"] == "status_change"
    assert received[1]["text"] == "hel"
    assert received[3]["type"] == "message_complete"


def test_events_carry_iso_timestamp_strings() -> None:
    async def run() -> dict[str, Any]:
        supervisor = AgentSupervisorService(StubTranscriptLog())
        adapter = StubAgentAdapter([_scripted()[0]])
        await supervisor.start_agent("WRK-001", "agt-1", adapter, _start_context())
        async with supervisor.subscribe("agt-1") as (_, sub):
            queue = sub.queue
            ev = await queue.get()
        await supervisor.shutdown()
        return ev

    ev = _run(run())
    assert isinstance(ev["ts"], str)
    assert ev["ts"].startswith("2026-05-01")


# ---------------------------------------------------------------------------
# Write-through-before-fanout invariant
# ---------------------------------------------------------------------------


def test_write_through_before_fanout() -> None:
    """By the time a subscriber receives an event, the transcript log already
    has it. Verifies the load-bearing ordering invariant."""

    async def run() -> None:
        log = StubTranscriptLog()
        supervisor = AgentSupervisorService(log)
        adapter = StubAgentAdapter(_scripted())
        await supervisor.start_agent("WRK-001", "agt-1", adapter, _start_context())

        async with supervisor.subscribe("agt-1") as (_, sub):
            queue = sub.queue
            for _ in range(5):
                ev = await queue.get()
                logged_seqs = [e["seq"] for e in log.events.get(("WRK-001", "agt-1"), [])]
                assert ev["seq"] in logged_seqs, (
                    f"event {ev['seq']} reached subscriber before being logged"
                )

        await supervisor.shutdown()

    _run(run())


def test_log_receives_every_event() -> None:
    async def run() -> StubTranscriptLog:
        log = StubTranscriptLog()
        supervisor = AgentSupervisorService(log)
        adapter = StubAgentAdapter(_scripted())
        await supervisor.start_agent("WRK-001", "agt-1", adapter, _start_context())
        await _await_agent(supervisor, "agt-1")
        await supervisor.shutdown()
        return log

    log = _run(run())
    seqs = [e["seq"] for e in log.events[("WRK-001", "agt-1")]]
    assert seqs == [1, 2, 3, 4, 5]


# ---------------------------------------------------------------------------
# send_input
# ---------------------------------------------------------------------------


def test_send_input_writes_user_input_then_forwards_to_adapter() -> None:
    async def run() -> tuple[dict[str, Any], list[str]]:
        supervisor = AgentSupervisorService(StubTranscriptLog())
        adapter = StubAgentAdapter([])  # no scripted events
        await supervisor.start_agent("WRK-001", "agt-1", adapter, _start_context())

        async with supervisor.subscribe("agt-1") as (_, sub):
            queue = sub.queue
            await supervisor.send_input("agt-1", "hello there")
            ev = await queue.get()

        await supervisor.shutdown()
        return ev, adapter.received_inputs

    ev, inputs = _run(run())
    assert ev["type"] == "user_input"
    assert ev["text"] == "hello there"
    assert ev["seq"] == 1
    assert inputs == ["hello there"]


def test_send_input_to_unknown_agent_raises() -> None:
    async def run() -> None:
        supervisor = AgentSupervisorService(StubTranscriptLog())
        with pytest.raises(ValueError, match="not running"):
            await supervisor.send_input("agt-404", "x")

    _run(run())


# ---------------------------------------------------------------------------
# Single-subscriber model: resubscribe replaces
# ---------------------------------------------------------------------------


def test_resubscribe_replaces_previous_subscriber() -> None:
    """Atelier is single-user single-browser: at most one subscriber per
    agent. A second subscribe (reconnect race) replaces the slot; the
    previous queue stops receiving events."""

    async def run() -> tuple[bool, str]:
        supervisor = AgentSupervisorService(StubTranscriptLog())
        adapter = StubAgentAdapter([])
        await supervisor.start_agent(
            "WRK-001", "agt-1", adapter, _start_context()
        )

        async with supervisor.subscribe("agt-1") as (_, sub1):
            async with supervisor.subscribe("agt-1") as (_, sub2):
                # The slot now points at sub2; sub1 is abandoned.
                await supervisor.send_input("agt-1", "after replace")
                ev = await sub2.queue.get()
                # sub1 was not put into.
                q1_empty = sub1.queue.empty()

        await supervisor.shutdown()
        return q1_empty, ev["text"]

    q1_empty, text = _run(run())
    assert q1_empty is True
    assert text == "after replace"


def test_outer_subscribe_finally_does_not_clear_inner_slot() -> None:
    """When two subscribes overlap (LIFO unwind), the outer's cleanup must
    only clear the slot if it still points at the outer's queue. Stale
    cleanup must not disturb a fresh inner subscriber."""

    async def run() -> bool:
        supervisor = AgentSupervisorService(StubTranscriptLog())
        adapter = StubAgentAdapter([])
        await supervisor.start_agent(
            "WRK-001", "agt-1", adapter, _start_context()
        )

        # Manually drive the cm protocol to force a non-LIFO order.
        outer = supervisor.subscribe("agt-1")
        _, _outer_sub = await outer.__aenter__()

        inner = supervisor.subscribe("agt-1")
        _, inner_sub = await inner.__aenter__()

        # Now exit OUTER first (the older one). Its finally should not
        # clear the slot, because the slot points at inner_sub.
        await outer.__aexit__(None, None, None)

        # The slot should still be inner_sub — verify by publishing.
        await supervisor.send_input("agt-1", "still flowing")
        ev = await inner_sub.queue.get()
        ok = ev["text"] == "still flowing"

        await inner.__aexit__(None, None, None)
        await supervisor.shutdown()
        return ok

    assert _run(run()) is True


# ---------------------------------------------------------------------------
# Late subscriber sees only new events
# ---------------------------------------------------------------------------


def test_late_subscriber_sees_only_post_subscription_events() -> None:
    async def run() -> tuple[int, bool]:
        supervisor = AgentSupervisorService(StubTranscriptLog())
        adapter = StubAgentAdapter(_scripted())
        await supervisor.start_agent("WRK-001", "agt-1", adapter, _start_context())

        # Wait for all five scripted events to be published + flushed.
        await _await_agent(supervisor, "agt-1")

        async with supervisor.subscribe("agt-1") as (from_seq, sub):
            queue = sub.queue
            try:
                await asyncio.wait_for(queue.get(), timeout=0.05)
                got_event = True
            except TimeoutError:
                got_event = False

        await supervisor.shutdown()
        return from_seq, got_event

    from_seq, got_event = _run(run())
    assert from_seq == 5
    assert got_event is False


# ---------------------------------------------------------------------------
# Lifecycle
# ---------------------------------------------------------------------------


def test_double_start_raises() -> None:
    async def run() -> None:
        supervisor = AgentSupervisorService(StubTranscriptLog())
        await supervisor.start_agent("WRK-001", "agt-1", StubAgentAdapter([]), _start_context())
        with pytest.raises(RuntimeError, match="already running"):
            await supervisor.start_agent("WRK-001", "agt-1", StubAgentAdapter([]), _start_context())
        await supervisor.shutdown()

    _run(run())


def test_shutdown_closes_all_adapters() -> None:
    async def run() -> tuple[bool, bool]:
        supervisor = AgentSupervisorService(StubTranscriptLog())
        a1 = StubAgentAdapter([])
        a2 = StubAgentAdapter([])
        await supervisor.start_agent("WRK-001", "agt-1", a1, _start_context())
        await supervisor.start_agent("WRK-001", "agt-2", a2, _start_context())
        await supervisor.shutdown()
        return a1.closed, a2.closed

    closed1, closed2 = _run(run())
    assert closed1 is True
    assert closed2 is True


def test_stop_agent_removes_state_and_closes_adapter() -> None:
    async def run() -> tuple[bool, bool]:
        supervisor = AgentSupervisorService(StubTranscriptLog())
        adapter = StubAgentAdapter(_scripted())
        await supervisor.start_agent("WRK-001", "agt-1", adapter, _start_context())
        await _await_agent(supervisor, "agt-1")
        await supervisor.stop_agent("agt-1")
        return adapter.closed, "agt-1" in supervisor._states

    closed, still_registered = _run(run())
    assert closed is True
    assert still_registered is False


def test_stop_agent_unknown_is_noop() -> None:
    async def run() -> None:
        supervisor = AgentSupervisorService(StubTranscriptLog())
        await supervisor.stop_agent("agt-404")  # does not raise

    _run(run())


def test_subscribe_unknown_agent_raises() -> None:
    async def run() -> None:
        supervisor = AgentSupervisorService(StubTranscriptLog())
        with pytest.raises(ValueError, match="not running"):
            async with supervisor.subscribe("agt-404"):
                pass

    _run(run())


# ---------------------------------------------------------------------------
# Adapter task crash
# ---------------------------------------------------------------------------


class _CrashingAdapter:
    """Yields one event then raises — simulates an upstream SDK crash."""

    def __init__(self) -> None:
        self.start_calls = 0
        self.closed = False

    async def start(self, context: AgentStartContext) -> None:
        self.start_calls += 1

    async def send_input(self, text: str) -> None: ...

    async def events(self):  # type: ignore[no-untyped-def]
        yield StatusChange(ts=UTC_NOW, status="thinking")
        raise RuntimeError("upstream SDK exploded")

    async def close(self) -> None:
        self.closed = True


def test_session_established_invokes_set_session_id_callback() -> None:
    captured: list[tuple[str, str]] = []

    async def run() -> None:
        supervisor = AgentSupervisorService(
            StubTranscriptLog(),
            set_session_id=lambda slug, sid: captured.append((slug, sid)),
        )
        events: list[AgentEvent] = [
            SessionEstablished(ts=UTC_NOW, session_id="sess-abc"),
            StatusChange(ts=UTC_NOW, status="idle"),
        ]
        adapter = StubAgentAdapter(events)
        await supervisor.start_agent("WRK-001", "agt-1", adapter, _start_context())
        await _await_agent(supervisor, "agt-1")
        await supervisor.shutdown()

    _run(run())
    assert captured == [("agt-1", "sess-abc")]


def test_adapter_task_error_emits_error_event() -> None:
    async def run() -> list[dict[str, Any]]:
        supervisor = AgentSupervisorService(StubTranscriptLog())
        adapter = _CrashingAdapter()
        await supervisor.start_agent("WRK-001", "agt-1", adapter, _start_context())

        async with supervisor.subscribe("agt-1") as (_, sub):
            queue = sub.queue
            first = await queue.get()
            second = await queue.get()

        await supervisor.shutdown()
        return [first, second]

    events = _run(run())
    assert events[0]["type"] == "status_change"
    assert events[1]["type"] == "error"
    assert "exploded" in events[1]["message"]


# ---------------------------------------------------------------------------
# Slow-subscriber drop policy
# ---------------------------------------------------------------------------


def test_subscription_queue_is_bounded() -> None:
    """The per-subscription queue caps at SUBSCRIBER_QUEUE_MAX so a slow
    subscriber can't grow memory unbounded."""

    async def run() -> int:
        supervisor = AgentSupervisorService(StubTranscriptLog())
        await supervisor.start_agent("WRK-001", "agt-1", StubAgentAdapter([]), _start_context())
        async with supervisor.subscribe("agt-1") as (_, sub):
            maxsize = sub.queue.maxsize
        await supervisor.shutdown()
        return maxsize

    assert _run(run()) == SUBSCRIBER_QUEUE_MAX


def test_slow_subscriber_is_kicked_and_slot_cleared() -> None:
    """A subscriber whose queue overflows fires its kicked event and the
    supervisor drops it from the slot, so subsequent events neither fan
    to the abandoned queue nor block publishing."""

    async def run() -> tuple[bool, bool, bool]:
        supervisor = AgentSupervisorService(StubTranscriptLog())
        adapter = StubAgentAdapter([])  # publish via send_input only
        await supervisor.start_agent("WRK-001", "agt-1", adapter, _start_context())

        async with supervisor.subscribe("agt-1") as (_, sub):
            # Don't drain the queue; fill past the cap to trigger overflow.
            for i in range(SUBSCRIBER_QUEUE_MAX + 5):
                await supervisor.send_input("agt-1", f"msg-{i}")
            kicked_fired = sub.kicked.is_set()
            slot_cleared = supervisor._states["agt-1"].subscriber is None
            queue_at_cap = sub.queue.qsize() == SUBSCRIBER_QUEUE_MAX

        await supervisor.shutdown()
        return kicked_fired, slot_cleared, queue_at_cap

    fired, cleared, capped = _run(run())
    assert fired is True
    assert cleared is True
    assert capped is True


def test_kicked_subscriber_does_not_block_further_publishing() -> None:
    """After a subscriber is dropped, publishing keeps working — events
    continue to land on disk for later replay via ?cursor=N."""

    async def run() -> int:
        log = StubTranscriptLog()
        supervisor = AgentSupervisorService(log)
        await supervisor.start_agent("WRK-001", "agt-1", StubAgentAdapter([]), _start_context())

        async with supervisor.subscribe("agt-1") as (_, sub):
            for i in range(SUBSCRIBER_QUEUE_MAX + 1):
                await supervisor.send_input("agt-1", f"msg-{i}")
            assert sub.kicked.is_set()
            # Publish one more after the kick; should still hit the log.
            await supervisor.send_input("agt-1", "after-kick")

        await supervisor.shutdown()
        return len(log.events[("WRK-001", "agt-1")])

    # SUBSCRIBER_QUEUE_MAX queued + 1 that triggered overflow + 1 after-kick.
    assert _run(run()) == SUBSCRIBER_QUEUE_MAX + 2
