"""OpenAI Codex adapter.

Wraps the Codex runtime so the supervisor can drive a Codex session
through the project's ``AgentAdapter`` Protocol. Mirrors the structure
of the Claude and Amp adapters — pump-pattern queues, shared AgentEvent
mapping, and domain-level permission callbacks — adapted to Codex's
JSON-RPC notification shapes.

Lifecycle:
  start()       — open AsyncCodex client, call ``thread_start`` (or
                  ``thread_resume`` when ``context.session_id`` is set).
                  No first turn is kicked off; the first user input drives
                  ``turn_start``.
  events()      — async generator: drain ``_outgoing`` (fed by a side
                  pump task that forwards Codex notifications into the
                  queue). Production uses Codex app-server so approval
                  requests round-trip through Atelier's PermissionRequest
                  UI; the legacy Python SDK wrapper remains as a fallback
                  seam for tests/older runtimes.
  send_input(t) — enqueue user text; pump consumes and forwards
  resolve_permission(rid, decision) — answers an open approval request
                  when tests/future SDKs provide a callback
  stop_turn()   — ``turn.interrupt()`` against the in-flight turn (real
                  cancel, not a no-op like Amp)
  close()       — exit the SDK context (closes the Codex subprocess);
                  idempotent.

Runtime seam: production talks to ``codex app-server`` through a small
JSON-RPC client. Tests inject a fake factory that yields scripted
notifications matching the local Protocols (``CodexClient`` /
``CodexThread`` / ``CodexTurnHandle`` / ``Notification`` defined below).
The older ``openai-codex-sdk`` exec wrapper is kept as a compatibility
client because it shares the same Protocol, but it cannot surface
approvals to Atelier.

Auth: the Codex SDK reads ``OPENAI_API_KEY`` from ``os.environ`` directly.
The lifespan forwards an optional ``Settings.openai_api_key`` into the
environment at startup, mirroring the Anthropic path. The Connections-
backed credential follow-up will replace this with a real ``codex``
connection type.
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import shlex
import sys
import uuid
from collections.abc import AsyncIterator, Callable, Iterable
from contextlib import suppress
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Protocol

from src.domain.agents import (
    AgentAdapter,
    AgentEvent,
    AgentStartContext,
    ArtifactMarker,
    CodexAgentConfig,
    CodexSandbox,
    Error,
    MessageComplete,
    MessageDelta,
    PermissionDecision,
    PermissionDecisionValue,
    PermissionRequest,
    ProviderContextCompacted,
    SessionEstablished,
    StatusChange,
    ThinkingComplete,
    ThinkingDelta,
    ToolCall,
    ToolResult,
    TurnMetrics,
)
from src.infrastructure.agents.atelier_mcp_tools import (
    MCP_SERVER_NAME,
    marker_payload_for_tool,
    scan_text_for_artifact_markers,
)
from src.infrastructure.agents.factory import build_adapter
from src.infrastructure.agents.tool_canonical import canonicalize_tool
from src.settings import Settings

_log = logging.getLogger(__name__)

_SHUTDOWN = object()  # sentinel pushed onto the queue by close()
_APP_SERVER_REQUEST_TIMEOUT_SECONDS = 60.0
_APP_SERVER_STDIO_LIMIT_BYTES = 16 * 1024 * 1024


# ---------------------------------------------------------------------------
# SDK seam — minimal Protocols matching openai-codex-sdk's surface
# ---------------------------------------------------------------------------
#
# The adapter targets these Protocols rather than the SDK's concrete
# types so production can lazy-import the SDK and tests can inject fakes
# without dragging it in. The shapes mirror the SDK as documented in the
# implementation plan (``_bmad-output/research/codex-adapter-plan.md``).


class Notification(Protocol):
    """One element from ``turn.stream()`` — a typed JSON-RPC notification.

    The Codex SDK delivers each frame as a dataclass-like object with a
    string ``type`` discriminator (e.g. ``"item/started"``,
    ``"item/agentMessage/delta"``, ``"turn/completed"``) and an open
    ``params`` payload dict. We don't model every variant statically; the
    adapter dispatches on ``type`` and reads the fields it needs out of
    ``params``.
    """

    type: str
    params: dict[str, Any]


class CodexTurnHandle(Protocol):
    """Handle for a single turn — what ``thread.turn_start`` returns.

    ``stream()`` is an async generator (not a coroutine that returns one),
    so call sites use ``async for n in turn.stream():`` without an extra
    ``await``. Declared without ``async`` here for mypy correctness; the
    actual implementations on the SDK side are async generator functions.
    """

    def stream(self) -> AsyncIterator[Notification]: ...

    async def interrupt(self) -> None: ...


class CodexThread(Protocol):
    """Live thread — what ``client.thread_start`` / ``thread_resume`` returns."""

    @property
    def id(self) -> str: ...

    async def turn_start(self, user_message: str) -> CodexTurnHandle: ...


class CodexClient(Protocol):
    """Async context-managed Codex SDK client."""

    async def __aenter__(self) -> CodexClient: ...

    async def __aexit__(self, exc_type: Any, exc: Any, tb: Any) -> None: ...

    async def thread_start(
        self,
        *,
        model: str,
        cwd: str,
        sandbox: str,
        approval_mode: str,
        base_instructions: str | None = None,
        mcp_servers: dict[str, Any] | None = None,
        config_overrides: dict[str, Any] | None = None,
        additional_directories: list[str] | None = None,
    ) -> CodexThread: ...

    async def thread_resume(
        self,
        thread_id: str,
        *,
        model: str,
        cwd: str,
        sandbox: str,
        approval_mode: str,
        base_instructions: str | None = None,
        mcp_servers: dict[str, Any] | None = None,
        config_overrides: dict[str, Any] | None = None,
        additional_directories: list[str] | None = None,
    ) -> CodexThread: ...

    def on_approval_request(
        self, callback: Callable[[ApprovalRequest], Any]
    ) -> None:
        """Register an async approval callback when an SDK exposes one.

        App-server production and fake tests use this seam. The legacy SDK
        wrapper no-ops it because ``exec --experimental-json`` is one-way.
        """


class ApprovalRequest(Protocol):
    """One server-initiated approval request.

    Codex distinguishes commandExecution approvals from fileChange
    approvals on the wire; we collapse them to a single canonical
    ``(tool_name, tool_input)`` pair via ``tool_canonical.canonicalize_tool``.
    """

    request_id: str
    # Tool name as Codex names it ("Bash" / "exec" / "fileChange" / etc.).
    # The adapter canonicalises before emitting the PermissionRequest.
    tool_name: str
    tool_input: dict[str, Any]


@dataclass
class _AppServerApprovalRequest:
    request_id: str
    tool_name: str
    tool_input: dict[str, Any]
    method: str
    params: dict[str, Any]


ClientFactory = Callable[[], CodexClient]
"""DI seam — tests inject a factory returning a fake CodexClient."""


@dataclass(frozen=True)
class _TokenSnapshot:
    last_prompt_tokens: int
    context_window: int | None = None


TokenSnapshotPoller = Callable[[str], _TokenSnapshot | None]


# ---------------------------------------------------------------------------
# Adapter
# ---------------------------------------------------------------------------


class CodexAdapter:
    """Adapter that streams a Codex SDK session as AgentEvents."""

    def __init__(
        self,
        config: CodexAgentConfig,
        *,
        client_factory: ClientFactory | None = None,
        token_snapshot_poller: TokenSnapshotPoller | None = None,
    ) -> None:
        self._config = config
        # Production builder injects ``_default_client_factory`` (lazy SDK
        # import); tests pass a fake. Either way the adapter only sees
        # the ``CodexClient`` Protocol.
        self._client_factory: ClientFactory = (
            client_factory or _default_client_factory
        )
        self._token_snapshot_poller: TokenSnapshotPoller = (
            token_snapshot_poller or _CodexTokenSnapshotTail().poll
        )
        self._client: CodexClient | None = None
        self._thread: CodexThread | None = None
        self._current_turn: CodexTurnHandle | None = None
        self._user_inputs: asyncio.Queue[str | object] = asyncio.Queue()
        self._outgoing: asyncio.Queue[AgentEvent | object] = asyncio.Queue()
        self._closed = False
        self._started = False
        # Track the Codex thread id: the value passed to ``thread_resume``
        # (if any) and the value last seen on the live thread handle.
        self._resume_thread_id: str | None = None
        self._reported_session_id: str | None = None
        # Permission state — same shape as Claude / Amp. ``_pending`` holds
        # open futures keyed by request_id; ``_allow_always`` is a
        # session-only set of canonical tool names the user has chosen to
        # auto-allow for the lifetime of this adapter.
        self._pending: dict[str, asyncio.Future[PermissionDecisionValue]] = {}
        # request_ids whose ``PermissionDecision`` event has already been
        # enqueued — guards against duplicate decisions when ``close()``
        # synthesises denials for in-flight prompts.
        self._decided: set[str] = set()
        self._allow_always: set[str] = set()
        self._pump_task: asyncio.Task[None] | None = None

    async def start(self, context: AgentStartContext) -> None:
        if self._started:
            raise RuntimeError("start() called twice")
        self._resume_thread_id = context.session_id
        client = self._client_factory()
        await client.__aenter__()
        self._client = client
        # Register the approval callback before any turn starts when the
        # concrete client supports it. The current SDK wrapper no-ops;
        # fake tests and future SDKs use the same seam.
        client.on_approval_request(self._handle_approval_request)

        try:
            if self._resume_thread_id is not None:
                thread = await client.thread_resume(
                    self._resume_thread_id, **self._thread_kwargs(context)
                )
            else:
                thread = await client.thread_start(**self._thread_kwargs(context))
        except Exception:
            # Fail loudly — the supervisor's start path catches and
            # surfaces this. We still need to clean up the entered client
            # so we don't leak a Codex subprocess.
            with suppress(Exception):
                await client.__aexit__(None, None, None)
            self._client = None
            raise
        self._thread = thread
        # Resumed threads already have an id. Fresh Codex SDK threads only
        # receive one after the first ``thread.started`` stream event, so
        # the input pump emits SessionEstablished there.
        if thread.id:
            await self._emit_session_established(thread.id)
        self._started = True

    async def send_input(self, text: str) -> None:
        await self._user_inputs.put(text)

    async def stop_turn(self) -> None:
        # Codex has a real interrupt: ``turn.interrupt()`` cancels the
        # in-flight model call and tool execution; the session stays
        # alive and accepts the next ``turn_start``.
        if not self._started or self._closed:
            return
        # If a permission prompt is open, the user pressing Esc means
        # "abort the turn" — deny any in-flight permission futures so
        # the SDK callback can return cleanly before the interrupt lands.
        # Same safety detail the Claude adapter has.
        for fut in list(self._pending.values()):
            if not fut.done():
                fut.set_result("deny")
        turn = self._current_turn
        if turn is None:
            return
        try:
            await turn.interrupt()
        except Exception:
            # Transient transport state between sub-streams; swallow so a
            # stop frame can never destabilise the supervisor's stream.
            pass

    async def resolve_permission(
        self, request_id: str, decision: PermissionDecisionValue
    ) -> None:
        fut = self._pending.get(request_id)
        if fut is None or fut.done():
            # Stale or duplicate — safe to ignore. A stale frame can
            # arrive if the WS reconnects mid-prompt and the user re-
            # clicks before the replay catches up.
            return
        fut.set_result(decision)

    async def events(self) -> AsyncIterator[AgentEvent]:
        if not self._started:
            raise RuntimeError("events() called before start()")
        self._pump_task = asyncio.create_task(
            self._run_input_pump(), name="codex-input-pump"
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
        """Drive turn-after-turn until the user_inputs queue closes.

        Each iteration:
          1. Pop the next user message (or _SHUTDOWN → exit).
          2. Emit ``StatusChange("thinking")``.
          3. ``turn = await thread.turn_start(text)`` — stash on
             ``self._current_turn`` so ``stop_turn`` can interrupt it.
          4. Iterate ``turn.stream()``; convert each notification to
             zero-or-more ``AgentEvent`` via ``_convert``; put each on
             ``_outgoing``.
          5. Loop.

        Errors yield an Error event + idle transition; the loop continues
        so the user can recover by sending another message.
        """
        assert self._thread is not None
        thread = self._thread
        model = self._config.model.value
        while True:
            text = await self._user_inputs.get()
            if text is _SHUTDOWN:
                await self._outgoing.put(_SHUTDOWN)
                return
            assert isinstance(text, str)
            await self._outgoing.put(
                StatusChange(ts=datetime.now(UTC), status="thinking")
            )
            # Track the prompt size of the latest sub-call for ctx%
            # display. Codex emits ``event_msg/token_count`` frames with
            # ``last_token_usage``; older/future SDK shapes may still hang
            # usage off an agent-message item, so the helper accepts both.
            last_prompt_tokens = 0
            context_window: int | None = None
            try:
                turn = await thread.turn_start(text)
                self._current_turn = turn
                if self._reported_session_id:
                    # Avoid carrying a previous turn's JSONL token_count
                    # into this turn's terminal TurnMetrics.
                    self._token_snapshot_poller(self._reported_session_id)
                async for notification in turn.stream():
                    thread_id = _thread_id_from_notification(notification)
                    if thread_id:
                        await self._emit_session_established(thread_id)
                    snapshot = _token_snapshot_from_notification(notification)
                    if snapshot is None and self._reported_session_id:
                        snapshot = self._token_snapshot_poller(
                            self._reported_session_id
                        )
                    if snapshot is not None:
                        last_prompt_tokens = snapshot.last_prompt_tokens
                        if snapshot.context_window is not None:
                            context_window = snapshot.context_window
                    for ev in _convert(
                        notification,
                        model=model,
                        last_prompt_tokens=last_prompt_tokens,
                        context_window=context_window,
                    ):
                        await self._outgoing.put(ev)
            except Exception as exc:
                await self._outgoing.put(
                    Error(ts=datetime.now(UTC), message=str(exc))
                )
                await self._outgoing.put(
                    StatusChange(ts=datetime.now(UTC), status="idle")
                )
            finally:
                self._current_turn = None

    async def _handle_approval_request(
        self, request: ApprovalRequest
    ) -> PermissionDecisionValue:
        """SDK callback for server-initiated approvals.

        Canonicalises the tool, short-circuits on the auto-allow set,
        otherwise emits a ``PermissionRequest`` and blocks on the user's
        decision (resolved by ``resolve_permission``). Mirrors the Claude
        adapter's ``_can_use_tool``.

        Returns the SDK-shaped decision string the SDK round-trips back
        to Codex as ``result.decision``. The supervisor's contract is
        ``allow``/``allow_always``/``deny``; we translate at the boundary.
        """
        canon_name, canon_input = canonicalize_tool(
            request.tool_name, dict(request.tool_input)
        )
        if canon_name in self._allow_always:
            return "allow"
        request_id = request.request_id or uuid.uuid4().hex
        loop = asyncio.get_running_loop()
        fut: asyncio.Future[PermissionDecisionValue] = loop.create_future()
        self._pending[request_id] = fut
        await self._outgoing.put(
            PermissionRequest(
                ts=datetime.now(UTC),
                request_id=request_id,
                tool_name=canon_name,
                tool_input=canon_input,
            )
        )
        try:
            try:
                decision = await fut
            except asyncio.CancelledError:
                # Turn cancelled (Esc / supervisor shutdown) — treat as
                # deny so the SDK doesn't run the tool.
                decision = "deny"
        finally:
            self._pending.pop(request_id, None)
        if request_id not in self._decided:
            self._decided.add(request_id)
            # Shield the put so a late cancellation can't drop the
            # decision and leave an orphan ``permission_request`` in the
            # transcript — the bug behind stuck "Allow ..." prompts on
            # the Claude/Amp adapters.
            await asyncio.shield(
                self._outgoing.put(
                    PermissionDecision(
                        ts=datetime.now(UTC),
                        request_id=request_id,
                        decision=decision,
                    )
                )
            )
        if decision == "allow_always":
            self._allow_always.add(canon_name)
        return decision

    async def _emit_session_established(self, session_id: str) -> None:
        if self._reported_session_id == session_id:
            return
        await self._outgoing.put(
            SessionEstablished(ts=datetime.now(UTC), session_id=session_id)
        )
        self._reported_session_id = session_id

    async def close(self) -> None:
        if self._closed:
            return
        self._closed = True
        # Resolve any in-flight permission prompts so the SDK callback
        # can return; otherwise the SDK exit would block on a hung future.
        # We publish a synthetic ``PermissionDecision(deny)`` BEFORE
        # resolving the future so the transcript always has a matching
        # decision — same belt-and-suspenders pattern as Claude / Amp.
        for request_id, fut in list(self._pending.items()):
            if request_id not in self._decided:
                self._decided.add(request_id)
                await self._outgoing.put(
                    PermissionDecision(
                        ts=datetime.now(UTC),
                        request_id=request_id,
                        decision="deny",
                    )
                )
            if not fut.done():
                fut.set_result("deny")
        # Push _SHUTDOWN onto user inputs so the pump exits cleanly.
        await self._user_inputs.put(_SHUTDOWN)
        # Cancel an in-flight turn stream if one is still running. The
        # SDK ``__aexit__`` would do this on its own, but cancelling
        # explicitly speeds shutdown when the model is stuck on a long
        # tool call.
        turn = self._current_turn
        if turn is not None:
            with suppress(Exception):
                await turn.interrupt()
        if self._client is not None:
            try:
                # Defensive timeout: if the SDK hangs on exit (transport
                # in a weird state) we'd rather move on than block the
                # supervisor's shutdown. NFR-005's ladder is the SDK's
                # responsibility above this — we only add the timeout
                # so a misbehaving SDK can't pin the event loop.
                await asyncio.wait_for(
                    self._client.__aexit__(None, None, None), timeout=5.0
                )
            except (TimeoutError, Exception) as exc:
                _log.warning(
                    "codex SDK exit failed or timed out: %r", exc
                )
            self._client = None

    def _thread_kwargs(self, context: AgentStartContext) -> dict[str, Any]:
        """Common kwargs for ``thread_start`` and ``thread_resume``.

        Wires:
          - ``model``           — primary selector
          - ``cwd``             — agent's workdir (sandbox root)
          - ``additional_directories`` — shared-folder targets that
                                  Codex should treat as writable roots
          - ``sandbox``         — OS-level filesystem gating tier
          - ``approval_mode``   — when-to-prompt policy
          - ``base_instructions`` — Atelier-built system prompt
          - ``mcp_servers``     — Atelier artifact tools subprocess
          - ``config_overrides`` — ``model_reasoning_effort`` (Codex's
                                  TOML config knob) routed through the
                                  SDK's escape hatch

        ``developer_instructions`` is intentionally unset: Atelier builds
        a single ``system_prompt`` via ``domain/agents/system_prompt.py``
        and feeds it through ``CommonAgentConfig``.
        """
        kwargs: dict[str, Any] = {
            "model": self._config.model.value,
            "cwd": str(self._config.common.workdir),
            "sandbox": self._config.sandbox.value,
            "approval_mode": self._config.approval_mode.value,
            "base_instructions": context.system_prompt,
            "config_overrides": {
                "model_reasoning_effort": self._config.reasoning_effort.value,
            },
        }
        if not self._config.summary_only:
            kwargs["mcp_servers"] = _build_atelier_mcp_servers()
        if (
            self._config.sandbox is CodexSandbox.WORKSPACE_WRITE
            and self._config.common.writable_roots
            and not self._config.summary_only
        ):
            kwargs["additional_directories"] = [
                str(root) for root in self._config.common.writable_roots
            ]
        return kwargs


def _build_atelier_mcp_servers() -> dict[str, Any]:
    """Atelier artifact-tool MCP server config the Codex SDK spawns.

    Subprocess form, matching Amp — the bundled ``atelier_mcp_server``
    module is invoked as ``python -m`` against the running backend's
    interpreter so the server resolves through Atelier's editable
    install. Each agent gets its own short-lived subprocess; no shared
    state to worry about.
    """
    return {
        MCP_SERVER_NAME: {
            "command": sys.executable,
            "args": [
                "-m",
                "src.infrastructure.agents.atelier_mcp_server",
            ],
        }
    }


@dataclass
class _SdkNotification:
    type: str
    params: dict[str, Any]


class _CodexAppServerClient:
    """Codex app-server JSON-RPC client implementing ``CodexClient``.

    The Python SDK's ``exec --experimental-json`` transport is one-way:
    it streams events, but approval requests stay inside Codex. The app-
    server protocol is bidirectional JSON-RPC over stdio, so command,
    file-change, and permission approvals can use Atelier's existing
    ``PermissionRequest`` callback path.
    """

    def __init__(
        self,
        *,
        executable_path: str = "codex",
        env: dict[str, str] | None = None,
    ) -> None:
        self._executable_path = executable_path
        self._env = env
        self._proc: asyncio.subprocess.Process | None = None
        self._reader_task: asyncio.Task[None] | None = None
        self._next_id = 1
        self._pending: dict[int | str, asyncio.Future[dict[str, Any]]] = {}
        self._thread_queues: dict[str, asyncio.Queue[_SdkNotification | object]] = {}
        self._approval_callback: Callable[[ApprovalRequest], Any] | None = None
        self._server_request_tasks: set[asyncio.Task[None]] = set()
        self._closed = False

    async def __aenter__(self) -> _CodexAppServerClient:
        env = os.environ.copy()
        if self._env is not None:
            env.update(self._env)
        self._proc = await asyncio.create_subprocess_exec(
            self._executable_path,
            "app-server",
            "--listen",
            "stdio://",
            stdin=asyncio.subprocess.PIPE,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            env=env,
            limit=_APP_SERVER_STDIO_LIMIT_BYTES,
        )
        self._reader_task = asyncio.create_task(
            self._read_loop(), name="codex-app-server-read-loop"
        )
        try:
            await self._request(
                "initialize",
                {
                    "clientInfo": {
                        "name": "atelier",
                        "title": "Atelier",
                        "version": "0",
                    },
                    "capabilities": {"experimentalApi": True},
                },
            )
            await self._send({"method": "initialized"})
        except Exception:
            await self.__aexit__(None, None, None)
            raise
        return self

    async def __aexit__(self, exc_type: Any, exc: Any, tb: Any) -> None:
        self._closed = True
        for queue in self._thread_queues.values():
            await queue.put(_SHUTDOWN)
        if self._reader_task is not None:
            self._reader_task.cancel()
            with suppress(asyncio.CancelledError):
                await self._reader_task
        for task in list(self._server_request_tasks):
            task.cancel()
        if self._server_request_tasks:
            await asyncio.gather(
                *self._server_request_tasks, return_exceptions=True
            )
        proc = self._proc
        if proc is not None and proc.returncode is None:
            proc.terminate()
            try:
                await asyncio.wait_for(proc.wait(), timeout=2.0)
            except TimeoutError:
                proc.kill()
                await proc.wait()
        self._proc = None

    async def thread_start(
        self,
        *,
        model: str,
        cwd: str,
        sandbox: str,
        approval_mode: str,
        base_instructions: str | None = None,
        mcp_servers: dict[str, Any] | None = None,
        config_overrides: dict[str, Any] | None = None,
        additional_directories: list[str] | None = None,
    ) -> CodexThread:
        result = await self._request(
            "thread/start",
            _app_server_thread_params(
                model=model,
                cwd=cwd,
                sandbox=sandbox,
                approval_mode=approval_mode,
                base_instructions=base_instructions,
                mcp_servers=mcp_servers,
                config_overrides=config_overrides,
                additional_directories=additional_directories,
            ),
        )
        thread_id = _thread_id_from_app_server_result(result)
        return _CodexAppServerThread(self, thread_id)

    async def thread_resume(
        self,
        thread_id: str,
        *,
        model: str,
        cwd: str,
        sandbox: str,
        approval_mode: str,
        base_instructions: str | None = None,
        mcp_servers: dict[str, Any] | None = None,
        config_overrides: dict[str, Any] | None = None,
        additional_directories: list[str] | None = None,
    ) -> CodexThread:
        params = _app_server_thread_params(
            model=model,
            cwd=cwd,
            sandbox=sandbox,
            approval_mode=approval_mode,
            base_instructions=base_instructions,
            mcp_servers=mcp_servers,
            config_overrides=config_overrides,
            additional_directories=additional_directories,
        )
        params["threadId"] = thread_id
        result = await self._request("thread/resume", params)
        return _CodexAppServerThread(
            self, _thread_id_from_app_server_result(result) or thread_id
        )

    def on_approval_request(self, callback: Callable[[ApprovalRequest], Any]) -> None:
        self._approval_callback = callback

    async def _turn_start(
        self, thread_id: str, user_message: str
    ) -> _CodexAppServerTurnHandle:
        result = await self._request(
            "turn/start",
            {
                "threadId": thread_id,
                "input": [{"type": "text", "text": user_message}],
            },
        )
        turn = result.get("turn") if isinstance(result, dict) else None
        turn_id = turn.get("id") if isinstance(turn, dict) else ""
        queue = self._queue_for_thread(thread_id)
        return _CodexAppServerTurnHandle(self, thread_id, str(turn_id or ""), queue)

    async def _interrupt_turn(self, thread_id: str, turn_id: str) -> None:
        if not turn_id:
            return
        await self._request(
            "turn/interrupt",
            {"threadId": thread_id, "turnId": turn_id},
        )

    def _queue_for_thread(self, thread_id: str) -> asyncio.Queue[_SdkNotification | object]:
        queue = self._thread_queues.get(thread_id)
        if queue is None:
            queue = asyncio.Queue()
            self._thread_queues[thread_id] = queue
        return queue

    async def _request(self, method: str, params: dict[str, Any]) -> dict[str, Any]:
        req_id = self._next_id
        self._next_id += 1
        loop = asyncio.get_running_loop()
        fut: asyncio.Future[dict[str, Any]] = loop.create_future()
        self._pending[req_id] = fut
        try:
            await self._send({"id": req_id, "method": method, "params": params})
            return await asyncio.wait_for(
                fut, timeout=_APP_SERVER_REQUEST_TIMEOUT_SECONDS
            )
        except TimeoutError as exc:
            self._pending.pop(req_id, None)
            if not fut.done():
                fut.cancel()
            raise RuntimeError(
                f"Codex app-server request {method!r} timed out after "
                f"{_APP_SERVER_REQUEST_TIMEOUT_SECONDS:g}s"
            ) from exc
        except Exception:
            self._pending.pop(req_id, None)
            raise

    async def _send(self, message: dict[str, Any]) -> None:
        proc = self._proc
        if proc is None or proc.stdin is None:
            raise RuntimeError("Codex app-server is not running")
        wire = dict(message)
        wire.setdefault("jsonrpc", "2.0")
        proc.stdin.write((json.dumps(wire) + "\n").encode("utf-8"))
        await proc.stdin.drain()

    async def _read_loop(self) -> None:
        assert self._proc is not None
        assert self._proc.stdout is not None
        try:
            while not self._closed:
                raw = await self._proc.stdout.readline()
                if not raw:
                    break
                try:
                    message = json.loads(raw)
                except ValueError:
                    continue
                await self._handle_message(message)
        finally:
            stderr = b""
            if self._proc is not None and self._proc.stderr is not None:
                with suppress(Exception):
                    stderr = await asyncio.wait_for(
                        self._proc.stderr.read(), timeout=0.2
                    )
            err_text = stderr.decode("utf-8", errors="replace").strip()
            for fut in self._pending.values():
                if not fut.done():
                    detail = f": {err_text}" if err_text else ""
                    fut.set_exception(RuntimeError(f"Codex app-server exited{detail}"))

    async def _handle_message(self, message: dict[str, Any]) -> None:
        msg_id = message.get("id")
        method = message.get("method")
        if isinstance(msg_id, int | str) and method:
            task = asyncio.create_task(self._handle_server_request(message))
            self._server_request_tasks.add(task)
            task.add_done_callback(self._server_request_tasks.discard)
            return
        if isinstance(msg_id, int | str):
            fut = self._pending.pop(msg_id, None)
            if fut is None or fut.done():
                return
            if "error" in message:
                fut.set_exception(RuntimeError(json.dumps(message["error"])))
            else:
                result = message.get("result")
                fut.set_result(result if isinstance(result, dict) else {})
            return
        if isinstance(method, str):
            notification = _normalize_app_server_notification(message)
            if notification is not None:
                await self._dispatch_notification(notification)

    async def _handle_server_request(self, message: dict[str, Any]) -> None:
        msg_id = message.get("id")
        method = str(message.get("method") or "")
        params = message.get("params")
        if not isinstance(msg_id, int | str) or not isinstance(params, dict):
            return
        callback = self._approval_callback
        if callback is None:
            await self._send({"id": msg_id, "result": _app_server_denial(method)})
            return
        request = _approval_request_from_app_server(method, params)
        if request is None:
            await self._send({"id": msg_id, "result": {}})
            return
        try:
            decision = await callback(request)
        except Exception:
            _log.exception("Codex approval callback failed")
            decision = "deny"
        await self._send(
            {
                "id": msg_id,
                "result": _app_server_approval_result(method, params, str(decision)),
            }
        )

    async def _dispatch_notification(self, notification: _SdkNotification) -> None:
        thread_id = _thread_id_from_app_server_params(notification.params)
        if thread_id:
            await self._queue_for_thread(thread_id).put(notification)
            return
        for queue in self._thread_queues.values():
            await queue.put(notification)


class _CodexAppServerThread:
    def __init__(self, client: _CodexAppServerClient, thread_id: str) -> None:
        self._client = client
        self._id = thread_id

    @property
    def id(self) -> str:
        return self._id

    async def turn_start(self, user_message: str) -> _CodexAppServerTurnHandle:
        return await self._client._turn_start(self._id, user_message)


class _CodexAppServerTurnHandle:
    def __init__(
        self,
        client: _CodexAppServerClient,
        thread_id: str,
        turn_id: str,
        queue: asyncio.Queue[_SdkNotification | object],
    ) -> None:
        self._client = client
        self._thread_id = thread_id
        self._turn_id = turn_id
        self._queue = queue

    async def stream(self) -> AsyncIterator[_SdkNotification]:
        while True:
            item = await self._queue.get()
            if item is _SHUTDOWN:
                return
            assert isinstance(item, _SdkNotification)
            turn_id = _turn_id_from_app_server_params(item.params)
            if turn_id and self._turn_id and turn_id != self._turn_id:
                continue
            yield item
            if item.type == "turn/completed":
                return

    async def interrupt(self) -> None:
        try:
            await self._client._interrupt_turn(self._thread_id, self._turn_id)
        finally:
            # The app-server interrupt request can complete before the
            # event stream receives a terminal turn/completed frame,
            # especially while a shell command is being torn down. Without
            # a terminal event this turn's stream stays parked forever and
            # later user inputs sit in the adapter queue until a full
            # reconnect. Emit the same terminal shape the server normally
            # sends so the pump returns to the next prompt.
            if self._turn_id:
                await self._queue.put(
                    _SdkNotification(
                        "turn/completed",
                        {
                            "threadId": self._thread_id,
                            "turnId": self._turn_id,
                            "status": "interrupted",
                            "duration_ms": 0,
                        },
                    )
                )


class _CodexTokenSnapshotTail:
    """Incrementally read Codex's session JSONL for token_count frames.

    The Python SDK's experimental-json stream currently exposes terminal
    usage, but Codex's richer ``last_token_usage`` snapshots are written
    to ``~/.codex/sessions``. Polling the append-only file lets the
    adapter enrich the next terminal ``TurnMetrics`` with the same
    context fields Claude/Amp attach from their SDK streams.
    """

    def __init__(self) -> None:
        self._path: Path | None = None
        self._offset = 0

    def poll(self, session_id: str) -> _TokenSnapshot | None:
        path = self._path
        if path is None or not path.exists():
            path = _codex_session_transcript_path(session_id)
            if path is None:
                return None
            self._path = path
            self._offset = 0

        latest: _TokenSnapshot | None = None
        try:
            if path.stat().st_size < self._offset:
                self._offset = 0
            with path.open("r", encoding="utf-8") as f:
                f.seek(self._offset)
                while raw := f.readline():
                    try:
                        entry = json.loads(raw)
                    except ValueError:
                        continue
                    snapshot = _token_snapshot_from_raw_entry(entry)
                    if snapshot is not None:
                        latest = snapshot
                self._offset = f.tell()
        except OSError:
            return None

        return latest


class _CodexSdkClient:
    """Adapter from openai-codex-sdk 0.1.x to Atelier's local Protocol."""

    def __init__(self, sdk_client: Any) -> None:
        self._sdk_client = sdk_client

    async def __aenter__(self) -> _CodexSdkClient:
        return self

    async def __aexit__(self, exc_type: Any, exc: Any, tb: Any) -> None:
        return None

    async def thread_start(
        self,
        *,
        model: str,
        cwd: str,
        sandbox: str,
        approval_mode: str,
        base_instructions: str | None = None,
        mcp_servers: dict[str, Any] | None = None,
        config_overrides: dict[str, Any] | None = None,
        additional_directories: list[str] | None = None,
    ) -> CodexThread:
        return _CodexSdkThread(
            self._sdk_client.start_thread(
                _thread_options_from_kwargs(
                    {
                        "model": model,
                        "cwd": cwd,
                        "sandbox": sandbox,
                        "approval_mode": approval_mode,
                        "base_instructions": base_instructions,
                        "mcp_servers": mcp_servers,
                        "config_overrides": config_overrides,
                        "additional_directories": additional_directories,
                    }
                )
            )
        )

    async def thread_resume(
        self,
        thread_id: str,
        *,
        model: str,
        cwd: str,
        sandbox: str,
        approval_mode: str,
        base_instructions: str | None = None,
        mcp_servers: dict[str, Any] | None = None,
        config_overrides: dict[str, Any] | None = None,
        additional_directories: list[str] | None = None,
    ) -> CodexThread:
        return _CodexSdkThread(
            self._sdk_client.resume_thread(
                thread_id,
                _thread_options_from_kwargs(
                    {
                        "model": model,
                        "cwd": cwd,
                        "sandbox": sandbox,
                        "approval_mode": approval_mode,
                        "base_instructions": base_instructions,
                        "mcp_servers": mcp_servers,
                        "config_overrides": config_overrides,
                        "additional_directories": additional_directories,
                    }
                ),
            )
        )

    def on_approval_request(self, callback: Callable[[ApprovalRequest], Any]) -> None:
        # The legacy Python SDK's exec transport is one-way; production
        # uses _CodexAppServerClient for bidirectional approvals.
        _ = callback


