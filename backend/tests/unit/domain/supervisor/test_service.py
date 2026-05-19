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
    ArtifactMarker,
    MessageComplete,
    MessageDelta,
    SessionEstablished,
    StatusChange,
)
from src.domain.models import Artifact, PrArtifact
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


async def _start(
    supervisor: AgentSupervisorService,
    work_slug: str,
    agent_slug: str,
    adapter: Any,
    context: AgentStartContext,
) -> None:
    """Test helper that mirrors the previous ``start_agent`` shape.

    Just calls ``register_agent`` since registration today eagerly calls
    ``adapter.start`` and creates the events-pump task. Kept as a helper
    so tests don't need updating if the lazy-spawn refactor lands later.
    """
    await supervisor.register_agent(work_slug, agent_slug, adapter, context)


# ---------------------------------------------------------------------------
# Happy path: events flow with monotonic seq
# ---------------------------------------------------------------------------


def test_start_agent_emits_events_with_monotonic_seq() -> None:
    async def run() -> list[dict[str, Any]]:
        log = StubTranscriptLog()
        supervisor = AgentSupervisorService(log)
        adapter = StubAgentAdapter(_scripted())
        await _start(supervisor, "WRK-001", "agt-1", adapter, _start_context())

        async with supervisor.subscribe("agt-1") as sub:
            queue = sub.queue
            # Subscribed before any event was published, so replay is empty
            # and all 5 events flow through the live queue.
            assert sub.replay == []
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
        await _start(supervisor, "WRK-001", "agt-1", adapter, _start_context())
        async with supervisor.subscribe("agt-1") as sub:
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
        await _start(supervisor, "WRK-001", "agt-1", adapter, _start_context())

        async with supervisor.subscribe("agt-1") as sub:
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
        await _start(supervisor, "WRK-001", "agt-1", adapter, _start_context())
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
        await _start(supervisor, "WRK-001", "agt-1", adapter, _start_context())

        async with supervisor.subscribe("agt-1") as sub:
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


def test_stop_turn_writes_user_stop_and_forwards_to_adapter() -> None:
    async def run() -> tuple[dict[str, Any], int]:
        log = StubTranscriptLog()
        supervisor = AgentSupervisorService(log)
        adapter = StubAgentAdapter([])
        await _start(supervisor, "WRK-001", "agt-1", adapter, _start_context())

        async with supervisor.subscribe("agt-1") as sub:
            await supervisor.stop_turn("agt-1")
            ev = await sub.queue.get()
        await supervisor.shutdown()
        return ev, adapter.stop_turn_calls

    ev, stop_calls = _run(run())
    assert ev["type"] == "user_stop"
    assert ev["seq"] == 1
    assert stop_calls == 1


def test_stop_turn_to_unknown_agent_raises() -> None:
    async def run() -> None:
        supervisor = AgentSupervisorService(StubTranscriptLog())
        with pytest.raises(ValueError, match="not running"):
            await supervisor.stop_turn("agt-404")

    _run(run())


# ---------------------------------------------------------------------------
# resolve_permission
# ---------------------------------------------------------------------------


def test_resolve_permission_forwards_to_adapter() -> None:
    async def run() -> list[tuple[str, str]]:
        supervisor = AgentSupervisorService(StubTranscriptLog())
        adapter = StubAgentAdapter([])
        await _start(supervisor, "WRK-001", "agt-1", adapter, _start_context())
        await supervisor.resolve_permission("agt-1", "req-1", "allow")
        await supervisor.resolve_permission("agt-1", "req-2", "deny")
        await supervisor.shutdown()
        return adapter.permission_resolutions

    resolutions = _run(run())
    assert resolutions == [("req-1", "allow"), ("req-2", "deny")]


def test_register_clears_stale_permission_requests() -> None:
    """A permission_request without a matching decision is the
    fingerprint of a backend crash mid-prompt (or a closed tile
    whose adapter went away). On the next attach, ``register_agent``
    must publish a synthetic ``deny`` for every orphan so the FE
    prompt clears and the user can proceed with new input."""

    async def run() -> tuple[list[dict[str, object]], list[dict[str, object]]]:
        log = StubTranscriptLog()
        # Seed the transcript with one resolved + one orphan request.
        log.events[("WRK-001", "agt-1")] = [
            {"seq": 1, "type": "permission_request", "request_id": "r1",
             "tool_name": "Bash"},
            {"seq": 2, "type": "permission_decision", "request_id": "r1",
             "decision": "allow"},
            {"seq": 3, "type": "permission_request", "request_id": "r2",
             "tool_name": "Write"},
        ]
        supervisor = AgentSupervisorService(log)
        adapter = StubAgentAdapter(scripted_events=[])
        await _start(supervisor, "WRK-001", "agt-1", adapter, _start_context())
        await supervisor.shutdown()
        events = log.events[("WRK-001", "agt-1")]
        synthetic = [e for e in events if e.get("stale") is True]
        return events, synthetic

    events, synthetic = _run(run())
    # Exactly one synthetic deny landed, for the orphan request only.
    assert len(synthetic) == 1
    assert synthetic[0]["type"] == "permission_decision"
    assert synthetic[0]["request_id"] == "r2"
    assert synthetic[0]["decision"] == "deny"
    # The already-resolved request didn't get a duplicate.
    decisions_for_r1 = [
        e for e in events
        if e.get("type") == "permission_decision" and e.get("request_id") == "r1"
    ]
    assert len(decisions_for_r1) == 1
    # Seq is monotonic — the synthetic landed AFTER the seeded events.
    assert synthetic[0]["seq"] == 4


