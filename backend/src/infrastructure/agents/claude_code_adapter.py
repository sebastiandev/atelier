"""Claude Agent SDK adapter.

Wraps ``ClaudeSDKClient`` from ``claude-agent-sdk`` so the supervisor can
drive it through the project's ``AgentAdapter`` Protocol. Maps native
``ContentBlock`` types (TextBlock, ThinkingBlock, ToolUseBlock,
ToolResultBlock) onto our normalised ``AgentEvent`` union, and bridges
``send_input`` → ``query()`` through an internal asyncio queue so the
agent session stays alive across multiple turns.

Lifecycle:
  start()       — connect ClaudeSDKClient (no first prompt sent)
  events()      — async generator: drain user-input queue, send query,
                  yield converted events for each turn, then loop
  send_input(t) — enqueue user text; events() consumes and forwards
  close()       — disconnect SDK; idempotent (sentinel unblocks events())

Auth: the underlying SDK shells out to the local Claude Code CLI, which
in turn reads ``ANTHROPIC_API_KEY`` from the environment when no other
credentials are configured. Phase 7 wires this through the Settings
object for the dev .env.local flow.
"""

import asyncio
import json
from collections.abc import AsyncIterator, Iterable
from datetime import UTC, datetime
from typing import Any

from claude_agent_sdk import (
    AssistantMessage,
    ClaudeAgentOptions,
    ClaudeSDKClient,
    ResultMessage,
    TextBlock,
    ThinkingBlock,
    ToolResultBlock,
    ToolUseBlock,
)

from src.domain.agents import (
    AgentAdapter,
    AgentEvent,
    AgentStartContext,
    ClaudeAgentConfig,
    ClaudeEffort,
    Error,
    MessageComplete,
    StatusChange,
    ThinkingComplete,
    ToolCall,
    ToolResult,
)
from src.infrastructure.agents.factory import build_adapter
from src.settings import Settings

_SHUTDOWN = object()  # sentinel pushed onto the queue by close()


class ClaudeCodeAdapter:
    """Adapter that streams a Claude Agent session as AgentEvents."""

    def __init__(self, config: ClaudeAgentConfig) -> None:
        self._config = config
        self._client: ClaudeSDKClient | None = None
        self._user_inputs: asyncio.Queue[str | object] = asyncio.Queue()
        self._closed = False

    async def start(self, context: AgentStartContext) -> None:
        # ``context`` is part of the Protocol contract but the adapter is
        # already fully configured via ``ClaudeAgentConfig`` at construction
        # time, so we ignore it (the route folds the same fields into the
        # config before calling build_adapter). Phase 6 collapses this seam.
        if self._client is not None:
            raise RuntimeError("start() called twice")
        self._client = ClaudeSDKClient(options=self._build_options())
        await self._client.connect()

    async def send_input(self, text: str) -> None:
        await self._user_inputs.put(text)

    async def events(self) -> AsyncIterator[AgentEvent]:
        if self._client is None:
            raise RuntimeError("events() called before start()")
        while True:
            text = await self._user_inputs.get()
            if text is _SHUTDOWN:
                return
            assert isinstance(text, str)
            yield StatusChange(ts=datetime.now(UTC), status="thinking")
            try:
                await self._client.query(text)
                async for msg in self._client.receive_response():
                    for ev in _convert(msg):
                        yield ev
            except Exception as e:  # noqa: BLE001
                yield Error(ts=datetime.now(UTC), message=str(e))
                yield StatusChange(ts=datetime.now(UTC), status="idle")

    async def close(self) -> None:
        if self._closed:
            return
        self._closed = True
        # Unblock events() if it's waiting on the queue.
        await self._user_inputs.put(_SHUTDOWN)
        if self._client is not None:
            await self._client.disconnect()

    def _build_options(self) -> ClaudeAgentOptions:
        kwargs: dict[str, Any] = {
            "model": self._config.model.value,
            "system_prompt": self._config.common.system_prompt,
            "cwd": str(self._config.common.workdir),
            "permission_mode": self._config.permission_mode.value,
        }
        if self._config.thinking_effort is not ClaudeEffort.OFF:
            kwargs["effort"] = self._config.thinking_effort.value
        return ClaudeAgentOptions(**kwargs)


def _convert(msg: object) -> Iterable[AgentEvent]:
    """Map a Claude SDK Message onto our AgentEvent union."""
    now = datetime.now(UTC)
    if isinstance(msg, AssistantMessage):
        for block in msg.content:
            if isinstance(block, TextBlock):
                yield MessageComplete(ts=now, text=block.text)
            elif isinstance(block, ThinkingBlock):
                yield ThinkingComplete(ts=now, text=block.thinking)
            elif isinstance(block, ToolUseBlock):
                yield ToolCall(
                    ts=now,
                    tool_id=block.id,
                    name=block.name,
                    arguments=block.input,
                )
            elif isinstance(block, ToolResultBlock):
                yield ToolResult(
                    ts=now,
                    tool_id=block.tool_use_id,
                    content=_stringify(block.content),
                    is_error=bool(block.is_error),
                )
            # ServerToolUseBlock / ServerToolResultBlock: emitted by remote
            # MCP tools; not mapped yet — tracked for a follow-up story.
    elif isinstance(msg, ResultMessage):
        if msg.is_error:
            err = msg.result or (msg.errors[0] if msg.errors else None) or "(unknown error)"
            yield Error(ts=now, message=err)
        yield StatusChange(ts=now, status="idle")


def _stringify(content: str | list[dict[str, Any]] | None) -> str:
    if content is None:
        return ""
    if isinstance(content, str):
        return content
    return json.dumps(content)


@build_adapter.register
def _build_claude_adapter(
    config: ClaudeAgentConfig, settings: Settings
) -> AgentAdapter:
    return ClaudeCodeAdapter(config)


__all__ = ["ClaudeCodeAdapter"]