class _CodexSdkThread:
    def __init__(self, sdk_thread: Any) -> None:
        self._sdk_thread = sdk_thread

    @property
    def id(self) -> str:
        return self._sdk_thread.id or ""

    async def turn_start(self, user_message: str) -> _CodexSdkTurnHandle:
        try:
            from openai_codex_sdk import AbortController
        except ImportError as exc:  # pragma: no cover - covered by factory import
            raise RuntimeError("openai-codex-sdk is not installed") from exc

        controller = AbortController()
        streamed = await self._sdk_thread.run_streamed(
            user_message, {"signal": controller.signal}
        )
        return _CodexSdkTurnHandle(streamed.events, controller)


class _CodexSdkTurnHandle:
    def __init__(self, events: AsyncIterator[Any], controller: Any) -> None:
        self._events = events
        self._controller = controller

    async def stream(self) -> AsyncIterator[_SdkNotification]:
        async for event in self._events:
            yield _normalize_sdk_event(event)

    async def interrupt(self) -> None:
        self._controller.abort("Interrupted by user")


def _thread_options_from_kwargs(kwargs: dict[str, Any]) -> dict[str, Any]:
    overrides = kwargs.get("config_overrides") or {}
    options: dict[str, Any] = {
        "model": kwargs.get("model"),
        "sandboxMode": kwargs.get("sandbox"),
        "workingDirectory": kwargs.get("cwd"),
        "approvalPolicy": kwargs.get("approval_mode"),
        "modelReasoningEffort": overrides.get("model_reasoning_effort"),
        "additionalDirectories": kwargs.get("additional_directories"),
    }
    # openai-codex-sdk 0.1.x exposes sandbox/approval/additional dirs
    # but not base instructions or MCP server registration on ThreadOptions.
    # Keep only supported keys.
    return {k: v for k, v in options.items() if v is not None}