def test_resolve_permission_to_unknown_agent_is_noop() -> None:
    """A user's click on a stale permission card (backend restarted
    between request and click) must not surface an error — the FE
    can't re-route to a future that's been garbage-collected, but
    the synthetic-deny cleanup at next resume already handles the
    transcript. ``resolve_permission`` swallows the lookup miss
    instead of raising so the route returns 204 cleanly."""

    async def run() -> None:
        supervisor = AgentSupervisorService(StubTranscriptLog())
        await supervisor.resolve_permission("agt-404", "req-1", "allow")

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
        await _start(supervisor,
            "WRK-001", "agt-1", adapter, _start_context()
        )

        async with supervisor.subscribe("agt-1") as sub1:
            async with supervisor.subscribe("agt-1") as sub2:
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
        await _start(supervisor,
            "WRK-001", "agt-1", adapter, _start_context()
        )

        # Manually drive the cm protocol to force a non-LIFO order.
        outer = supervisor.subscribe("agt-1")
        _outer_sub = await outer.__aenter__()

        inner = supervisor.subscribe("agt-1")
        inner_sub = await inner.__aenter__()

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


def test_late_subscriber_with_cursor_sees_only_post_subscription_events() -> None:
    async def run() -> tuple[int, list[dict[str, Any]], bool]:
        supervisor = AgentSupervisorService(StubTranscriptLog())
        adapter = StubAgentAdapter(_scripted())
        await _start(supervisor, "WRK-001", "agt-1", adapter, _start_context())

        # Wait for all five scripted events to be published + flushed.
        await _await_agent(supervisor, "agt-1")

        # Subscribe with cursor=5 ("I've already seen seqs 1..5"). The
        # replay window (5, ∞) is empty on disk, the queue is empty too
        # (adapter exhausted), so nothing should arrive.
        async with supervisor.subscribe("agt-1", cursor=5) as sub:
            queue = sub.queue
            replay = sub.replay
            try:
                await asyncio.wait_for(queue.get(), timeout=0.05)
                got_event = True
            except TimeoutError:
                got_event = False

        await supervisor.shutdown()
        return len(replay), replay, got_event

    replay_len, replay, got_event = _run(run())
    assert replay_len == 0
    assert replay == []
    assert got_event is False


def test_subscribe_with_cursor_zero_replays_full_disk_history() -> None:
    """Cursor=0 means "I haven't seen anything"; the Subscription's
    replay carries the entire transcript so the WS handler can forward
    it before the live queue starts."""

    async def run() -> list[int]:
        supervisor = AgentSupervisorService(StubTranscriptLog())
        adapter = StubAgentAdapter(_scripted())
        await _start(supervisor, "WRK-001", "agt-1", adapter, _start_context())
        await _await_agent(supervisor, "agt-1")

        async with supervisor.subscribe("agt-1", cursor=0) as sub:
            seqs = [e["seq"] for e in sub.replay]

        await supervisor.shutdown()
        return seqs

    assert _run(run()) == [1, 2, 3, 4, 5]


# ---------------------------------------------------------------------------
# Lifecycle
# ---------------------------------------------------------------------------


def test_double_register_raises() -> None:
    async def run() -> None:
        supervisor = AgentSupervisorService(StubTranscriptLog())
        await _start(supervisor, "WRK-001", "agt-1", StubAgentAdapter([]), _start_context())
        with pytest.raises(RuntimeError, match="already registered"):
            await _start(supervisor, "WRK-001", "agt-1", StubAgentAdapter([]), _start_context())
        await supervisor.shutdown()

    _run(run())


