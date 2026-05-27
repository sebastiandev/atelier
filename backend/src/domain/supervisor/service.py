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
    │   subscribe (e.g., reconnect race) replaces the slot and kicks  │
    │   the stale socket. publish puts events into the current queue. │
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
    ArtifactMarker,
    PermissionDecisionValue,
    SessionEstablished,
)
from src.domain.models import Artifact
from src.domain.workstore.ports import TranscriptLog

SetSessionIdFn = Callable[[str, str], None]
RecordArtifactFn = Callable[[str, str, dict[str, Any]], Artifact]

_log = logging.getLogger(__name__)


# Bound for the per-subscription queue. The supervisor stamps events as
# fast as the adapter emits them; the WS handler drains them at network
# speed. If a subscriber falls this far behind we cut it loose rather
# than grow memory unbounded — disk has every event, so the client can
# reconnect with ?cursor=N and resume.
SUBSCRIBER_QUEUE_MAX = 256


class AgentTerminated(RuntimeError):
    """Raised by ``send_input`` when the agent's event pump has ended
    (subprocess died, upstream 429, provider stream EOF) but the
    eviction-after-pump-end cleanup hasn't completed yet. The WS layer
    catches this and closes the socket so the FE reconnects, which
    triggers a fresh resume + adapter build against the same session_id.
    """


@dataclasses.dataclass
class AgentSubscription:
    """Handle returned by ``AgentSupervisorService.subscribe``.

    Streams every event with ``seq > cursor`` exactly once, in order:
    disk-replay events first (everything on disk at subscribe time that
    the caller hasn't seen yet), then live events from the queue.

    ``queue`` is bounded to ``SUBSCRIBER_QUEUE_MAX``. If publishing
    overflows it (slow consumer), the supervisor sets ``kicked`` and
    drops the subscriber slot. Callers should monitor ``kicked``
    alongside iteration of ``stream()`` and close the upstream
    connection if ``kicked`` fires.
    """

    queue: asyncio.Queue[dict[str, Any]]
    kicked: asyncio.Event
    # Disk-replay events captured at subscribe time, ordered by seq.
    # Yielded by ``stream()`` before the live queue. Populated under the
    # publish lock so it never overlaps with what's about to land in the
    # queue.
    replay: list[dict[str, Any]] = dataclasses.field(default_factory=list)

    async def stream(self) -> AsyncIterator[dict[str, Any]]:
        """Yield every event exactly once, in order: replay then live."""
        for event in self.replay:
            yield event
        while True:
            yield await self.queue.get()


@dataclasses.dataclass
class _AgentState:
    work_slug: str
    agent_slug: str
    adapter: AgentAdapter
    # Held until the first ``send_input`` triggers ``adapter.start`` and
    # the events-pump task. Retaining it on the state lets the supervisor
    # spawn lazily so a re-attach (or any "open agent from rail" flow)
    # doesn't fork a new provider session unless the user actually types.
    context: AgentStartContext
    started: bool = False
    seq: int = 0
    task: asyncio.Task[None] | None = None
    publish_lock: asyncio.Lock = dataclasses.field(default_factory=asyncio.Lock)
    # Atelier is single-user single-browser: at most one WS subscriber per
    # agent. A second subscribe (e.g., reconnect-before-cleanup) replaces
    # the slot and kicks the previous subscription so a stale-but-open WS
    # cannot keep sending input while no longer receiving live events.
    subscriber: AgentSubscription | None = None