def _normalize_sdk_event(event: Any) -> _SdkNotification:
    event_type = getattr(event, "type", "")
    if event_type == "thread.started":
        return _SdkNotification(
            "thread/started",
            {"thread_id": getattr(event, "thread_id", "")},
        )
    if event_type == "turn.started":
        return _SdkNotification("turn/started", {})
    if event_type == "turn.completed":
        return _SdkNotification(
            "turn/completed",
            {
                "status": "completed",
                "usage": _model_dump(getattr(event, "usage", None)),
            },
        )
    if event_type == "turn.failed":
        error = getattr(event, "error", None)
        return _SdkNotification(
            "turn/completed",
            {
                "status": "failed",
                "error": getattr(error, "message", None) or str(error or ""),
            },
        )
    if event_type in {"item.started", "item.updated", "item.completed"}:
        item = _normalize_sdk_item(getattr(event, "item", None))
        return _SdkNotification(event_type.replace(".", "/"), {"item": item})
    if event_type == "error":
        return _SdkNotification(
            "turn/completed",
            {"status": "failed", "error": getattr(event, "message", "")},
        )
    if event_type == "event_msg":
        data = _model_dump(event)
        payload = data.get("payload")
        if isinstance(payload, dict) and payload.get("type") == "token_count":
            return _SdkNotification("token_count", payload)
        if isinstance(payload, dict) and payload.get("type") == "context_compacted":
            return _SdkNotification("context_compacted", payload)
        return _SdkNotification("event_msg", data)
    return _SdkNotification(event_type.replace(".", "/"), _model_dump(event))