def test_register_evicts_state_when_adapter_start_raises() -> None:
    """If ``adapter.start`` raises, the supervisor must not leave a
    half-registered ``_states`` entry behind. A lingering ghost would
    make every subsequent ``connect`` see ``is_registered == True``
    and skip resume, leaving the agent looking stuck on the FE."""

    class _StartFailingAdapter:
        def __init__(self) -> None:
            self.start_calls = 0
            self.closed = False

        async def start(self, context: AgentStartContext) -> None:
            self.start_calls += 1
            raise RuntimeError("permission socket bind failed")

        async def send_input(self, text: str) -> None: ...

        async def events(self):  # type: ignore[no-untyped-def]
            if False:
                yield  # pragma: no cover

        async def close(self) -> None:
            self.closed = True

    async def run() -> tuple[bool, bool, bool]:
        supervisor = AgentSupervisorService(StubTranscriptLog())
        adapter = _StartFailingAdapter()
        with pytest.raises(RuntimeError, match="permission socket bind"):
            await _start(
                supervisor, "WRK-001", "agt-1", adapter, _start_context()
            )
        first_registered = "agt-1" in supervisor._states
        # A second register attempt must now succeed — the slot was
        # freed and the new adapter takes over.
        ok_adapter = StubAgentAdapter([])
        await _start(supervisor, "WRK-001", "agt-1", ok_adapter, _start_context())
        second_registered = "agt-1" in supervisor._states
        await supervisor.shutdown()
        return first_registered, second_registered, adapter.closed

    first, second, closed = _run(run())
    assert first is False
    assert second is True
    assert closed is True


def test_shutdown_closes_all_adapters() -> None:
    async def run() -> tuple[bool, bool]:
        supervisor = AgentSupervisorService(StubTranscriptLog())
        a1 = StubAgentAdapter([])
        a2 = StubAgentAdapter([])
        await _start(supervisor, "WRK-001", "agt-1", a1, _start_context())
        await _start(supervisor, "WRK-001", "agt-2", a2, _start_context())
        await supervisor.shutdown()
        return a1.closed, a2.closed

    closed1, closed2 = _run(run())
    assert closed1 is True
    assert closed2 is True


def test_stop_agent_removes_state_and_closes_adapter() -> None:
    async def run() -> tuple[bool, bool]:
        supervisor = AgentSupervisorService(StubTranscriptLog())
        adapter = StubAgentAdapter(_scripted())
        await _start(supervisor, "WRK-001", "agt-1", adapter, _start_context())
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
        await _start(supervisor, "WRK-001", "agt-1", adapter, _start_context())
        await _await_agent(supervisor, "agt-1")
        await supervisor.shutdown()

    _run(run())
    assert captured == [("agt-1", "sess-abc")]


def test_adapter_task_error_emits_error_event() -> None:
    async def run() -> list[dict[str, Any]]:
        supervisor = AgentSupervisorService(StubTranscriptLog())
        adapter = _CrashingAdapter()
        await _start(supervisor, "WRK-001", "agt-1", adapter, _start_context())

        async with supervisor.subscribe("agt-1") as sub:
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
        await _start(supervisor, "WRK-001", "agt-1", StubAgentAdapter([]), _start_context())
        async with supervisor.subscribe("agt-1") as sub:
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
        await _start(supervisor, "WRK-001", "agt-1", adapter, _start_context())

        async with supervisor.subscribe("agt-1") as sub:
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
        await _start(supervisor, "WRK-001", "agt-1", StubAgentAdapter([]), _start_context())

        async with supervisor.subscribe("agt-1") as sub:
            for i in range(SUBSCRIBER_QUEUE_MAX + 1):
                await supervisor.send_input("agt-1", f"msg-{i}")
            assert sub.kicked.is_set()
            # Publish one more after the kick; should still hit the log.
            await supervisor.send_input("agt-1", "after-kick")

        await supervisor.shutdown()
        return len(log.events[("WRK-001", "agt-1")])

    # SUBSCRIBER_QUEUE_MAX queued + 1 that triggered overflow + 1 after-kick.
    assert _run(run()) == SUBSCRIBER_QUEUE_MAX + 2


# ---------------------------------------------------------------------------
# Lazy spawn (re-attach path: register without firing the events pump)
# ---------------------------------------------------------------------------


def test_lazy_register_does_not_start_pump_until_send_input() -> None:
    """``register_agent(..., lazy=True)`` registers state and runs the
    cheap ``adapter.start`` setup, but doesn't iterate ``adapter.events``.
    The first ``send_input`` creates the pump task. This is the seam the
    re-attach (resume) path uses to avoid burning a fork on providers
    that fork-on-resume."""

    async def run() -> tuple[bool, list[dict[str, Any]], list[str]]:
        log = StubTranscriptLog()
        supervisor = AgentSupervisorService(log)
        adapter = StubAgentAdapter(_scripted())
        await supervisor.register_agent(
            "WRK-001", "agt-1", adapter, _start_context(), lazy=True
        )
        # Adapter.start ran (cheap setup), but no events task exists.
        state = supervisor._states["agt-1"]
        no_task_yet = state.task is None

        async with supervisor.subscribe("agt-1") as sub:
            queue = sub.queue
            await supervisor.send_input("agt-1", "wake up")
            # 1 user_input + 5 scripted demo events from the now-live pump.
            events = [await queue.get() for _ in range(6)]

        await supervisor.shutdown()
        return no_task_yet, events, adapter.received_inputs

    no_task_yet, events, inputs = _run(run())
    assert no_task_yet is True
    assert events[0]["type"] == "user_input"
    assert events[0]["text"] == "wake up"
    assert [e["seq"] for e in events] == [1, 2, 3, 4, 5, 6]
    assert events[-1]["type"] == "status_change"
    assert inputs == ["wake up"]


