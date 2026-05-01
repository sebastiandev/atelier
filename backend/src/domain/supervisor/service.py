"""AgentSupervisorService — owns one asyncio.Task per running agent.

The task drains the adapter's event stream. Each event is:
  1. stamped with a per-agent monotonic ``seq`` (starting at 1)
  2. converted to a flat dict ``{seq, ts, type, ...payload}``
  3. appended + fsynced to the transcript via ``TranscriptLog``
  4. fanned out to subscriber queues

The "before fan-out" ordering is the load-bearing invariant: a subscriber
can never receive an event that isn't already on disk.

User input takes the same path. ``send_input`` writes a ``user_input``
line to the transcript with a stamped seq before forwarding to
``adapter.send_input``, so the transcript is the single ordered source
of truth for both sides of the conversation.

Subscribers receive only events stamped after they registered. The
``subscribe`` context manager returns ``(from_seq, queue)`` so callers
(typically the WS handler) can read ``transcript.ndjson`` for events
``cursor < seq <= from_seq`` and then drain ``queue`` for the rest with
``seq > from_seq`` — no duplicates, no gaps.
"""

from __future__ import annotations

import asyncio
import dataclasses
import logging
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager, suppress
from datetime import UTC, datetime
from typing import Any

from src.domain.agents import AgentAdapter, AgentEvent, AgentStartContext
from src.domain.workstore.ports import TranscriptLog

_log = logging.getLogger(__name__)


@dataclasses.dataclass
class _AgentState:
    work_slug: str
    agent_slug: str
    adapter: AgentAdapter
    seq: int = 0
    task: asyncio.Task[None] | None = None
    publish_lock: asyncio.Lock = dataclasses.field(default_factory=asyncio.Lock)
    subscribers: list[asyncio.Queue[dict[str, Any]]] = dataclasses.field(default_factory=list)


class AgentSupervisorService:
    def __init__(self, transcript_log: TranscriptLog) -> None:
        self._transcript_log = transcript_log
        self._states: dict[str, _AgentState] = {}
        self._registry_lock = asyncio.Lock()

    async def start_agent(
        self,
        work_slug: str,
        agent_slug: str,
        adapter: AgentAdapter,
        context: AgentStartContext,
    ) -> None:
        async with self._registry_lock:
            if agent_slug in self._states:
                raise RuntimeError(f"agent already running: {agent_slug}")
            state = _AgentState(work_slug=work_slug, agent_slug=agent_slug, adapter=adapter)
            self._states[agent_slug] = state
        await adapter.start(context)
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

    @asynccontextmanager
    async def subscribe(
        self, agent_slug: str
    ) -> AsyncIterator[tuple[int, asyncio.Queue[dict[str, Any]]]]:
        """Yield ``(from_seq, queue)`` registered atomically against publish.

        Any event with ``seq <= from_seq`` is already on disk; any event
        with ``seq > from_seq`` lands in ``queue``. The caller is
        responsible for replaying disk-side events between its cursor and
        ``from_seq`` before draining the queue.
        """
        state = self._require_state(agent_slug)
        queue: asyncio.Queue[dict[str, Any]] = asyncio.Queue()
        async with state.publish_lock:
            from_seq = state.seq
            state.subscribers.append(queue)
        try:
            yield from_seq, queue
        finally:
            async with state.publish_lock:
                with suppress(ValueError):
                    state.subscribers.remove(queue)

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
            for queue in list(state.subscribers):
                queue.put_nowait(stamped)

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


__all__ = ["AgentSupervisorService"]