def _normalize_sdk_item(item: Any) -> dict[str, Any]:
    data = _model_dump(item)
    item_type = data.get("type")
    if item_type == "agent_message":
        data["itemType"] = "agentMessage"
    elif item_type == "reasoning":
        data["itemType"] = "reasoning"
    elif item_type == "command_execution":
        data["itemType"] = "commandExecution"
        if "aggregated_output" in data:
            data["output"] = data["aggregated_output"]
    elif item_type == "file_change":
        data["itemType"] = "fileChange"
        changes = data.get("changes")
        if isinstance(changes, list) and changes:
            first = changes[0]
            if isinstance(first, dict):
                data.setdefault("path", first.get("path"))
                data.setdefault("result", first.get("kind"))
    elif item_type == "mcp_tool_call":
        data["itemType"] = "mcpToolCall"
    return data


def _app_server_thread_params(
    *,
    model: str,
    cwd: str,
    sandbox: str,
    approval_mode: str,
    base_instructions: str | None,
    mcp_servers: dict[str, Any] | None,
    config_overrides: dict[str, Any] | None,
    additional_directories: list[str] | None,
) -> dict[str, Any]:
    config: dict[str, Any] = dict(config_overrides or {})
    if additional_directories:
        config["sandbox_workspace_write"] = {
            "writable_roots": additional_directories,
            "network_access": False,
        }
    if mcp_servers:
        # The app-server reads MCP servers from the same config shape the
        # CLI uses. Older Codex builds may ignore this; that is no worse
        # than the legacy SDK path, which could not register MCP servers.
        config["mcp_servers"] = mcp_servers
    params: dict[str, Any] = {
        "model": model,
        "cwd": cwd,
        "sandbox": sandbox,
        "approvalPolicy": approval_mode,
        "approvalsReviewer": "user",
        "baseInstructions": base_instructions,
    }
    if config:
        params["config"] = config
    return {k: v for k, v in params.items() if v is not None}


