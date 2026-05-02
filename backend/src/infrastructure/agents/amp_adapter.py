"""Amp adapter — stub-backed placeholder.

Real Sourcegraph Amp SDK integration is deferred. For now the adapter
exercises the spec/config/factory wiring end-to-end by delegating to
``StubAgentAdapter`` with a canned demo sequence. When the real Amp
integration lands, this module is the swap point — the public
``AgentAdapter`` shape stays the same.

The canned sequence is the walking-skeleton fixture used to verify the
streaming pipeline without burning Anthropic tokens or requiring an
``AMP_API_KEY``.
"""

from collections.abc import AsyncIterator
from datetime import UTC, datetime

from src.domain.agents import (
    AgentAdapter,
    AgentEvent,
    AgentStartContext,
    AmpAgentConfig,
    MessageComplete,
    MessageDelta,
    StatusChange,
    ToolCall,
    ToolResult,
)
from src.infrastructure.agents.factory import build_adapter
from src.infrastructure.agents.stub_adapter import StubAgentAdapter
from src.settings import Settings


def _demo_events() -> list[AgentEvent]:
    """Canned 15-event sequence, replayed by the stub backing."""
    now = datetime.now(UTC)
    return [
        StatusChange(ts=now, status="thinking"),
        MessageDelta(ts=now, text="Hello! I'm "),
        MessageDelta(ts=now, text="a stub agent "),
        MessageDelta(ts=now, text="for the walking-skeleton."),
        MessageComplete(ts=now, text="Hello! I'm a stub agent for the walking-skeleton."),
        StatusChange(ts=now, status="thinking"),
        MessageDelta(ts=now, text="Let me try a tool call."),
        MessageComplete(ts=now, text="Let me try a tool call."),
        ToolCall(
            ts=now,
            tool_id="t-1",
            name="read_file",
            arguments={"path": "~/notes.md"},
        ),
        ToolResult(
            ts=now,
            tool_id="t-1",
            content="(simulated) file not found",
        ),
        MessageDelta(ts=now, text="Got "),
        MessageDelta(ts=now, text="the result. "),
        MessageDelta(ts=now, text="That's all for now."),
        MessageComplete(ts=now, text="Got the result. That's all for now."),
        StatusChange(ts=now, status="idle"),
    ]


class AmpAdapter:
    """Stub-backed Amp adapter. Real SDK integration tracked separately."""

    def __init__(self, config: AmpAgentConfig, *, delay_seconds: float = 0.0) -> None:
        self._config = config
        self._inner = StubAgentAdapter(
            scripted_events=_demo_events(),
            delay_seconds=delay_seconds,
        )

    async def start(self, context: AgentStartContext) -> None:
        await self._inner.start(context)

    async def send_input(self, text: str) -> None:
        await self._inner.send_input(text)

    def events(self) -> AsyncIterator[AgentEvent]:
        return self._inner.events()

    async def close(self) -> None:
        await self._inner.close()


@build_adapter.register
def _build_amp_adapter(config: AmpAgentConfig, settings: Settings) -> AgentAdapter:
    return AmpAdapter(config, delay_seconds=settings.stub_event_delay)


__all__ = ["AmpAdapter"]