# ---------------------------------------------------------------------------
# ArtifactMarker dispatch
# ---------------------------------------------------------------------------


def test_artifact_marker_invokes_recorder_and_emits_artifact_recorded() -> None:
    """When an adapter emits ArtifactMarker, the supervisor calls the
    injected recorder and then publishes a synthetic ``artifact_recorded``
    event into the same transcript stream."""

    captured: list[tuple[str, str, dict[str, Any]]] = []

    def recorder(work: str, agent: str, payload: dict[str, Any]) -> Artifact:
        captured.append((work, agent, payload))
        return PrArtifact(
            id=1,
            slug="art-1",
            work_id=1,
            agent_id=1,
            title=payload["title"],
            status=payload["status"],
            created_at=UTC_NOW,
            url=payload["url"],
        )

    async def run() -> list[dict[str, Any]]:
        log = StubTranscriptLog()
        supervisor = AgentSupervisorService(log, record_artifact=recorder)
        marker = ArtifactMarker(
            ts=UTC_NOW,
            payload={
                "type": "pr",
                "url": "https://x/1",
                "title": "Add foo",
                "status": "open",
            },
        )
        adapter = StubAgentAdapter([marker])
        await _start(supervisor, "WRK-001", "agt-1", adapter, _start_context())
        await _await_agent(supervisor, "agt-1")
        await supervisor.shutdown()
        return log.events[("WRK-001", "agt-1")]

    events = _run(run())
    assert captured == [
        (
            "WRK-001",
            "agt-1",
            {
                "type": "pr",
                "url": "https://x/1",
                "title": "Add foo",
                "status": "open",
            },
        )
    ]
    assert [e["type"] for e in events] == ["artifact_marker", "artifact_recorded"]
    recorded = events[1]
    assert recorded["artifact"]["slug"] == "art-1"
    assert recorded["artifact"]["title"] == "Add foo"


def test_artifact_marker_validation_failure_emits_error_event() -> None:
    """A ``ValueError`` from the recorder surfaces as an ``error`` event in
    the transcript — the marker itself is still durable on disk."""

    def recorder(_w: str, _a: str, _p: dict[str, Any]) -> Artifact:
        raise ValueError("missing 'title'")

    async def run() -> list[dict[str, Any]]:
        log = StubTranscriptLog()
        supervisor = AgentSupervisorService(log, record_artifact=recorder)
        marker = ArtifactMarker(ts=UTC_NOW, payload={"type": "pr"})
        adapter = StubAgentAdapter([marker])
        await _start(supervisor, "WRK-001", "agt-1", adapter, _start_context())
        await _await_agent(supervisor, "agt-1")
        await supervisor.shutdown()
        return log.events[("WRK-001", "agt-1")]

    events = _run(run())
    types = [e["type"] for e in events]
    assert types == ["artifact_marker", "error"]
    assert "missing 'title'" in events[1]["message"]


def test_artifact_marker_without_recorder_is_passthrough() -> None:
    """If no recorder is wired, the marker still flows through the normal
    transcript pipeline; the supervisor is just a relay."""

    async def run() -> list[dict[str, Any]]:
        log = StubTranscriptLog()
        supervisor = AgentSupervisorService(log)  # no recorder
        marker = ArtifactMarker(ts=UTC_NOW, payload={"type": "pr"})
        adapter = StubAgentAdapter([marker])
        await _start(supervisor, "WRK-001", "agt-1", adapter, _start_context())
        await _await_agent(supervisor, "agt-1")
        await supervisor.shutdown()
        return log.events[("WRK-001", "agt-1")]

    events = _run(run())
    assert [e["type"] for e in events] == ["artifact_marker"]


def test_lazy_register_calls_adapter_start_with_context() -> None:
    """The ``adapter.start`` step still runs at register time even in
    lazy mode — it's the cheap setup (Amp's permission socket, Claude's
    SDK connect) that needs to be live before the first send_input."""

    async def run() -> AgentStartContext | None:
        supervisor = AgentSupervisorService(StubTranscriptLog())
        adapter = StubAgentAdapter([])
        ctx = _start_context()
        await supervisor.register_agent("WRK-001", "agt-1", adapter, ctx, lazy=True)
        await supervisor.shutdown()
        return adapter.start_context

    assert _run(run()) is not None