def _thread_id_from_app_server_result(result: dict[str, Any]) -> str:
    thread = result.get("thread")
    if isinstance(thread, dict):
        thread_id = thread.get("id") or thread.get("sessionId")
        if isinstance(thread_id, str):
            return thread_id
    return ""


def _normalize_app_server_notification(
    message: dict[str, Any]
) -> _SdkNotification | None:
    method = str(message.get("method") or "")
    params = message.get("params")
    if not isinstance(params, dict):
        params = {}
    if method in {
        "thread/started",
        "turn/started",
        "item/agentMessage/delta",
        "item/reasoning/summaryTextDelta",
    }:
        return _SdkNotification(method, dict(params))
    if method == "thread/compacted":
        return _SdkNotification("context_compacted", dict(params))
    if method in {"item/started", "item/completed"}:
        normalized = dict(params)
        item = normalized.get("item")
        if isinstance(item, dict):
            normalized["item"] = _normalize_app_server_item(item)
        return _SdkNotification(method, normalized)
    if method == "turn/completed":
        normalized = dict(params)
        turn = normalized.get("turn")
        if isinstance(turn, dict):
            status = turn.get("status")
            normalized["status"] = status if isinstance(status, str) else "completed"
            error = turn.get("error")
            if isinstance(error, dict):
                normalized["error"] = error.get("message") or json.dumps(error)
            normalized["duration_ms"] = turn.get("durationMs") or 0
        return _SdkNotification(method, normalized)
    if method == "thread/tokenUsage/updated":
        usage = params.get("tokenUsage")
        if isinstance(usage, dict):
            last = usage.get("last")
            if isinstance(last, dict):
                return _SdkNotification(
                    "token_count",
                    {
                        "info": {
                            "last_token_usage": {
                                "input_tokens": last.get("inputTokens", 0),
                                "cached_input_tokens": last.get(
                                    "cachedInputTokens", 0
                                ),
                                "output_tokens": last.get("outputTokens", 0),
                            },
                            "model_context_window": usage.get(
                                "modelContextWindow"
                            ),
                        }
                    },
                )
    return None


