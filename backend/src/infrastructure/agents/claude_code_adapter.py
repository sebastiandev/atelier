"""Claude Agent SDK adapter.

Wraps ``ClaudeSDKClient`` from ``claude-agent-sdk`` so the supervisor can
drive it through the project's ``AgentAdapter`` Protocol. Maps native
``ContentBlock`` types (TextBlock, ThinkingBlock, ToolUseBlock,
ToolResultBlock) onto our normalised ``AgentEvent`` union, and bridges
``send_input`` → ``query()`` through an internal asyncio queue so the
agent session stays alive across multiple turns.

Lifecycle:
  start()       — connect ClaudeSDKClient (no first prompt sent)
  events()      — async generator: drain ``_outgoing`` (fed by a side
                  pump task that forwards SDK responses + permission
                  events). The decoupling matters because the SDK's
                  ``can_use_tool`` callback runs inline in the response
                  loop and awaits a future; if ``events()`` fed off the
                  response iterator directly, the ``PermissionRequest``
                  emitted by the callback could never reach the
                  supervisor before the future resolved.
  send_input(t) — enqueue user text; the pump consumes and forwards
  resolve_permission(rid, decision) — answers an open ``can_use_tool``
                  by setting the corresponding future
  close()       — disconnect SDK; idempotent (sentinel unblocks events())

Auth: the underlying SDK shells out to the local Claude Code CLI, which
in turn reads ``ANTHROPIC_API_KEY`` from the environment when no other
credentials are configured. Phase 7 wires this through the Settings
object for the dev .env.local flow.
"""

import asyncio
import json
import uuid
from collections.abc import AsyncIterator, Iterable
from contextlib import suppress
from datetime import UTC, datetime
from typing import Any

