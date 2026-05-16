"""OpenAI Codex SDK adapter.

Wraps ``openai-codex-sdk`` so the supervisor can drive a Codex session
through the project's ``AgentAdapter`` Protocol. Mirrors the structure of
the Claude and Amp adapters — pump-pattern queues, in-process artifact
MCP wiring (subprocess form, same as Amp), per-tool permission gating —
adapted to Codex's JSON-RPC notification shapes.

Lifecycle:
  start()       — open AsyncCodex client, call ``thread_start`` (or
                  ``thread_resume`` when ``context.session_id`` is set).
                  No first turn is kicked off; the first user input drives
                  ``turn_start``.
  events()      — async generator: drain ``_outgoing`` (fed by a side
                  pump task that forwards Codex notifications + approval
                  events into the queue). Same decoupling rationale as
                  the Claude adapter — server-initiated approvals run
                  inline on the SDK transport and must publish a
                  ``PermissionRequest`` and then await a future without
                  starving the supervisor.
  send_input(t) — enqueue user text; pump consumes and forwards
  resolve_permission(rid, decision) — answers an open approval request
  stop_turn()   — ``turn.interrupt()`` against the in-flight turn (real
                  cancel, not a no-op like Amp)
  close()       — exit the SDK context (closes the Codex subprocess);
                  idempotent.

SDK seam: production wires the real ``openai-codex-sdk`` via a thin
``_default_client_factory`` that lazy-imports the SDK at first call.
Tests inject a fake factory that yields scripted notifications matching
the local Protocols (``CodexClient`` / ``CodexThread`` / ``CodexTurnHandle``
/ ``Notification`` defined below). Keeps the module importable even when
the SDK isn't installed — symmetric with Amp's ``executor`` DI.

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
import sys
import uuid
from collections.abc import AsyncIterator, Callable, Iterable
from contextlib import suppress
from datetime import UTC, datetime
from typing import Any, Protocol

from src.domain.agents import (
    AgentAdapter,
    AgentEvent,
    AgentStartContext,
    ArtifactMarker,
    CodexAgentConfig,
    Error,
    MessageComplete,
    MessageDelta,
    PermissionDecision,
    PermissionDecisionValue,
    PermissionRequest,
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

    id: str

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
    ) -> CodexThread: ...

    def on_approval_request(
        self, callback: Callable[[ApprovalRequest], Any]
    ) -> None:
        """Register an async callback the SDK invokes when Codex asks for
        approval. The callback must return ``"accept"`` or ``"decline"``.

        The SDK exposes server-initiated approvals either as a callback or
        as a special notification in the turn stream; the plan describes
        both shapes. We use the callback path because it's the cleaner
        seam — the adapter resolves the future from inside the callback
        and the SDK round-trips the response itself.
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