def _normalize_app_server_item(item: dict[str, Any]) -> dict[str, Any]:
    data = dict(item)
    item_type = data.get("type")
    if isinstance(item_type, str):
        data["itemType"] = item_type
    if item_type == "commandExecution":
        if "aggregatedOutput" in data:
            data["output"] = data.get("aggregatedOutput") or ""
        if "exitCode" in data:
            data["exit_code"] = data.get("exitCode")
    elif item_type == "fileChange":
        changes = data.get("changes")
        if isinstance(changes, list) and changes:
            first = changes[0]
            if isinstance(first, dict):
                data.setdefault("path", first.get("path"))
                data.setdefault("result", first.get("kind"))
    elif item_type == "reasoning":
        summary = data.get("summary")
        if isinstance(summary, list):
            data["summary"] = "\n".join(str(part) for part in summary)
    return data


def _thread_id_from_app_server_params(params: dict[str, Any]) -> str | None:
    thread_id = params.get("threadId")
    if isinstance(thread_id, str) and thread_id:
        return thread_id
    thread = params.get("thread")
    if isinstance(thread, dict):
        thread_id = thread.get("id") or thread.get("sessionId")
        if isinstance(thread_id, str) and thread_id:
            return thread_id
    return None


def _turn_id_from_app_server_params(params: dict[str, Any]) -> str | None:
    turn_id = params.get("turnId")
    if isinstance(turn_id, str) and turn_id:
        return turn_id
    turn = params.get("turn")
    if isinstance(turn, dict):
        turn_id = turn.get("id")
        if isinstance(turn_id, str) and turn_id:
            return turn_id
    return None


def _approval_request_from_app_server(
    method: str, params: dict[str, Any]
) -> _AppServerApprovalRequest | None:
    request_id = _app_server_request_id(method, params)
    if method in {
        "item/commandExecution/requestApproval",
        "execCommandApproval",
    }:
        command = params.get("command")
        tool_input: dict[str, Any] = {}
        if isinstance(command, list):
            tool_input["command"] = shlex.join(str(part) for part in command)
        elif isinstance(command, str):
            tool_input["command"] = command
        if isinstance(params.get("cwd"), str):
            tool_input["cwd"] = params["cwd"]
        if isinstance(params.get("reason"), str):
            tool_input["reason"] = params["reason"]
        return _AppServerApprovalRequest(
            request_id=request_id,
            tool_name="Bash",
            tool_input=tool_input,
            method=method,
            params=params,
        )
    if method in {"item/fileChange/requestApproval", "applyPatchApproval"}:
        tool_input = {k: v for k, v in params.items() if k not in {"threadId", "turnId"}}
        return _AppServerApprovalRequest(
            request_id=request_id,
            tool_name="FileChange",
            tool_input=tool_input,
            method=method,
            params=params,
        )
    if method == "item/permissions/requestApproval":
        return _AppServerApprovalRequest(
            request_id=request_id,
            tool_name="Permission",
            tool_input=dict(params),
            method=method,
            params=params,
        )
    return None


def _app_server_request_id(method: str, params: dict[str, Any]) -> str:
    for key in ("approvalId", "itemId", "callId"):
        value = params.get(key)
        if isinstance(value, str) and value:
            return f"{method}:{value}"
    return f"{method}:{uuid.uuid4().hex}"


def _app_server_approval_result(
    method: str, params: dict[str, Any], decision: str
) -> dict[str, Any]:
    if method in {
        "item/commandExecution/requestApproval",
        "item/fileChange/requestApproval",
    }:
        if decision == "allow_always":
            return {"decision": "acceptForSession"}
        if decision == "allow":
            return {"decision": "accept"}
        return {"decision": "decline"}
    if method == "execCommandApproval":
        if decision == "allow_always":
            return {"decision": "approved_for_session"}
        if decision == "allow":
            return {"decision": "approved"}
        return {"decision": "denied"}
    if method == "applyPatchApproval":
        if decision == "allow_always":
            return {"decision": "approved_for_session"}
        if decision == "allow":
            return {"decision": "approved"}
        return {"decision": "denied"}
    if method == "item/permissions/requestApproval":
        if decision in {"allow", "allow_always"}:
            granted = params.get("permissions")
            return {
                "permissions": granted if isinstance(granted, dict) else {},
                "scope": "session" if decision == "allow_always" else "turn",
            }
        return {"permissions": {}, "scope": "turn"}
    return {}


def _app_server_denial(method: str) -> dict[str, Any]:
    return _app_server_approval_result(method, {}, "deny")


def _model_dump(value: Any) -> dict[str, Any]:
    if value is None:
        return {}
    if isinstance(value, dict):
        return dict(value)
    dump = getattr(value, "model_dump", None)
    if callable(dump):
        dumped = dump()
        return dict(dumped) if isinstance(dumped, dict) else {}
    if hasattr(value, "__dict__"):
        return dict(value.__dict__)
    return {}


def _codex_session_transcript_path(session_id: str) -> Path | None:
    if not session_id:
        return None
    sessions_root = Path.home() / ".codex" / "sessions"
    if not sessions_root.exists():
        return None
    matches = sorted(
        sessions_root.glob(f"**/rollout-*-{session_id}.jsonl"),
        key=lambda p: p.stat().st_mtime,
        reverse=True,
    )
    return matches[0] if matches else None


