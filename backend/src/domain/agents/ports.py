"""Port for agent adapters.

`AgentAdapter` is the boundary between the supervisor and a concrete
provider SDK (Claude Agent SDK, Amp, Codex, plus the stub used in tests).
Each adapter normalises native event shapes into the `AgentEvent` union.

Async by design — this is one of the three places (WS, supervisor, SDK
adapter) where the system is forced async by an upstream SDK iterator.
The rest of the stack stays sync; bridging happens via `asyncio.to_thread`.
"""

from collections.abc import AsyncIterator
from dataclasses import dataclass
from pathlib import Path
from typing import Protocol

from src.domain.agents.events import AgentEvent


@dataclass(frozen=True)
class AgentStartContext:
    """Bundle the adapter needs to launch its underlying SDK session.

    ``session_id`` resumes an existing provider session if set: passed as
    ``resume`` to the Claude SDK or ``continue_thread`` to Amp. ``None``
    means start fresh; the adapter emits a ``SessionEstablished`` once
    the provider assigns one.
    """

    workdir: Path
    context_md: str
    model: str
    system_prompt: str
    session_id: str | None = None


class AgentAdapter(Protocol):
    """Async lifecycle + event stream for one agent.

    Order of operations a caller must respect:
      1. ``await start(ctx)`` exactly once
      2. iterate ``events()`` (and concurrently call ``send_input(...)``)
      3. ``await close()`` exactly once

    ``close()`` must be idempotent — the supervisor calls it during
    graceful shutdown and again in error paths.
    """

    async def start(self, context: AgentStartContext) -> None: ...

    async def send_input(self, text: str) -> None: ...

    def events(self) -> AsyncIterator[AgentEvent]: ...

    async def close(self) -> None: ...


__all__ = ["AgentAdapter", "AgentStartContext"]