from claude_agent_sdk import (
    AssistantMessage,
    ClaudeAgentOptions,
    ClaudeSDKClient,
    PermissionResultAllow,
    PermissionResultDeny,
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
    PermissionDecision,
    PermissionDecisionValue,
    PermissionRequest,
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
        self._outgoing: asyncio.Queue[AgentEvent | object] = asyncio.Queue()
        self._closed = False
        # Track the SDK session: the value passed to ``resume`` (if any),
        # the value last seen on a ResultMessage, and a warning to surface
        # the first time ``events()`` runs if a requested resume failed.
        self._resume_session_id: str | None = None
        self._reported_session_id: str | None = None
        self._pending_warning: str | None = None
        # Permission callback state. ``_pending`` holds open futures keyed
        # by request_id; ``_allow_always`` is a session-only set of tool
        # names the user has chosen to auto-allow for the rest of this
        # adapter's lifetime (cleared on close).
        self._pending: dict[str, asyncio.Future[PermissionDecisionValue]] = {}
        self._allow_always: set[str] = set()
        self._pump_task: asyncio.Task[None] | None = None

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
        # If a permission prompt is open, the user pressing Esc means
        # "abort the turn" — deny any in-flight permission requests so
        # the SDK callback can return cleanly before the interrupt lands.
        for fut in list(self._pending.values()):
            if not fut.done():
                fut.set_result("deny")
        try:
            await self._client.interrupt()
        except Exception:
            # Underlying transport could be in a transient bad state
            # (e.g. between turns); the next send_input will fail loudly
            # if there's a real problem. No need to raise here.
            pass

    async def resolve_permission(
        self, request_id: str, decision: PermissionDecisionValue
    ) -> None:
        fut = self._pending.get(request_id)
        if fut is None or fut.done():
            # Stale or duplicate decision; safe to ignore. A stale frame
            # can arrive if the WS reconnects mid-prompt and the user
            # re-clicks before the replay catches up.
            return
        fut.set_result(decision)

    async def events(self) -> AsyncIterator[AgentEvent]:
        if self._client is None:
            raise RuntimeError("events() called before start()")
        if self._pending_warning is not None:
            yield Error(ts=datetime.now(UTC), message=self._pending_warning)
            self._pending_warning = None
        # Spawn the pump that forwards SDK responses + permission events
        # into ``_outgoing``. Decoupling the response iterator from the
        # ``yield`` site is what lets ``can_use_tool`` (which runs inline
        # in the SDK's response loop) emit a ``PermissionRequest`` and
        # then await a future without starving the supervisor.
        self._pump_task = asyncio.create_task(
            self._run_input_pump(), name="claude-input-pump"
        )
        try:
            while True:
                item = await self._outgoing.get()
                if item is _SHUTDOWN:
                    return
                yield item  # type: ignore[misc]
        finally:
            if self._pump_task is not None and not self._pump_task.done():
                self._pump_task.cancel()
                with suppress(asyncio.CancelledError):
                    await self._pump_task

    async def _run_input_pump(self) -> None:
        assert self._client is not None
        while True:
            text = await self._user_inputs.get()
            if text is _SHUTDOWN:
                await self._outgoing.put(_SHUTDOWN)
                return
            assert isinstance(text, str)
            await self._outgoing.put(StatusChange(ts=datetime.now(UTC), status="thinking"))
            try:
                await self._client.query(text)
                async for msg in self._client.receive_response():
                    sid = _session_id_of(msg)
                    if sid is not None and sid != self._reported_session_id:
                        self._reported_session_id = sid
                        await self._outgoing.put(
                            SessionEstablished(ts=datetime.now(UTC), session_id=sid)
                        )
                    for ev in _convert(msg, model=self._config.model.value):
                        await self._outgoing.put(ev)
            except Exception as e:
                await self._outgoing.put(Error(ts=datetime.now(UTC), message=str(e)))
                await self._outgoing.put(
                    StatusChange(ts=datetime.now(UTC), status="idle")
                )

    async def _can_use_tool(
        self,
        tool_name: str,
        tool_input: dict[str, Any],
        _context: object,
    ) -> PermissionResultAllow | PermissionResultDeny:
        """SDK callback. Gates each non-allowlisted tool through the user.

        Auto-allows tools the user previously said "always allow" to in
        this session — short-circuits without emitting an event so the
        transcript stays clean.
        """
        if tool_name in self._allow_always:
            return PermissionResultAllow()
        request_id = uuid.uuid4().hex
        loop = asyncio.get_running_loop()
        fut: asyncio.Future[PermissionDecisionValue] = loop.create_future()
        self._pending[request_id] = fut
        await self._outgoing.put(
            PermissionRequest(
                ts=datetime.now(UTC),
                request_id=request_id,
                tool_name=tool_name,
                tool_input=dict(tool_input),
            )
        )
        try:
            try:
                decision = await fut
            except asyncio.CancelledError:
                # Turn was cancelled (Esc / supervisor shutdown). Treat
                # as deny so the SDK doesn't run the tool.
                decision = "deny"
        finally:
            self._pending.pop(request_id, None)
        await self._outgoing.put(
            PermissionDecision(
                ts=datetime.now(UTC), request_id=request_id, decision=decision
            )
        )
        if decision == "allow_always":
            self._allow_always.add(tool_name)
        if decision in ("allow", "allow_always"):
            return PermissionResultAllow()
        return PermissionResultDeny(message="user denied", interrupt=False)

    async def close(self) -> None:
        if self._closed:
            return
        self._closed = True
        # Resolve any in-flight permission prompts so the SDK callback
        # can return; otherwise disconnect would block on a hung future.
        for fut in list(self._pending.values()):
            if not fut.done():
                fut.set_result("deny")
        # Unblock events() / pump if they're waiting on the queue.
        await self._user_inputs.put(_SHUTDOWN)
        if self._client is not None:
            await self._client.disconnect()

    def _build_options(self) -> ClaudeAgentOptions:
        kwargs: dict[str, Any] = {
            "model": self._config.model.value,
            "system_prompt": self._config.common.system_prompt,
            "cwd": str(self._config.common.workdir),
            "permission_mode": self._config.permission_mode.value,
            # Conservative auto-allow list (read-only research tools).
            # Anything outside this set flows through ``can_use_tool``.
            "allowed_tools": list(self._config.allowed_tools),
            "can_use_tool": self._can_use_tool,
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