def _token_snapshot_from_raw_entry(entry: dict[str, Any]) -> _TokenSnapshot | None:
    if entry.get("type") != "event_msg":
        return None
    payload = entry.get("payload")
    if not isinstance(payload, dict) or payload.get("type") != "token_count":
        return None
    return _token_snapshot_from_token_count_payload(payload)


def _thread_id_from_notification(notification: Notification) -> str | None:
    if notification.type == "thread/started":
        thread_id = notification.params.get("thread_id")
        if isinstance(thread_id, str) and thread_id:
            return thread_id
    return None


# ---------------------------------------------------------------------------
# Notification → AgentEvent mapping
# ---------------------------------------------------------------------------


def _convert(
    notification: Notification,
    *,
    model: str | None = None,
    last_prompt_tokens: int = 0,
    context_window: int | None = None,
) -> Iterable[AgentEvent]:
    """Map one Codex notification onto zero-or-more ``AgentEvent``.

    Discriminator is ``notification.type``. The Codex SDK delivers a
    three-state lifecycle per item: ``item/started`` → optional deltas
    → ``item/completed``. Each item carries an ``itemType`` (e.g.
    ``agentMessage``, ``reasoning``, ``commandExecution``, ``fileChange``,
    ``mcpToolCall``) that drives the mapping.

    ``model`` lets the adapter stamp per-turn metrics with the configured
    Codex model id. ``last_prompt_tokens`` is the prompt size of the
    latest sub-call; ``context_window`` is the CLI's effective runtime
    window when it reports one. See ``TurnMetrics`` for the full rationale.
    """
    now = datetime.now(UTC)
    t = notification.type
    params = notification.params

    # Per-item streaming chunks ------------------------------------------------
    if t == "item/agentMessage/delta":
        delta = params.get("delta") or params.get("text") or ""
        if delta:
            yield MessageDelta(ts=now, text=str(delta))
        return
    if t == "item/reasoning/summaryTextDelta":
        delta = params.get("delta") or params.get("text") or ""
        if delta:
            yield ThinkingDelta(ts=now, text=str(delta))
        return

    # Item started — emit a ToolCall for tool-like items.
    if t == "item/started":
        item = params.get("item") or params
        item_type = item.get("itemType") or item.get("type")
        if item_type == "commandExecution":
            yield from _emit_command_tool_call(item, now)
            return
        if item_type == "fileChange":
            yield from _emit_file_change_tool_call(item, now)
            return
        if item_type == "mcpToolCall":
            yield from _emit_mcp_tool_call(item, now)
            return
        # Agent message + reasoning start frames carry no useful event:
        # we wait for the streaming deltas (and the matching completion).
        return

    # Item completed — emit terminal forms (MessageComplete /
    # ThinkingComplete / ToolResult), plus ArtifactMarker on artifact MCP
    # tool completions.
    if t == "item/completed":
        item = params.get("item") or params
        item_type = item.get("itemType") or item.get("type")
        if item_type == "agentMessage":
            text = _coerce_str(item.get("text") or item.get("content"))
            yield MessageComplete(ts=now, text=text)
            # Belt-and-suspenders artifact scan — same fallback the
            # Claude/Amp adapters carry, in case the model emits an
            # ``atelier_artifact`` line in chat instead of (or in
            # addition to) the MCP tool. Tracker dedupes per work.
            for payload in scan_text_for_artifact_markers(text):
                yield ArtifactMarker(ts=now, payload=payload)
            return
        if item_type == "reasoning":
            text = _coerce_str(item.get("text") or item.get("summary"))
            yield ThinkingComplete(ts=now, text=text)
            return
        if item_type == "commandExecution":
            yield from _emit_command_tool_result(item, now)
            return
        if item_type == "fileChange":
            yield from _emit_file_change_tool_result(item, now)
            return
        if item_type == "mcpToolCall":
            yield from _emit_mcp_tool_result(item, now)
            return
        if item_type in {"contextCompaction", "context_compaction"}:
            yield ProviderContextCompacted(ts=now, provider="codex")
            return
        return

    # Turn lifecycle ----------------------------------------------------------
    if t == "turn/started":
        # The pump already emitted ``StatusChange("thinking")`` when it
        # popped the user's message; an extra "thinking" here would be a
        # no-op for the FE but adds noise to the transcript.
        return
    if t == "turn/completed":
        if params.get("status") == "failed":
            err = (
                params.get("error")
                or params.get("message")
                or "(unknown turn failure)"
            )
            yield Error(ts=now, message=str(err))
        yield from _metrics_from_turn(
            params,
            now,
            model,
            last_prompt_tokens,
            context_window,
        )
        yield StatusChange(ts=now, status="idle")
        return
    if t == "context_compacted":
        yield ProviderContextCompacted(ts=now, provider="codex")
        return

    # Anything else (handshake / heartbeats / unknown future types) is
    # quietly dropped — same liberal-in-what-you-accept posture the
    # Claude and Amp adapters take for SDK-internal frames.
    return


def _emit_command_tool_call(item: dict[str, Any], now: datetime) -> Iterable[AgentEvent]:
    """``item/started`` for an ``itemType="commandExecution"`` → ToolCall(Bash)."""
    raw = _command_execution_args(item)
    canon_name, canon_args = canonicalize_tool("Bash", raw)
    yield ToolCall(
        ts=now,
        tool_id=str(item.get("id") or item.get("itemId") or ""),
        name=canon_name,
        arguments=canon_args,
    )


def _emit_command_tool_result(
    item: dict[str, Any], now: datetime
) -> Iterable[AgentEvent]:
    """``item/completed`` for commandExecution → ToolResult."""
    content = _coerce_str(
        item.get("output")
        or item.get("text")
        or item.get("stdout")
        or ""
    )
    exit_code = item.get("exit_code", item.get("exitCode"))
    is_error = bool(exit_code) and exit_code != 0
    yield ToolResult(
        ts=now,
        tool_id=str(item.get("id") or item.get("itemId") or ""),
        content=content,
        is_error=is_error,
    )


def _command_execution_args(item: dict[str, Any]) -> dict[str, Any]:
    """Lift Codex's commandExecution payload into Atelier's ``Bash`` shape.

    Codex passes ``command`` as an argv list. We collapse the
    ``["-c", "<cmd>"]`` form into ``{"command": "<cmd>"}`` — same rule
    the Amp permission bridge uses, so the frontend's ``summariseToolInput``
    Bash branch renders Codex Bash identically to Amp Bash. Anything
    else falls back to ``{"argv": [...]}``.
    """
    cmd = item.get("command")
    out: dict[str, Any] = {}
    if isinstance(cmd, list):
        if len(cmd) == 2 and cmd[0] == "-c" and isinstance(cmd[1], str):
            out["command"] = cmd[1]
        else:
            out["argv"] = [str(c) for c in cmd]
    elif isinstance(cmd, str):
        out["command"] = cmd
    if "cwd" in item:
        out["cwd"] = item["cwd"]
    return out


def _emit_file_change_tool_call(
    item: dict[str, Any], now: datetime
) -> Iterable[AgentEvent]:
    """fileChange → ToolCall(Edit) or ToolCall(Write).

    Codex's fileChange item carries ``path``, ``old_text``, and
    ``new_text``. Empty ``old_text`` + full ``new_text`` is a write
    (canonical name ``Write``); both present is an edit (``Edit``).
    """
    tool_name, args = _file_change_canonical(item)
    canon_name, canon_args = canonicalize_tool(tool_name, args)
    yield ToolCall(
        ts=now,
        tool_id=str(item.get("id") or item.get("itemId") or ""),
        name=canon_name,
        arguments=canon_args,
    )


def _emit_file_change_tool_result(
    item: dict[str, Any], now: datetime
) -> Iterable[AgentEvent]:
    """fileChange completion → ToolResult."""
    yield ToolResult(
        ts=now,
        tool_id=str(item.get("id") or item.get("itemId") or ""),
        content=_coerce_str(
            item.get("result")
            or item.get("text")
            or item.get("message")
            or ""
        ),
        is_error=bool(item.get("error")) or item.get("status") == "failed",
    )


