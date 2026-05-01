"""Scripted adapter for tests.

Holds an explicit list of `AgentEvent` and replays them as the async
event stream. `start`, `send_input`, and `close` mark observable state
so tests can assert lifecycle ordering. The contract test suite
(STORY-007) parametrises against this and any real adapter.
"""

from collections.abc import AsyncIterator

from src.domain.agents.events import AgentEvent
from src.domain.agents.ports import AgentStartContext


class StubAgentAdapter:
    def __init__(self, scripted_events: list[AgentEvent]) -> None:
        self._scripted = list(scripted_events)
        self.start_context: AgentStartContext | None = None
        self.received_inputs: list[str] = []
        self.closed: bool = False

    async def start(self, context: AgentStartContext) -> None:
        if self.start_context is not None:
            raise RuntimeError("start() called twice")
        self.start_context = context

    async def send_input(self, text: str) -> None:
        self.received_inputs.append(text)

    async def events(self) -> AsyncIterator[AgentEvent]:
        for event in self._scripted:
            yield event

    async def close(self) -> None:
        # Idempotent — safe to call repeatedly.
        self.closed = True


__all__ = ["StubAgentAdapter"]
