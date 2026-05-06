"""AgentSupervisor — runs one agent, ferries events between SDK, disk, and browser.

═══════════════════════════════════════════════════════════════════════
What this does (plain English)
═══════════════════════════════════════════════════════════════════════

An "agent" is a Claude/Amp/Codex AI session. It produces a stream of
events: streaming text, tool calls, status changes. The supervisor is
the traffic cop sitting between three things:

    ┌─────────────┐   WebSocket    ┌──────────────┐  AgentAdapter   ┌────────────┐
    │   Browser   │ ◄────────────► │  Supervisor  │ ◄─────────────► │ Claude SDK │
    │ (AgentTile) │                │              │                 │  (or stub) │
    └─────────────┘                └───────┬──────┘                 └────────────┘
                                           │
                                           │ append + fsync
                                           ▼
                                   ┌───────────────┐
                                   │ transcript.   │  ← canonical, on disk
                                   │   ndjson      │
                                   └───────────────┘

For each event the SDK emits, the supervisor — under a per-agent lock —
does three things, in this order:

    1. stamp a per-agent monotonic ``seq`` (1, 2, 3, ...)
    2. append the event to ``transcript.ndjson`` and fsync
    3. fan out to every subscribed WebSocket queue

The lock keeps these three steps atomic. The ORDER is the load-bearing
invariant:

    "No event reaches a subscriber before it's already on disk."

If the process crashes between step 2 and step 3, the event is durable;
the browser just hasn't seen it yet. It picks it up on the next
reconnect via the replay path below.

User input takes the same path: ``send_input`` writes a ``user_input``
line (under the same lock, with its own stamped seq) BEFORE forwarding
to the SDK. Result: the transcript is one ordered conversation, both
sides interleaved by ``seq``.

═══════════════════════════════════════════════════════════════════════
Replay on reconnect — how the cursor works
═══════════════════════════════════════════════════════════════════════

A browser disconnects mid-stream. It reconnects with ``?cursor=N``
(the ``seq`` of the last event it saw). The WS handler:

    1. ``supervisor.subscribe(slug)`` → ``(from_seq, queue)``.
       ``from_seq`` is the supervisor's current seq AT subscription time,
       captured atomically with queue registration under the lock.
    2. Read ``transcript.ndjson`` for events with ``cursor < seq <= from_seq``
       and send them to the browser. (These are the events it missed.)
    3. Drain ``queue`` for events with ``seq > from_seq``. (Live ones.)

Step 1's atomicity is the trick: any event published BEFORE subscription
landed on disk only (and is picked up by step 2). Any event published
AFTER subscription lands on both disk AND the queue (and is delivered
by step 3). No overlap, no gap, exactly-once delivery.

═══════════════════════════════════════════════════════════════════════
Concurrency cheat sheet
═══════════════════════════════════════════════════════════════════════

Per running agent there are three concurrent things going on:

    ┌─ Agent task (one asyncio.Task) ─────────────────────────────────┐
    │   async for event in adapter.events():                          │
    │       stamp seq → write+fsync → fan out                         │
    └─────────────────────────────────────────────────────────────────┘
    ┌─ WS subscriber (zero or one) ───────────────────────────────────┐
    │   the WS handler registers a queue via subscribe(); a second    │
    │   subscribe (e.g., reconnect race) replaces the slot. publish   │
    │   puts events into the current queue if one is registered.      │
    └─────────────────────────────────────────────────────────────────┘
    ┌─ User input ────────────────────────────────────────────────────┐
    │   send_input() writes user_input transcript line + forwards     │
    │   the text to adapter.send_input()                              │
    └─────────────────────────────────────────────────────────────────┘

A per-agent ``publish_lock`` serializes the three steps of publishing,
so the agent task and ``send_input`` from any number of WS handlers
never interleave a partial publish.
"""

from __future__ import annotations

import asyncio
import dataclasses
import logging
from collections.abc import AsyncIterator, Callable
from contextlib import asynccontextmanager, suppress
from datetime import UTC, datetime
from typing import Any

from src.domain.agents import (
    AgentAdapter,
    AgentEvent,
    AgentStartContext,
    PermissionDecisionValue,
    SessionEstablished,
)
from src.domain.workstore.ports import TranscriptLog

SetSessionIdFn = Callable[[str, str], None]

_log = logging.getLogger(__name__)