def _file_change_canonical(item: dict[str, Any]) -> tuple[str, dict[str, Any]]:
    """``(tool_name, raw_args)`` ready for ``canonicalize_tool``.

    Decides ``Write`` vs ``Edit`` based on the patch shape: empty
    ``old_text`` + full ``new_text`` → ``Write``; both present → ``Edit``.
    """
    path = item.get("path") or item.get("file_path")
    old_text = item.get("old_text", item.get("oldText", ""))
    new_text = item.get("new_text", item.get("newText", ""))
    if not old_text and new_text:
        return "Write", {"path": path, "content": new_text}
    return "Edit", {
        "path": path,
        "old_text": old_text,
        "new_text": new_text,
    }


def _emit_mcp_tool_call(item: dict[str, Any], now: datetime) -> Iterable[AgentEvent]:
    """mcpToolCall item start → ToolCall (+ ArtifactMarker for our tools).

    Atelier's artifact tools (``record_pr`` / ``record_jira`` /
    ``record_doc``) come through here when the model invokes them via
    the MCP server we register in ``_thread_kwargs``. Emit the marker
    on the side; the regular ToolCall still flows so the chat shows the
    agent's exact invocation.
    """
    tool_name = str(item.get("tool") or item.get("name") or "")
    arguments = item.get("arguments") or item.get("input") or {}
    if not isinstance(arguments, dict):
        arguments = {}
    payload = marker_payload_for_tool(tool_name, dict(arguments))
    if payload is not None:
        yield ArtifactMarker(ts=now, payload=payload)
    canon_name, canon_args = canonicalize_tool(tool_name, dict(arguments))
    yield ToolCall(
        ts=now,
        tool_id=str(item.get("id") or item.get("itemId") or ""),
        name=canon_name,
        arguments=canon_args,
    )


def _emit_mcp_tool_result(
    item: dict[str, Any], now: datetime
) -> Iterable[AgentEvent]:
    """mcpToolCall completion → ToolResult."""
    yield ToolResult(
        ts=now,
        tool_id=str(item.get("id") or item.get("itemId") or ""),
        content=_coerce_str(
            item.get("result") or item.get("content") or item.get("text") or ""
        ),
        is_error=bool(item.get("error")) or item.get("status") == "failed",
    )


def _metrics_from_turn(
    params: dict[str, Any],
    now: datetime,
    model: str | None,
    last_prompt_tokens: int,
    context_window: int | None = None,
) -> Iterable[TurnMetrics]:
    usage = params.get("usage") or {}
    input_tokens, cache_read_tokens = _split_prompt_usage(usage)
    yield TurnMetrics(
        ts=now,
        duration_ms=int(params.get("duration_ms", 0) or 0),
        input_tokens=input_tokens,
        output_tokens=_non_negative_int(usage.get("output_tokens")),
        cache_read_input_tokens=cache_read_tokens,
        cache_creation_input_tokens=_non_negative_int(
            usage.get("cache_creation_input_tokens")
        ),
        last_prompt_tokens=last_prompt_tokens,
        model=model,
        context_window=context_window,
    )


def _token_snapshot_from_notification(
    notification: Notification,
) -> _TokenSnapshot | None:
    if notification.type == "token_count":
        return _token_snapshot_from_token_count_payload(notification.params)
    prompt_tokens = _per_call_prompt_tokens(notification)
    if prompt_tokens is None:
        return None
    return _TokenSnapshot(
        last_prompt_tokens=prompt_tokens,
    )


def _token_snapshot_from_token_count_payload(
    payload: dict[str, Any],
) -> _TokenSnapshot | None:
    info = payload.get("info")
    if not isinstance(info, dict):
        return None
    usage = info.get("last_token_usage")
    if not isinstance(usage, dict):
        return None
    return _TokenSnapshot(
        last_prompt_tokens=_prompt_tokens_from_usage(usage),
        context_window=_positive_int(info.get("model_context_window")),
    )


def _per_call_prompt_tokens(notification: Notification) -> int | None:
    """Pull a prompt snapshot off an agent-message usage payload, if present.

    Codex's ``item/completed`` for ``agentMessage`` *may* carry a
    ``usage`` block. If it's absent we leave ``last_prompt_tokens`` at
    the previous value rather than using cumulative turn totals.
    """
    if notification.type != "item/completed":
        return None
    item = notification.params.get("item") or notification.params
    if (item.get("itemType") or item.get("type")) != "agentMessage":
        return None
    usage = item.get("usage")
    if not isinstance(usage, dict):
        return None
    return _prompt_tokens_from_usage(usage)


def _prompt_tokens_from_usage(usage: dict[str, Any]) -> int:
    """OpenAI's ``input_tokens`` already includes cached input tokens.

    Anthropic-style shapes split prompt categories into non-overlapping
    ``input`` / ``cache_read`` / ``cache_creation`` buckets. Codex's
    token-count event follows OpenAI usage semantics where
    ``cached_input_tokens`` is a subset of ``input_tokens``, so for context
    we prefer the full prompt input count and only fall back to summing
    split buckets for older/future shapes.
    """
    input_tokens = _non_negative_int(usage.get("input_tokens"))
    if "cached_input_tokens" in usage:
        return input_tokens
    return (
        input_tokens
        + _non_negative_int(usage.get("cache_read_input_tokens"))
        + _non_negative_int(usage.get("cache_creation_input_tokens"))
    )


def _split_prompt_usage(usage: dict[str, Any]) -> tuple[int, int]:
    """Map Codex/OpenAI prompt usage into Atelier's non-overlapping buckets."""
    input_tokens = _non_negative_int(usage.get("input_tokens"))
    cached_tokens = _non_negative_int(usage.get("cached_input_tokens"))
    if cached_tokens:
        return max(0, input_tokens - cached_tokens), cached_tokens
    return input_tokens, _non_negative_int(usage.get("cache_read_input_tokens"))


def _non_negative_int(value: Any) -> int:
    if isinstance(value, bool):
        return 0
    if isinstance(value, int):
        return max(0, value)
    if isinstance(value, float):
        return max(0, int(value))
    return 0


def _positive_int(value: Any) -> int | None:
    parsed = _non_negative_int(value)
    return parsed if parsed > 0 else None


def _coerce_str(value: Any) -> str:
    """Best-effort string coercion for SDK fields that vary in type.

    Some SDK paths surface ``text`` as a plain string; others wrap it
    in a list of content blocks (``[{"type": "text", "text": "..."}]``)
    or hand back a structured object. We keep the adapter forgiving so
    a new SDK shape doesn't crash the pump — at worst the transcript
    carries a JSON-serialised payload, which still renders.
    """
    if value is None:
        return ""
    if isinstance(value, str):
        return value
    if isinstance(value, list):
        parts: list[str] = []
        for block in value:
            if isinstance(block, dict):
                text = block.get("text")
                if isinstance(text, str):
                    parts.append(text)
                else:
                    parts.append(json.dumps(block))
            else:
                parts.append(str(block))
        return "".join(parts)
    if isinstance(value, dict):
        text = value.get("text")
        if isinstance(text, str):
            return text
        return json.dumps(value)
    return str(value)


# ---------------------------------------------------------------------------
# Production client factory
# ---------------------------------------------------------------------------


def _default_client_factory() -> CodexClient:
    """Return a fresh Codex app-server client.

    The app-server protocol is the only local Codex transport that exposes
    approval requests as bidirectional JSON-RPC. ``openai-codex-sdk``
    remains installed for type/CLI compatibility, but its exec transport
    cannot round-trip approvals into Atelier.
    """
    return _CodexAppServerClient()


# ---------------------------------------------------------------------------
# Factory registration
# ---------------------------------------------------------------------------


@build_adapter.register
def _build_codex_adapter(
    config: CodexAgentConfig, settings: Settings
) -> AgentAdapter:
    return CodexAdapter(config)


__all__ = ["ClientFactory", "CodexAdapter"]
