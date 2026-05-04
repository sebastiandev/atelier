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
    SessionEstablished,
    StatusChange,
    ThinkingComplete,
    ToolCall,
    ToolResult,
    TurnMetrics,
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
        # Track the SDK session: the value passed to ``resume`` (if any),
        # the value last seen on a ResultMessage, and a warning to surface
        # the first time ``events()`` runs if a requested resume failed.
        self._resume_session_id: str | None = None
        self._reported_session_id: str | None = None
        self._pending_warning: str | None = None

    async def start(self, context: AgentStartContext) -> None:
        # ``context`` is mostly informational (the adapter is configured
        # via ``ClaudeAgentConfig`` at construction time), but we read
        # ``session_id`` here so a previously-assigned SDK session can be
        # resumed across reconnects / backend restarts.
        if self._client is not None:
            raise RuntimeError("start() called twice")
        self._resume_session_id = context.session_id
        try:
            self._client = ClaudeSDKClient(options=self._build_options())
            await self._client.connect()
        except FileNotFoundError as exc:
            # The on-disk session was wiped (user cleared
            # ~/.claude/projects/...). Drop the resume hint and start fresh
            # — the agent's transcript.ndjson is unaffected, but the SDK
            # has no prior turns to draw on. Surface a warning event on
            # the next iteration of events() so the user knows.
            self._pending_warning = (
                f"Previous session not found ({exc}); started a fresh one."
            )
            self._resume_session_id = None
            self._client = ClaudeSDKClient(options=self._build_options())
            await self._client.connect()

    async def send_input(self, text: str) -> None:
        await self._user_inputs.put(text)

    async def stop_turn(self) -> None:
        # ClaudeSDKClient.interrupt sends a control-protocol message to
        # the CLI; the session stays alive and is ready for the next
        # query. Safe to call when no turn is in flight (the SDK no-ops
        # gracefully). We swallow exceptions so a stop frame can never
        # destabilise the supervisor's stream.
        if self._client is None or self._closed:
            return
        try:
            await self._client.interrupt()
        except Exception:  # noqa: BLE001
            # Underlying transport could be in a transient bad state
            # (e.g. between turns); the next send_input will fail loudly
            # if there's a real problem. No need to raise here.
            pass

    async def events(self) -> AsyncIterator[AgentEvent]:
        if self._client is None:
            raise RuntimeError("events() called before start()")
        if self._pending_warning is not None:
            yield Error(ts=datetime.now(UTC), message=self._pending_warning)
            self._pending_warning = None
        while True:
            text = await self._user_inputs.get()
            if text is _SHUTDOWN:
                return
            assert isinstance(text, str)
            yield StatusChange(ts=datetime.now(UTC), status="thinking")
            try:
                await self._client.query(text)
                async for msg in self._client.receive_response():
                    sid = _session_id_of(msg)
                    if sid is not None and sid != self._reported_session_id:
                        self._reported_session_id = sid
                        yield SessionEstablished(ts=datetime.now(UTC), session_id=sid)
                    for ev in _convert(msg, model=self._config.model.value):
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
        if self._resume_session_id is not None:
            kwargs["resume"] = self._resume_session_id
        return ClaudeAgentOptions(**kwargs)


def _convert(msg: object, *, model: str | None = None) -> Iterable[AgentEvent]:
    """Map a Claude SDK Message onto our AgentEvent union.

    ``model`` lets the adapter stamp the per-turn metrics with its own
    configured model id; the SDK doesn't echo it on ResultMessage.
    """
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
        usage = msg.usage or {}
        yield TurnMetrics(
            ts=now,
            duration_ms=msg.duration_ms,
            input_tokens=int(usage.get("input_tokens", 0) or 0),
            output_tokens=int(usage.get("output_tokens", 0) or 0),
            cache_read_input_tokens=int(usage.get("cache_read_input_tokens", 0) or 0),
            cache_creation_input_tokens=int(
                usage.get("cache_creation_input_tokens", 0) or 0
            ),
            model=model,
        )
        yield StatusChange(ts=now, status="idle")


def _session_id_of(msg: object) -> str | None:
    """Pull the session_id off a Claude SDK message if it has one.

    Both ``ResultMessage`` (always) and ``AssistantMessage`` (sometimes)
    carry a ``session_id`` attribute. Normalised to a single helper so
    the adapter can capture it once per message without caring which
    variant exposed it.
    """
    return getattr(msg, "session_id", None)


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