# Bound for the per-subscription queue. The supervisor stamps events as
# fast as the adapter emits them; the WS handler drains them at network
# speed. If a subscriber falls this far behind we cut it loose rather
# than grow memory unbounded — disk has every event, so the client can
# reconnect with ?cursor=N and resume.
SUBSCRIBER_QUEUE_MAX = 256


@dataclasses.dataclass(frozen=True)
class AgentSubscription:
    """Handle returned by ``AgentSupervisorService.subscribe``.

    ``queue`` is bounded to ``SUBSCRIBER_QUEUE_MAX``. If publishing
    overflows it (slow consumer), the supervisor sets ``kicked`` and
    drops the subscriber slot. Callers should select between
    ``queue.get()`` and ``kicked.wait()`` and close the upstream
    connection if ``kicked`` fires.
    """

    queue: asyncio.Queue[dict[str, Any]]
    kicked: asyncio.Event


@dataclasses.dataclass
class _AgentState:
    work_slug: str
    agent_slug: str
    adapter: AgentAdapter
    seq: int = 0
    task: asyncio.Task[None] | None = None
    publish_lock: asyncio.Lock = dataclasses.field(default_factory=asyncio.Lock)
    # Atelier is single-user single-browser: at most one WS subscriber per
    # agent. A second subscribe (e.g., reconnect-before-cleanup) replaces
    # the slot; the previous subscription is silently abandoned because
    # its WS is presumed dead.
    subscriber: AgentSubscription | None = None


