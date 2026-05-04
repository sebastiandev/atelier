"""Scripted adapter for tests and the walking-skeleton dev demo.

Holds an explicit list of `AgentEvent` and replays them as the async
event stream. `start`, `send_input`, and `close` mark observable state
so tests can assert lifecycle ordering. The contract test suite
(STORY-007) parametrises against this and any real adapter.

`delay_seconds` lets the dev demo space events out so the streaming UX
is visible in the browser; tests leave it at 0 for determinism.
"""

import asyncio
import dataclasses
from collections.abc import AsyncIterator
from datetime import UTC, datetime

from src.domain.agents.events import AgentEvent
from src.domain.agents.ports import AgentStartContext


class StubAgentAdapter:
    def __init__(
        self,
        scripted_events: list[AgentEvent],
        *,
        delay_seconds: float = 0.0,
    ) -> None:
        self._scripted = list(scripted_events)
        self._delay = delay_seconds
        self.start_context: AgentStartContext | None = None
        self.received_inputs: list[str] = []
        self.stop_turn_calls: int = 0
        self.closed: bool = False

    async def start(self, context: AgentStartContext) -> None:
        if self.start_context is not None:
            raise RuntimeError("start() called twice")
        self.start_context = context

    async def send_input(self, text: str) -> None:
        self.received_inputs.append(text)

    async def stop_turn(self) -> None:
        # No-op for the stub: it doesn't model an in-flight turn. Tests
        # that care about the contract observe ``stop_turn_calls``.
        self.stop_turn_calls += 1

    async def events(self) -> AsyncIterator[AgentEvent]:
        for event in self._scripted:
            if self._delay > 0:
                # Demo mode: space events out and restamp `ts` to the actual
                # emit time. Tests use the default delay=0 path and keep the
                # caller-supplied timestamps for deterministic assertions.
                await asyncio.sleep(self._delay)
                yield dataclasses.replace(event, ts=datetime.now(UTC))
            else:
                yield event

    async def close(self) -> None:
        # Idempotent — safe to call repeatedly.
        self.closed = True


__all__ = ["StubAgentAdapter"]