class AgentSupervisorService:
    def __init__(
        self,
        transcript_log: TranscriptLog,
        set_session_id: SetSessionIdFn = lambda _slug, _sid: None,
        record_artifact: RecordArtifactFn | None = None,
    ) -> None:
        self._transcript_log = transcript_log
        # Called when an adapter emits SessionEstablished — the supervisor
        # persists the provider session/thread ID so the same conversation
        # can be resumed on the next reconnect. Default is a no-op so
        # tests that only exercise event flow don't need a workstore.
        self._set_session_id = set_session_id
        # Called when an adapter emits ArtifactMarker — sync callable
        # injected at boot. None (default) means the supervisor still
        # writes the marker to disk and fans it out, but doesn't persist
        # an Artifact row. Tests that don't care about artifact recording
        # leave it unset.
        self._record_artifact = record_artifact
        self._states: dict[str, _AgentState] = {}
        self._registry_lock = asyncio.Lock()

    async def register_agent(
        self,
        work_slug: str,
        agent_slug: str,
        adapter: AgentAdapter,
        context: AgentStartContext,
        *,
        lazy: bool = False,
    ) -> None:
        """Register an agent with the supervisor.

        Steps:
          1. Seed ``seq`` from the on-disk transcript so resume continues
             monotonically — restarting at 0 would let new events collide
             with on-disk history. ``last_seq`` is a tail-read; cheap.
          2. Insert the agent into the registry (under ``_registry_lock``).
          3. Call ``adapter.start(context)`` — cheap per-adapter setup
             (Amp's permission socket, Claude's SDK connect).
          4. If ``lazy`` is False (default): create the events-pump task
             that iterates ``adapter.events()`` and ferries each frame
             through ``_publish``. If True: skip — ``send_input`` creates
             the pump on first call. Lazy is for re-attach (``resume``)
             on providers that fork-on-resume (Amp's
             ``--execute --stream-json``); a view-only re-attach should
             not burn a fork.

        Raises ``RuntimeError`` if the agent is already registered. The
        caller (``connect`` / ``start`` / ``resume``) handles the race —
        a concurrent caller that won drops its adapter copy.
        """
        seed_seq = await asyncio.to_thread(
            self._transcript_log.last_seq, work_slug, agent_slug
        )
        async with self._registry_lock:
            if agent_slug in self._states:
                raise RuntimeError(f"agent already registered: {agent_slug}")
            state = _AgentState(
                work_slug=work_slug,
                agent_slug=agent_slug,
                adapter=adapter,
                context=context,
                seq=seed_seq,
            )
            self._states[agent_slug] = state
        # If ``adapter.start`` raises, evict the half-registered state
        # before propagating: otherwise the slot stays occupied with a
        # dead adapter, subsequent ``connect``s see ``is_registered ==
        # True`` and skip resume entirely, and the agent looks "stuck
        # thinking" on the FE forever. The cleanup path also closes the
        # adapter so any partial setup (Amp's permission socket, tempdir,
        # bridge wrapper) doesn't leak.
        try:
            await adapter.start(context)
        except BaseException:
            async with self._registry_lock:
                self._states.pop(agent_slug, None)
            with suppress(Exception):
                await adapter.close()
            raise
        state.started = True
        # Stale permission cleanup: a permission_request whose decision
        # never landed (backend crashed mid-prompt, adapter died on
        # resume, tile closed while waiting) becomes a zombie prompt
        # the FE re-derives from the transcript on every reload — and
        # the user's "Allow" click can't reach any live future because
        # the original adapter is gone. Publish a synthetic ``deny``
        # for every orphan so the prompt clears and the agent can
        # accept new input. Runs BEFORE the pump task so stale
        # decisions land first in seq order.
        await self._clear_stale_permission_requests(state)
        if not lazy:
            state.task = asyncio.create_task(
                self._run_agent(state), name=f"agent-{agent_slug}"
            )

    async def send_input(self, agent_slug: str, text: str) -> None:
        state = self._require_state(agent_slug)
        if state.task is not None and state.task.done():
            # The pump has terminated (subprocess died, upstream 429,
            # provider stream EOF) and the eviction-after-pump-end task
            # is in flight but hasn't won the registry lock yet. Don't
            # write a ``user_input`` event we can't deliver — raise so
            # the WS layer can close the socket; the FE reconnects and
            # the resume path rebuilds the adapter against the same
            # session_id.
            raise AgentTerminated(
                f"agent {agent_slug} pump ended; reconnect to rebuild"
            )
        if state.task is None:
            # Lazy spawn: register_agent deferred the pump (re-attach
            # path). Start it now, before publishing user_input, so the
            # events task is alive to consume what the adapter forwards.
            state.task = asyncio.create_task(
                self._run_agent(state), name=f"agent-{state.agent_slug}"
            )
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
        state = self._states.get(agent_slug)
        if state is None:
            # Stale prompt: backend restarted (or the agent was closed)
            # while the FE was still showing a permission card. The
            # adapter's pending future is gone; routing to it would
            # fail. The auto-cleanup at register_agent already covers
            # the next attach, but the user's click should also clear
            # the prompt right now. Fall through silently: when the
            # agent next resumes, ``_clear_stale_permission_requests``
            # writes the synthetic deny.
            _log.info(
                "resolve_permission for unregistered agent=%s rid=%s — "
                "deferred to next resume",
                agent_slug, request_id,
            )
            return
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
        self, agent_slug: str, cursor: int = 0
    ) -> AsyncIterator[AgentSubscription]:
        """Yield a ``Subscription`` whose ``stream()`` emits every event
        with ``seq > cursor`` exactly once, in order: disk replay first,
        then live events from the queue.

        Atomicity: ``from_seq`` (current high-water seq) is captured under
        the publish lock together with subscriber registration. Any event
        with ``seq <= from_seq`` is already on disk and goes into the
        replay list (filtered to ``seq > cursor``). Any event with
        ``seq > from_seq`` was published after the lock released and is
        delivered live via the queue. No overlap, no gap.

        The queue is bounded; on overflow ``subscription.kicked`` fires
        and the slot is cleared. Callers must monitor ``kicked`` alongside
        ``stream()`` and close the upstream connection if it fires.

        A second subscribe to the same agent replaces the slot and kicks
        the previous subscription. Cleanup only clears the slot if it
        still points at our subscription, so a stale ``finally`` can't
        disturb a fresh subscriber.
        """
        state = self._require_state(agent_slug)
        subscription = AgentSubscription(
            queue=asyncio.Queue(maxsize=SUBSCRIBER_QUEUE_MAX),
            kicked=asyncio.Event(),
        )
        async with state.publish_lock:
            from_seq = state.seq
            previous = state.subscriber
            if previous is not None:
                previous.kicked.set()
            state.subscriber = subscription
        # Disk read is sync + slow; do it outside the publish lock so it
        # doesn't block other publishes. Filter to (cursor, from_seq] —
        # events past from_seq land in the live queue instead, so reading
        # them from disk would create duplicates.
        all_events = await asyncio.to_thread(
            self._read_disk_window, state.work_slug, agent_slug, cursor
        )
        subscription.replay = [
            e for e in all_events if e["seq"] <= from_seq
        ]
        try:
            yield subscription
        finally:
            async with state.publish_lock:
                if state.subscriber is subscription:
                    state.subscriber = None

    def _read_disk_window(
        self, work_slug: str, agent_slug: str, cursor: int
    ) -> list[dict[str, Any]]:
        return list(
            self._transcript_log.read_from_cursor(work_slug, agent_slug, cursor)
        )

    async def stop_agent(self, agent_slug: str) -> None:
        async with self._registry_lock:
            state = self._states.pop(agent_slug, None)
        if state is None:
            return
        if state.subscriber is not None:
            state.subscriber.kicked.set()
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

    def is_registered(self, agent_slug: str) -> bool:
        """True if the supervisor has live state for this agent.

        Used by ``connect`` / ``resume`` to decide whether to rebuild the
        adapter + register, or attach to existing state.
        """
        return agent_slug in self._states

    def is_lazy_registered(self, agent_slug: str) -> bool:
        """True when the agent is registered but its provider pump is idle."""
        state = self._states.get(agent_slug)
        return state is not None and state.task is None

    async def refresh_seq_from_disk(self, agent_slug: str) -> None:
        """Advance a lazy state's replay high-water mark after external
        transcript writes such as CLI catch-up."""
        state = self._states.get(agent_slug)
        if state is None:
            return
        last_seq = await asyncio.to_thread(
            self._transcript_log.last_seq, state.work_slug, agent_slug
        )
        async with state.publish_lock:
            state.seq = max(state.seq, last_seq)

    # -- internals --

    async def _run_agent(self, state: _AgentState) -> None:
        try:
            async for event in state.adapter.events():
                if isinstance(event, SessionEstablished):
                    await asyncio.to_thread(
                        self._set_session_id, state.agent_slug, event.session_id
                    )
                # Marker hits disk via _publish FIRST so a downstream
                # validation failure still leaves a durable record of
                # what the agent emitted.
                await self._publish(state, _event_to_dict(event))
                if isinstance(event, ArtifactMarker) and self._record_artifact:
                    await self._record_marker(state, event)
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
        finally:
            # Pump returned — the adapter is unusable for any further
            # turns (subprocess died, error path drained _outgoing,
            # provider stream EOF). Without this eviction the state
            # lingers with a dead adapter, and the next ``send_input``
            # writes to a closed stdin transport with a cryptic
            # ``WriteUnixTransport closed`` error. Evicting frees the
            # slot so the next ``connect`` builds a fresh adapter via
            # the resume path. Skipped silently if a concurrent
            # ``stop_agent`` / ``shutdown`` already cleaned us up.
            await self._evict_after_pump_end(state)

    async def _evict_after_pump_end(self, state: _AgentState) -> None:
        async with self._registry_lock:
            current = self._states.get(state.agent_slug)
            if current is not state:
                return
            del self._states[state.agent_slug]
        # Kick the live WS subscription (if any) so the handler closes
        # the socket and the FE reconnects — that fresh open lands in
        # ``connect`` → ``resume`` and rebuilds the adapter. Reuses the
        # slow-subscriber kick channel; the close code on the FE side
        # is the same retry-with-backoff path.
        sub = state.subscriber
        if sub is not None:
            sub.kicked.set()
        with suppress(Exception):
            await state.adapter.close()

    async def _clear_stale_permission_requests(
        self, state: _AgentState
    ) -> None:
        """Synthesise a ``deny`` for every permission_request that
        never got a decision recorded.

        Called once at register-time. Builds the orphan set from a
        full transcript read; for each, calls ``_publish`` which
        bumps seq, appends to NDJSON, and broadcasts to any live
        subscriber. Deny (not allow) on purpose: the re-attach is
        automatic — auto-allowing would let a tool call through
        that the user never explicitly approved.
        """
        events = await asyncio.to_thread(
            lambda: list(
                self._transcript_log.read_from_cursor(
                    state.work_slug, state.agent_slug, 0
                )
            )
        )
        requested: dict[str, str | None] = {}
        decided: set[str] = set()
        for ev in events:
            t = ev.get("type")
            rid = ev.get("request_id")
            if not isinstance(rid, str):
                continue
            if t == "permission_request":
                requested[rid] = ev.get("tool_name")
            elif t == "permission_decision":
                decided.add(rid)
        orphans = [(rid, name) for rid, name in requested.items() if rid not in decided]
        if not orphans:
            return
        now = datetime.now(UTC).isoformat()
        for rid, tool_name in orphans:
            _log.info(
                "auto-deny stale permission request agent=%s tool=%s rid=%s",
                state.agent_slug, tool_name, rid,
            )
            await self._publish(
                state,
                {
                    "type": "permission_decision",
                    "ts": now,
                    "request_id": rid,
                    "decision": "deny",
                    "stale": True,
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

    async def _record_marker(
        self, state: _AgentState, event: ArtifactMarker
    ) -> None:
        if self._record_artifact is None:
            return
        try:
            artifact = await asyncio.to_thread(
                self._record_artifact,
                state.work_slug,
                state.agent_slug,
                event.payload,
            )
        except ValueError as exc:
            await self._publish(
                state,
                {
                    "type": "error",
                    "ts": datetime.now(UTC).isoformat(),
                    "message": f"invalid artifact marker: {exc}",
                },
            )
            return
        except Exception as exc:
            _log.exception(
                "artifact tracker failed for agent=%s", state.agent_slug
            )
            await self._publish(
                state,
                {
                    "type": "error",
                    "ts": datetime.now(UTC).isoformat(),
                    "message": f"artifact tracker error: {exc!r}",
                },
            )
            return
        await self._publish(
            state,
            {
                "type": "artifact_recorded",
                "ts": datetime.now(UTC).isoformat(),
                "artifact": _artifact_to_dict(artifact),
            },
        )

    def _require_state(self, agent_slug: str) -> _AgentState:
        state = self._states.get(agent_slug)
        if state is None:
            raise ValueError(f"agent not running: {agent_slug}")
        return state


def _event_to_dict(event: AgentEvent) -> dict[str, Any]:
    """Flatten a frozen variant into a JSON-friendly dict, ts as ISO-8601."""
    d = dataclasses.asdict(event)
    d["ts"] = event.ts.isoformat()
    if d.get("type") == "turn_metrics" and d.get("context_window") is None:
        d.pop("context_window", None)
    return d


def _artifact_to_dict(artifact: Artifact) -> dict[str, Any]:
    """Flatten a typed artifact into a wire-shaped dict.

    Each subtype owns a different subset of the optional fields
    (``url`` / ``repo`` / ``doc_path``); the frontend's
    ``ArtifactSummary`` expects all three keys with ``null`` for the
    ones that don't apply. ``getattr`` keeps this branch-free and
    immune to a future subtype that adds a field but not a method —
    new keys become null without code changes here.
    """
    return {
        "slug": artifact.slug,
        "type": artifact.type,
        "title": artifact.title,
        "status": artifact.status,
        "created_at": artifact.created_at.isoformat(),
        "url": getattr(artifact, "url", None),
        "repo": getattr(artifact, "repo", None),
        "doc_path": getattr(artifact, "doc_path", None),
    }


__all__ = ["SUBSCRIBER_QUEUE_MAX", "AgentSubscription", "AgentSupervisorService"]