class AgentSupervisorService:
    def __init__(
        self,
        transcript_log: TranscriptLog,
        set_session_id: SetSessionIdFn = lambda _slug, _sid: None,
    ) -> None:
        self._transcript_log = transcript_log
        # Called when an adapter emits SessionEstablished — the supervisor
        # persists the provider session/thread ID so the same conversation
        # can be resumed on the next reconnect. Default is a no-op so
        # tests that only exercise event flow don't need a workstore.
        self._set_session_id = set_session_id
        self._states: dict[str, _AgentState] = {}
        self._registry_lock = asyncio.Lock()

    async def start_agent(
        self,
        work_slug: str,
        agent_slug: str,
        adapter: AgentAdapter,
        context: AgentStartContext,
        *,
        first_message: str | None = None,
    ) -> None:
        # Seed seq from any pre-existing transcript so resume continues
        # monotonically — restarting at 0 would let new events collide
        # with on-disk history. last_seq is a tail-read; cheap.
        seed_seq = await asyncio.to_thread(
            self._transcript_log.last_seq, work_slug, agent_slug
        )
        async with self._registry_lock:
            if agent_slug in self._states:
                raise RuntimeError(f"agent already running: {agent_slug}")
            state = _AgentState(
                work_slug=work_slug,
                agent_slug=agent_slug,
                adapter=adapter,
                seq=seed_seq,
            )
            self._states[agent_slug] = state
        await adapter.start(context)
        # Inject the synthesised contexts message before the event task
        # starts: the user_input transcript line lands first; the SDK
        # sees the input; events flow afterwards. Resume callers pass
        # ``first_message=None`` because the SDK session already
        # includes the original turn.
        if first_message is not None:
            await self.send_input(agent_slug, first_message)
        state.task = asyncio.create_task(self._run_agent(state), name=f"agent-{agent_slug}")

    async def send_input(self, agent_slug: str, text: str) -> None:
        state = self._require_state(agent_slug)
        await self._publish(
            state,
            {
                "type": "user_input",
                "ts": datetime.now(UTC).isoformat(),
                "text": text,
            },
        )
        await state.adapter.send_input(text)

    async def resolve_permission(
        self, agent_slug: str, request_id: str, decision: PermissionDecisionValue
    ) -> None:
        # Routes a user's allow/deny decision back to the adapter that
        # raised the prompt. The adapter emits ``PermissionDecision``
        # itself once the future resolves, so no transcript write here —
        # the publish path stays the same as any other adapter event.
        state = self._require_state(agent_slug)
        await state.adapter.resolve_permission(request_id, decision)

    async def stop_turn(self, agent_slug: str) -> None:
        # Records the user's intent in the transcript before forwarding
        # to the adapter, mirroring send_input. Adapters whose SDK can't
        # interrupt mid-turn (Amp today) no-op the second step; the
        # transcript line still lands so the user sees they pressed stop.
        state = self._require_state(agent_slug)
        await self._publish(
            state,
            {"type": "user_stop", "ts": datetime.now(UTC).isoformat()},
        )
        await state.adapter.stop_turn()

    @asynccontextmanager
    async def subscribe(
        self, agent_slug: str
    ) -> AsyncIterator[tuple[int, AgentSubscription]]:
        """Yield ``(from_seq, subscription)`` registered atomically against publish.

        Any event with ``seq <= from_seq`` is already on disk; any event
        with ``seq > from_seq`` lands in ``subscription.queue``. The caller
        is responsible for replaying disk-side events between its cursor
        and ``from_seq`` before draining the queue.

        The queue is bounded; on overflow ``subscription.kicked`` fires and
        the slot is cleared. Callers must monitor ``kicked`` alongside
        the queue and close the upstream connection if it fires.

        A second subscribe to the same agent replaces the slot; the
        previous subscription is abandoned (its WS is presumed dead).
        Cleanup only clears the slot if it still points at our
        subscription, so a stale ``finally`` can't disturb a fresh
        subscriber.
        """
        state = self._require_state(agent_slug)
        subscription = AgentSubscription(
            queue=asyncio.Queue(maxsize=SUBSCRIBER_QUEUE_MAX),
            kicked=asyncio.Event(),
        )
        async with state.publish_lock:
            from_seq = state.seq
            state.subscriber = subscription
        try:
            yield from_seq, subscription
        finally:
            async with state.publish_lock:
                if state.subscriber is subscription:
                    state.subscriber = None

    async def stop_agent(self, agent_slug: str) -> None:
        async with self._registry_lock:
            state = self._states.pop(agent_slug, None)
        if state is None:
            return
        if state.task is not None:
            state.task.cancel()
            with suppress(asyncio.CancelledError):
                await state.task
        await state.adapter.close()

    async def shutdown(self) -> None:
        async with self._registry_lock:
            slugs = list(self._states.keys())
        for slug in slugs:
            await self.stop_agent(slug)

    def get_work_slug_for(self, agent_slug: str) -> str | None:
        """Return the work slug for a running agent, or None if unknown.

        Used by the WS handler to resolve the transcript path for replay
        before subscribing.
        """
        state = self._states.get(agent_slug)
        return state.work_slug if state else None

    # -- internals --

    async def _run_agent(self, state: _AgentState) -> None:
        try:
            async for event in state.adapter.events():
                if isinstance(event, SessionEstablished):
                    await asyncio.to_thread(
                        self._set_session_id, state.agent_slug, event.session_id
                    )
                await self._publish(state, _event_to_dict(event))
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            _log.exception("agent task crashed for %s", state.agent_slug)
            await self._publish(
                state,
                {
                    "type": "error",
                    "ts": datetime.now(UTC).isoformat(),
                    "message": f"adapter task crashed: {exc!r}",
                },
            )

    async def _publish(self, state: _AgentState, payload: dict[str, Any]) -> None:
        async with state.publish_lock:
            state.seq += 1
            stamped: dict[str, Any] = {"seq": state.seq, **payload}
            # NDJSON append + fsync is sync; bridge to the asyncio loop.
            await asyncio.to_thread(
                self._transcript_log.append,
                state.work_slug,
                state.agent_slug,
                stamped,
            )
            sub = state.subscriber
            if sub is None:
                return
            try:
                sub.queue.put_nowait(stamped)
            except asyncio.QueueFull:
                # Slow consumer — drop the subscriber. The disk has the
                # event; reconnect with ?cursor=N picks it up.
                _log.warning(
                    "dropping slow WS subscriber for agent=%s at seq=%d",
                    state.agent_slug,
                    state.seq,
                )
                sub.kicked.set()
                state.subscriber = None

    def _require_state(self, agent_slug: str) -> _AgentState:
        state = self._states.get(agent_slug)
        if state is None:
            raise ValueError(f"agent not running: {agent_slug}")
        return state


def _event_to_dict(event: AgentEvent) -> dict[str, Any]:
    """Flatten a frozen variant into a JSON-friendly dict, ts as ISO-8601."""
    d = dataclasses.asdict(event)
    d["ts"] = event.ts.isoformat()
    return d


__all__ = ["SUBSCRIBER_QUEUE_MAX", "AgentSubscription", "AgentSupervisorService"]