ClientFactory = Callable[[], CodexClient]
"""DI seam — tests inject a factory returning a fake CodexClient."""


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
    ) -> None:
        self._config = config
        # Production builder injects ``_default_client_factory`` (lazy SDK
        # import); tests pass a fake. Either way the adapter only sees
        # the ``CodexClient`` Protocol.
        self._client_factory: ClientFactory = (
            client_factory or _default_client_factory
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
        # Register the approval callback before any turn starts so an
        # approval that fires on the very first turn can't race the
        # registration.
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
        # Emit ``SessionEstablished`` as soon as the SDK assigns us a
        # thread id; supervisor persists it for next-time resume.
        await self._outgoing.put(
            SessionEstablished(ts=datetime.now(UTC), session_id=thread.id)
        )
        self._reported_session_id = thread.id
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
            # display. Codex's ``turn/completed`` payload typically
            # exposes cumulative tokens; if per-call usage is unavailable
            # the helper falls back to ``input + cache_read +
            # cache_creation`` from the latest item with usage. Same
            # accuracy/clarity caveat as Claude — documented on
            # ``TurnMetrics``.
            last_prompt_tokens = 0
            try:
                turn = await thread.turn_start(text)
                self._current_turn = turn
                async for notification in turn.stream():
                    per_call = _per_call_prompt_tokens(notification)
                    if per_call is not None:
                        last_prompt_tokens = per_call
                    for ev in _convert(
                        notification,
                        model=model,
                        last_prompt_tokens=last_prompt_tokens,
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
        return {
            "model": self._config.model.value,
            "cwd": str(self._config.common.workdir),
            "sandbox": self._config.sandbox.value,
            "approval_mode": self._config.approval_mode.value,
            "base_instructions": context.system_prompt,
            "mcp_servers": _build_atelier_mcp_servers(),
            "config_overrides": {
                "model_reasoning_effort": self._config.reasoning_effort.value,
            },
        }


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


# ---------------------------------------------------------------------------
# Notification → AgentEvent mapping
# ---------------------------------------------------------------------------


def _convert(
    notification: Notification,
    *,
    model: str | None = None,
    last_prompt_tokens: int = 0,
) -> Iterable[AgentEvent]:
    """Map one Codex notification onto zero-or-more ``AgentEvent``.

    Discriminator is ``notification.type``. The Codex SDK delivers a
    three-state lifecycle per item: ``item/started`` → optional deltas
    → ``item/completed``. Each item carries an ``itemType`` (e.g.
    ``agentMessage``, ``reasoning``, ``commandExecution``, ``fileChange``,
    ``mcpToolCall``) that drives the mapping.

    ``model`` lets the adapter stamp per-turn metrics with the configured
    Codex model id. ``last_prompt_tokens`` is the prompt size of the
    latest sub-call — see ``TurnMetrics`` for the full rationale.
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
        yield from _metrics_from_turn(params, now, model, last_prompt_tokens)
        yield StatusChange(ts=now, status="idle")
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
) -> Iterable[TurnMetrics]:
    usage = params.get("usage") or {}
    yield TurnMetrics(
        ts=now,
        duration_ms=int(params.get("duration_ms", 0) or 0),
        input_tokens=int(usage.get("input_tokens", 0) or 0),
        output_tokens=int(usage.get("output_tokens", 0) or 0),
        cache_read_input_tokens=int(usage.get("cache_read_input_tokens", 0) or 0),
        cache_creation_input_tokens=int(
            usage.get("cache_creation_input_tokens", 0) or 0
        ),
        last_prompt_tokens=last_prompt_tokens,
        model=model,
    )


def _per_call_prompt_tokens(notification: Notification) -> int | None:
    """Pull ``input + cache_read + cache_creation`` off a per-call usage
    payload if Codex surfaces one mid-turn, else ``None``.

    Codex's ``item/completed`` for ``agentMessage`` *may* carry a
    ``usage`` block (the SDK's per-call shape mirrors Anthropic's). If
    it's absent we leave ``last_prompt_tokens`` at the previous value;
    the ``turn/completed`` aggregate is the fallback documented in the
    plan (cumulative-as-approximation). See ``TurnMetrics`` for the
    rationale on why this is the right denominator for ctx%.
    """
    if notification.type != "item/completed":
        return None
    item = notification.params.get("item") or notification.params
    if (item.get("itemType") or item.get("type")) != "agentMessage":
        return None
    usage = item.get("usage")
    if not isinstance(usage, dict):
        return None
    return (
        int(usage.get("input_tokens", 0) or 0)
        + int(usage.get("cache_read_input_tokens", 0) or 0)
        + int(usage.get("cache_creation_input_tokens", 0) or 0)
    )


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
# Production client factory — lazy SDK import
# ---------------------------------------------------------------------------


def _default_client_factory() -> CodexClient:
    """Lazy-import the Codex SDK and return a fresh ``AsyncCodex`` client.

    Import happens at call time (not module load) so the adapter module
    stays importable even when ``openai-codex-sdk`` isn't installed —
    the missing dependency only fails the agent actually creating a
    Codex session, not the whole backend. Mirrors the way Claude /
    Amp resolve their SDKs at runtime.
    """
    try:
        from openai_codex_sdk import AsyncCodex
    except ImportError as exc:  # pragma: no cover - exercised on machines without the SDK
        raise RuntimeError(
            "openai-codex-sdk is not installed. Run "
            "``uv add openai-codex-sdk`` (or pip install) in the backend "
            "to enable Codex agents."
        ) from exc
    client: CodexClient = AsyncCodex()
    return client


# ---------------------------------------------------------------------------
# Factory registration
# ---------------------------------------------------------------------------


@build_adapter.register
def _build_codex_adapter(
    config: CodexAgentConfig, settings: Settings
) -> AgentAdapter:
    return CodexAdapter(config)


__all__ = ["ClientFactory", "CodexAdapter"]
