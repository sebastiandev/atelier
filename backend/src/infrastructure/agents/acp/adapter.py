"""ACP client adapter: one ``AgentAdapter`` for every ACP-backed provider.

Spawns the agent CLI/wrapper as a subprocess, speaks the Agent Client
Protocol over its stdio via the official ``agent-client-protocol`` SDK,
and maps ``session/update`` notifications onto Atelier's ``AgentEvent``
union (see ``mapping.py``). Per-provider differences live entirely in
the ``AcpAgentConfig`` subclass (which knobs → which protocol config
options) and the spawn argv passed by the factory registration — this
class contains zero provider branches.

Lifecycle mirrors the Claude adapter's queue choreography:
  start()       — spawn subprocess, ``initialize``, create/load/resume
                  the session (MCP artifact server injected), apply
                  config options + mode tolerantly
  events()      — drain ``_outgoing``; a side input pump runs prompts so
                  ``request_permission`` (which arrives as an inbound
                  RPC and awaits a user decision) can emit its
                  ``PermissionRequest`` without starving the supervisor
  send_input(t) — enqueue user text; pump wraps it in a text block
  stop_turn()   — ``session/cancel`` + answer open permission requests
                  with the protocol's ``cancelled`` outcome
  close()       — idempotent; cancels pending permissions, closes the
                  connection, terminates the subprocess

System prompt: ACP has no system-prompt parameter. Atelier's persona /
work brief is prepended to the *first* prompt of a fresh session as a
fenced context block; resumed sessions already carry it in-history.
"""

import asyncio
import logging
import os
import re
import signal
import sys
import time
import uuid
from collections.abc import AsyncIterator, Sequence
from contextlib import suppress
from datetime import UTC, datetime
from typing import TYPE_CHECKING, Any, Protocol, cast

from acp import connect_to_agent

if TYPE_CHECKING:
    from acp import Client
from acp.schema import (
    AllowedOutcome,
    ConfigOptionUpdate,
    DeniedOutcome,
    McpServerStdio,
    PermissionOption,
    RequestPermissionResponse,
)

from src.domain.agents import (
    AcpAgentConfig,
    AgentEvent,
    AgentStartContext,
    Error,
    PermissionDecision,
    PermissionDecisionValue,
    PermissionRequest,
    SessionConfigChanged,
    SessionConfigOptions,
    SessionEstablished,
    StatusChange,
    TurnMetrics,
)
from src.infrastructure.agents.acp.mapping import AcpUpdateMapper
from src.infrastructure.agents.atelier_mcp_tools import MCP_SERVER_NAME
from src.infrastructure.agents.tool_canonical import canonicalize_tool

logger = logging.getLogger(__name__)

_SHUTDOWN = object()
_RECOVERY_PROMPT = (
    "Continue from where you left off after the transport reconnect. "
    "Do not repeat completed tool calls unless their results are missing."
)
_MAX_RECOVERY_ATTEMPTS_PER_TURN = 8
_ACP_STDIO_BUFFER_LIMIT_BYTES = 50 * 1024 * 1024

# Atelier decision → acceptable ACP option kinds, most-specific first.
_DECISION_KINDS: dict[PermissionDecisionValue, tuple[str, ...]] = {
    "allow": ("allow_once", "allow_always"),
    "allow_always": ("allow_always", "allow_once"),
    "deny": ("reject_once", "reject_always"),
}

_CANCELLED = "__cancelled__"  # sentinel decision for protocol-mandated cancel


class AcpConnection(Protocol):
    """The slice of ``acp.client.ClientSideConnection`` the adapter uses.

    Tests substitute a fake; production wires the real connection built
    by ``connect_to_agent`` over the subprocess's stdio.
    """

    async def initialize(self, protocol_version: int, **kwargs: Any) -> Any: ...

    async def new_session(self, cwd: str, **kwargs: Any) -> Any: ...

    async def load_session(self, cwd: str, session_id: str, **kwargs: Any) -> Any: ...

    async def resume_session(self, cwd: str, session_id: str, **kwargs: Any) -> Any: ...

    async def prompt(self, prompt: list[Any], session_id: str, **kwargs: Any) -> Any: ...

    async def cancel(self, session_id: str, **kwargs: Any) -> None: ...

    async def set_config_option(
        self, config_id: str, session_id: str, value: str | bool, **kwargs: Any
    ) -> Any: ...

    async def set_session_mode(self, mode_id: str, session_id: str, **kwargs: Any) -> Any: ...

    async def close(self) -> None: ...


class AcpAdapter:
    """Adapter that streams any ACP agent session as AgentEvents."""

    def __init__(
        self,
        config: AcpAgentConfig,
        argv: Sequence[str],
        *,
        model_label: str | None = None,
        connect_factory: Any = None,
    ) -> None:
        """``connect_factory`` is the test seam: an async callable
        ``(adapter) -> AcpConnection`` that replaces the subprocess +
        stdio wiring. Production leaves it ``None``."""
        self._config = config
        self._argv = tuple(argv)
        self._model_label = model_label
        self._connect_factory = connect_factory
        self._proc: asyncio.subprocess.Process | None = None
        self._conn: AcpConnection | None = None
        self._session_id: str | None = None
        self._fresh_session = False
        self._pending_warning: str | None = None
        self._mapper = AcpUpdateMapper()
        self._session_config_options: tuple[dict[str, Any], ...] = ()
        self._advertised_config_values: dict[str, set[str | bool]] = {}
        self._replaying = False
        self._suppress_restored_updates_until_prompt = False
        self._user_inputs: asyncio.Queue[str | object] = asyncio.Queue()
        self._outgoing: asyncio.Queue[AgentEvent | object] = asyncio.Queue()
        self._pump_task: asyncio.Task[None] | None = None
        self._stderr_task: asyncio.Task[None] | None = None
        self._closed = False
        self._terminal_error: BaseException | None = None
        self._restored_session_id: str | None = None
        self._fresh_fallback_used = False
        self._pending: dict[str, asyncio.Future[str]] = {}
        self._pending_options: dict[str, list[PermissionOption]] = {}
        self._decided: set[str] = set()

    # -- AgentAdapter surface ---------------------------------------------------

    async def start(self, context: AgentStartContext) -> None:
        if self._conn is not None:
            raise RuntimeError("start() called twice")
        self._conn = await self._connect()
        init = await self._conn.initialize(protocol_version=1)
        if getattr(init, "protocol_version", 1) != 1:
            raise RuntimeError(
                f"ACP protocol version mismatch: agent wants "
                f"{init.protocol_version}, Atelier speaks 1"
            )
        caps = getattr(init, "agent_capabilities", None)
        cwd = str(self._config.common.workdir)
        mcp_servers = [_atelier_mcp_server()]

        session_response: Any = None
        if context.session_id is not None:
            session_response = await self._restore_session(
                caps, cwd, context.session_id, mcp_servers
            )
        if self._session_id is None:
            session_response = await self._conn.new_session(
                cwd=cwd, mcp_servers=mcp_servers
            )
            self._session_id = session_response.session_id
            self._fresh_session = True
        self._session_config_options = _config_options_payload(session_response)
        self._advertised_config_values = _advertised_options(session_response)
        await self._apply_session_settings(session_response)

    async def send_input(self, text: str) -> None:
        if self._closed:
            raise RuntimeError("ACP session is closed")
        if self._terminal_error is not None:
            raise self._terminal_error
        await self._user_inputs.put(text)

    async def stop_turn(self) -> None:
        if self._conn is None or self._closed or self._session_id is None:
            return
        # Protocol contract: after session/cancel the client MUST answer
        # any pending permission requests with the ``cancelled`` outcome.
        for fut in list(self._pending.values()):
            if not fut.done():
                fut.set_result(_CANCELLED)
        try:
            await self._conn.cancel(session_id=self._session_id)
        except Exception:
            # Transient transport state between turns — the next
            # send_input fails loudly if something is actually broken.
            pass

    async def resolve_permission(
        self, request_id: str, decision: PermissionDecisionValue
    ) -> None:
        fut = self._pending.get(request_id)
        if fut is None or fut.done():
            return  # stale / duplicate frame after a WS reconnect
        fut.set_result(decision)

    async def set_config_option(self, config_id: str, value: str | bool) -> None:
        if self._conn is None or self._closed or self._session_id is None:
            raise RuntimeError("ACP session is not running")
        allowed = self._advertised_config_values.get(config_id)
        if allowed is None:
            raise ValueError(f"session config option is not advertised: {config_id}")
        if allowed and value not in allowed:
            raise ValueError(
                f"session config option {config_id} does not allow value {value!r}"
            )
        response = await self._conn.set_config_option(
            config_id=config_id, session_id=self._session_id, value=value
        )
        updated = self._record_config_options_response(
            response, fallback_config_id=config_id, fallback_value=value
        )
        if config_id == "model" and isinstance(value, str):
            self._model_label = value
        if updated:
            await self._outgoing.put(
                SessionConfigOptions(ts=_now(), options=self._session_config_options)
            )
        await self._outgoing.put(
            SessionConfigChanged(ts=_now(), config_id=config_id, value=value)
        )

    async def refresh_config_options(self, config_id: str) -> None:
        if self._conn is None or self._closed or self._session_id is None:
            raise RuntimeError("ACP session is not running")
        option = _find_config_option(self._session_config_options, config_id)
        if option is None:
            raise ValueError(f"session config option is not advertised: {config_id}")
        current = option.get("current_value")
        if not isinstance(current, (str, bool)):
            await self._outgoing.put(
                SessionConfigOptions(ts=_now(), options=self._session_config_options)
            )
            return
        response = await self._conn.set_config_option(
            config_id=config_id, session_id=self._session_id, value=current
        )
        self._record_config_options_response(
            response, fallback_config_id=config_id, fallback_value=current
        )
        if config_id == "model" and isinstance(current, str):
            self._model_label = current
        await self._outgoing.put(
            SessionConfigOptions(ts=_now(), options=self._session_config_options)
        )

    async def events(self) -> AsyncIterator[AgentEvent]:
        if self._conn is None:
            raise RuntimeError("events() called before start()")
        if self._pending_warning is not None:
            yield Error(ts=_now(), message=self._pending_warning)
            self._pending_warning = None
        if self._session_id is not None:
            yield SessionEstablished(ts=_now(), session_id=self._session_id)
        if self._session_config_options:
            yield SessionConfigOptions(ts=_now(), options=self._session_config_options)
        self._pump_task = asyncio.create_task(
            self._run_input_pump(), name="acp-input-pump"
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

    async def close(self) -> None:
        if self._closed:
            return
        self._closed = True
        for request_id, fut in list(self._pending.items()):
            if request_id not in self._decided:
                self._decided.add(request_id)
                await self._outgoing.put(
                    PermissionDecision(
                        ts=_now(), request_id=request_id, decision="deny"
                    )
                )
            if not fut.done():
                fut.set_result(_CANCELLED)
        await self._user_inputs.put(_SHUTDOWN)
        await self._close_transport()

    async def _close_transport(self) -> None:
        if self._conn is not None:
            with suppress(Exception):
                await self._conn.close()
            self._conn = None
        if self._proc is not None and self._proc.returncode is None:
            await _terminate_process_tree(self._proc)
        self._proc = None
        if self._stderr_task is not None and not self._stderr_task.done():
            self._stderr_task.cancel()
            with suppress(asyncio.CancelledError):
                await self._stderr_task
        self._stderr_task = None

    # -- ACP client callbacks (invoked by the SDK router) -----------------------

    async def session_update(self, session_id: str, update: Any, **kwargs: Any) -> None:
        if self._replaying:
            # session/load replays history as ordinary updates before its
            # response resolves; Atelier's own transcript already holds
            # those turns, so re-emitting would duplicate them.
            return
        if self._suppress_restored_updates_until_prompt and _is_replay_update(update):
            # Some ACP agents emit restored-history updates just after
            # load/resume resolves. Normal live output starts after
            # prompt(); suppress only content-shaped updates during this
            # restored-but-not-prompting phase.
            return
        if isinstance(update, ConfigOptionUpdate):
            if self._record_config_options_response(update):
                await self._outgoing.put(
                    SessionConfigOptions(
                        ts=_now(), options=self._session_config_options
                    )
                )
            return
        for event in self._mapper.handle(update):
            await self._outgoing.put(event)

    async def request_permission(
        self,
        options: list[PermissionOption],
        session_id: str,
        tool_call: Any,
        **kwargs: Any,
    ) -> RequestPermissionResponse:
        if self._config.summary_only:
            # Summary/compaction sessions never run tools: auto-reject
            # without emitting events so the transcript stays clean.
            option = _pick_option(list(options), "deny")
            if option is None:
                return RequestPermissionResponse(
                    outcome=DeniedOutcome(outcome="cancelled")
                )
            return RequestPermissionResponse(
                outcome=AllowedOutcome(outcome="selected", option_id=option.option_id)
            )
        request_id = uuid.uuid4().hex
        fut: asyncio.Future[str] = asyncio.get_running_loop().create_future()
        self._pending[request_id] = fut
        self._pending_options[request_id] = list(options)
        raw_input = getattr(tool_call, "raw_input", None)
        tool_id = getattr(tool_call, "tool_call_id", None)
        tool_id_str = tool_id if isinstance(tool_id, str) else None
        provider_name = (
            self._mapper.provider_tool_name_for(tool_id_str)
            or _provider_name_from_permission_options(options)
            or getattr(tool_call, "title", None)
            or "tool"
        )
        canon_name, canon_input = canonicalize_tool(
            provider_name, dict(raw_input) if isinstance(raw_input, dict) else {}
        )
        await self._outgoing.put(
            PermissionRequest(
                ts=_now(),
                request_id=request_id,
                tool_name=canon_name,
                tool_input=canon_input,
                options=tuple(
                    {"option_id": o.option_id, "name": o.name, "kind": o.kind}
                    for o in options
                ),
                tool_id=tool_id_str,
            )
        )
        try:
            try:
                decision = await fut
            except asyncio.CancelledError:
                decision = _CANCELLED
        finally:
            self._pending.pop(request_id, None)
            option_pool = self._pending_options.pop(request_id, list(options))
        if decision == _CANCELLED:
            if request_id not in self._decided:
                self._decided.add(request_id)
                await asyncio.shield(
                    self._outgoing.put(
                        PermissionDecision(
                            ts=_now(), request_id=request_id, decision="deny"
                        )
                    )
                )
            return RequestPermissionResponse(
                outcome=DeniedOutcome(outcome="cancelled")
            )
        assert decision in ("allow", "allow_always", "deny")
        if request_id not in self._decided:
            self._decided.add(request_id)
            await asyncio.shield(
                self._outgoing.put(
                    PermissionDecision(
                        ts=_now(),
                        request_id=request_id,
                        decision=decision,  # type: ignore[arg-type]
                    )
                )
            )
        option = _pick_option(option_pool, decision)  # type: ignore[arg-type]
        if option is None:
            # The agent offered no option in this direction — treat a
            # deny with no reject options as cancelled, an allow with no
            # allow options as the protocol-safe cancel too.
            return RequestPermissionResponse(
                outcome=DeniedOutcome(outcome="cancelled")
            )
        return RequestPermissionResponse(
            outcome=AllowedOutcome(outcome="selected", option_id=option.option_id)
        )

    def on_connect(self, conn: Any) -> None:  # SDK hook; the adapter
        return None  # already holds the connection it built.

    async def ext_method(self, method: str, params: dict[str, Any]) -> dict[str, Any]:
        logger.debug("ignoring ACP extension method %s", method)
        return {}

    async def ext_notification(self, method: str, params: dict[str, Any]) -> None:
        logger.debug("ignoring ACP extension notification %s", method)

    # fs / terminal capabilities are not advertised in v1; a conforming
    # agent never calls these. Fail loudly if one does anyway.
    async def read_text_file(self, **kwargs: Any) -> Any:
        raise RuntimeError("fs capability not advertised")

    async def write_text_file(self, **kwargs: Any) -> Any:
        raise RuntimeError("fs capability not advertised")

    async def create_terminal(self, **kwargs: Any) -> Any:
        raise RuntimeError("terminal capability not advertised")

    async def terminal_output(self, **kwargs: Any) -> Any:
        raise RuntimeError("terminal capability not advertised")

    async def release_terminal(self, **kwargs: Any) -> Any:
        raise RuntimeError("terminal capability not advertised")

    async def wait_for_terminal_exit(self, **kwargs: Any) -> Any:
        raise RuntimeError("terminal capability not advertised")

    async def kill_terminal(self, **kwargs: Any) -> Any:
        raise RuntimeError("terminal capability not advertised")

    # -- internals -------------------------------------------------------------

    async def _connect(self) -> AcpConnection:
        if self._connect_factory is not None:
            result = await self._connect_factory(self)
            return result  # type: ignore[no-any-return]
        self._proc = await asyncio.create_subprocess_exec(
            *self._argv,
            stdin=asyncio.subprocess.PIPE,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            cwd=str(self._config.common.workdir),
            start_new_session=os.name == "posix",
            limit=_ACP_STDIO_BUFFER_LIMIT_BYTES,
        )
        assert self._proc.stdin is not None and self._proc.stdout is not None
        self._stderr_task = asyncio.create_task(
            self._drain_stderr(), name="acp-stderr-drain"
        )
        # The adapter implements the SDK's ``Client`` protocol with
        # kwargs-only stubs for the capabilities it doesn't advertise —
        # structurally fine at runtime, hence the cast.
        return connect_to_agent(
            cast("Client", self), self._proc.stdin, self._proc.stdout
        )

    async def _drain_stderr(self) -> None:
        assert self._proc is not None and self._proc.stderr is not None
        while True:
            line = await self._proc.stderr.readline()
            if not line:
                return
            logger.debug(
                "acp[%s] stderr: %s",
                self._argv[0],
                line.decode(errors="replace").rstrip(),
            )

    async def _restore_session(
        self,
        caps: Any,
        cwd: str,
        session_id: str,
        mcp_servers: list[Any],
    ) -> Any:
        """Try load (with replay suppressed) → resume → fall back to fresh."""
        assert self._conn is not None
        session_caps = getattr(caps, "session_capabilities", None)
        try:
            if getattr(caps, "load_session", False):
                self._replaying = True
                try:
                    response = await self._conn.load_session(
                        cwd=cwd, session_id=session_id, mcp_servers=mcp_servers
                    )
                finally:
                    self._replaying = False
                self._session_id = session_id
                self._restored_session_id = session_id
                self._suppress_restored_updates_until_prompt = True
                return response
            if (
                session_caps is not None
                and getattr(session_caps, "resume", None) is not None
            ):
                response = await self._conn.resume_session(
                    cwd=cwd, session_id=session_id, mcp_servers=mcp_servers
                )
                self._session_id = session_id
                self._restored_session_id = session_id
                self._suppress_restored_updates_until_prompt = True
                return response
            self._pending_warning = (
                "Agent does not support session restore; started a fresh session."
            )
        except Exception as exc:
            self._pending_warning = (
                f"Previous session not restored ({exc}); started a fresh one."
            )
        return None

    async def _start_fresh_session_after_restore(self) -> bool:
        if (
            self._restored_session_id is None
            or self._fresh_fallback_used
            or self._closed
        ):
            return False
        old_session_id = self._restored_session_id
        self._fresh_fallback_used = True
        try:
            await self._close_transport()
            self._conn = await self._connect()
            init = await self._conn.initialize(protocol_version=1)
            if getattr(init, "protocol_version", 1) != 1:
                return False
            assert self._conn is not None
            response = await self._conn.new_session(
                cwd=str(self._config.common.workdir),
                mcp_servers=[_atelier_mcp_server()],
            )
            session_id = getattr(response, "session_id", None)
            if not isinstance(session_id, str) or not session_id:
                return False
            self._session_id = session_id
            self._restored_session_id = None
            self._fresh_session = True
            self._terminal_error = None
            self._session_config_options = _config_options_payload(response)
            self._advertised_config_values = _advertised_options(response)
            await self._apply_session_settings(response)
            await self._outgoing.put(
                Error(
                    ts=_now(),
                    message=(
                        "Previous provider session disconnected; "
                        "started a fresh session."
                    ),
                )
            )
            await self._outgoing.put(
                SessionEstablished(ts=_now(), session_id=session_id)
            )
            logger.info(
                "acp: replaced disconnected restored session %s with fresh %s",
                old_session_id,
                session_id,
            )
            return True
        except Exception:
            logger.exception(
                "acp: failed to replace disconnected restored session %s",
                old_session_id,
            )
            return False

    async def _apply_session_settings(self, session_response: Any) -> None:
        """Apply config options + mode from the typed config, tolerantly."""
        assert self._conn is not None and self._session_id is not None
        advertised = self._advertised_config_values
        for config_id, value in self._config.acp_config_values():
            allowed = advertised.get(config_id)
            if allowed is not None and value not in allowed:
                logger.debug(
                    "acp: skipping config option %s=%s (agent advertises %s)",
                    config_id, value, sorted(allowed),
                )
                continue
            try:
                response = await self._conn.set_config_option(
                    config_id=config_id, session_id=self._session_id, value=value
                )
                self._record_config_options_response(
                    response, fallback_config_id=config_id, fallback_value=value
                )
                if config_id == "model" and isinstance(value, str):
                    self._model_label = value
            except Exception as exc:
                logger.debug("acp: set_config_option %s failed: %s", config_id, exc)
        mode_id = self._config.acp_mode_id()
        if mode_id is not None:
            modes = getattr(session_response, "modes", None)
            available = {
                m.id for m in getattr(modes, "available_modes", []) or []
            }
            if available and mode_id not in available:
                logger.debug(
                    "acp: skipping mode %s (agent advertises %s)",
                    mode_id, sorted(available),
                )
                return
            try:
                await self._conn.set_session_mode(
                    mode_id=mode_id, session_id=self._session_id
                )
            except Exception as exc:
                logger.debug("acp: set_session_mode %s failed: %s", mode_id, exc)

    def _record_config_options_response(
        self,
        response: Any,
        *,
        fallback_config_id: str | None = None,
        fallback_value: str | bool | None = None,
    ) -> bool:
        options = _config_options_payload(response)
        if options:
            self._session_config_options = options
            self._advertised_config_values = _advertised_options(response)
            return True
        if fallback_config_id is None or fallback_value is None:
            return False
        next_options = _set_config_current(
            self._session_config_options, fallback_config_id, fallback_value
        )
        self._session_config_options = next_options
        return False

    async def _run_input_pump(self) -> None:
        assert self._conn is not None
        while True:
            text = await self._user_inputs.get()
            if text is _SHUTDOWN:
                await self._outgoing.put(_SHUTDOWN)
                return
            assert isinstance(text, str)
            await self._outgoing.put(StatusChange(ts=_now(), status="thinking"))
            started = time.monotonic()
            prompt_text = text
            original_prompt_text = text
            recovery_attempts = 0
            try:
                while True:
                    blocks = self._prompt_blocks(prompt_text)
                    assert self._conn is not None
                    try:
                        self._suppress_restored_updates_until_prompt = False
                        response = await self._conn.prompt(
                            prompt=blocks, session_id=self._session_id or ""
                        )
                        break
                    except Exception as e:
                        for event in self._mapper.flush_turn():
                            await self._outgoing.put(event)
                        if not _is_terminal_connection_error(e):
                            raise
                        recovery_attempts += 1
                        if (
                            prompt_text == _RECOVERY_PROMPT
                            and await self._start_fresh_session_after_restore()
                        ):
                            prompt_text = original_prompt_text
                            recovery_attempts = 0
                            continue
                        if recovery_attempts > _MAX_RECOVERY_ATTEMPTS_PER_TURN:
                            if await self._start_fresh_session_after_restore():
                                prompt_text = original_prompt_text
                                recovery_attempts = 0
                                continue
                            raise
                        recovered = await self._recover_connection()
                        if not recovered:
                            if await self._start_fresh_session_after_restore():
                                prompt_text = original_prompt_text
                                recovery_attempts = 0
                                continue
                            raise
                        prompt_text = _RECOVERY_PROMPT

                for event in self._mapper.flush_turn():
                    await self._outgoing.put(event)
                stop_reason = getattr(response, "stop_reason", "end_turn")
                if stop_reason == "refusal":
                    await self._outgoing.put(
                        Error(ts=_now(), message="The agent refused this request.")
                    )
                elif stop_reason in ("max_tokens", "max_turn_requests"):
                    await self._outgoing.put(
                        Error(
                            ts=_now(),
                            message=f"Turn stopped early: {stop_reason.replace('_', ' ')}.",
                        )
                    )
                await self._outgoing.put(
                    self._build_turn_metrics(response, started)
                )
                await self._outgoing.put(StatusChange(ts=_now(), status="idle"))
            except Exception as e:
                for event in self._mapper.flush_turn():
                    await self._outgoing.put(event)
                await self._outgoing.put(Error(ts=_now(), message=str(e)))
                if _is_terminal_connection_error(e):
                    self._terminal_error = e
                    await self._outgoing.put(_SHUTDOWN)
                    return
                await self._outgoing.put(StatusChange(ts=_now(), status="idle"))

    def _prompt_blocks(self, text: str) -> list[dict[str, Any]]:
        blocks: list[dict[str, Any]] = []
        if self._fresh_session and self._config.common.system_prompt:
            blocks.append(
                {
                    "type": "text",
                    "text": (
                        "<atelier-context>\n"
                        f"{self._config.common.system_prompt}\n"
                        "</atelier-context>"
                    ),
                }
            )
        self._fresh_session = False
        blocks.append({"type": "text", "text": text})
        return blocks

    async def _recover_connection(self) -> bool:
        session_id = self._session_id
        if session_id is None or self._closed:
            return False
        await self._close_transport()
        self._conn = await self._connect()
        init = await self._conn.initialize(protocol_version=1)
        if getattr(init, "protocol_version", 1) != 1:
            return False
        caps = getattr(init, "agent_capabilities", None)
        self._pending_warning = None
        self._session_id = None
        response = await self._restore_session(
            caps,
            str(self._config.common.workdir),
            session_id,
            [_atelier_mcp_server()],
        )
        if response is None or self._session_id != session_id:
            await self._close_transport()
            self._session_id = session_id
            return False
        self._session_config_options = _config_options_payload(response)
        self._advertised_config_values = _advertised_options(response)
        await self._apply_session_settings(response)
        self._fresh_session = False
        return True

    def _build_turn_metrics(self, response: Any, started: float) -> TurnMetrics:
        usage = getattr(response, "usage", None)
        snapshot = self._mapper.usage
        return TurnMetrics(
            ts=_now(),
            duration_ms=int((time.monotonic() - started) * 1000),
            input_tokens=int(getattr(usage, "input_tokens", 0) or 0),
            output_tokens=int(getattr(usage, "output_tokens", 0) or 0),
            cache_read_input_tokens=int(getattr(usage, "cached_read_tokens", 0) or 0),
            cache_creation_input_tokens=int(
                getattr(usage, "cached_write_tokens", 0) or 0
            ),
            last_prompt_tokens=snapshot.used,
            model=self._model_label,
            context_window=snapshot.size,
            cost_usd=snapshot.cost_usd,
        )


def _now() -> datetime:
    return datetime.now(UTC)


def _is_replay_update(update: Any) -> bool:
    return getattr(update, "session_update", None) in {
        "agent_message_chunk",
        "agent_thought_chunk",
        "tool_call",
        "tool_call_update",
        "plan",
        "current_mode_update",
        "usage_update",
    }


async def _terminate_process_tree(proc: asyncio.subprocess.Process) -> None:
    if proc.returncode is not None:
        return
    if os.name == "posix":
        with suppress(ProcessLookupError):
            os.killpg(os.getpgid(proc.pid), signal.SIGTERM)
        try:
            await asyncio.wait_for(proc.wait(), timeout=5)
            return
        except TimeoutError:
            with suppress(ProcessLookupError):
                os.killpg(os.getpgid(proc.pid), signal.SIGKILL)
            with suppress(Exception):
                await proc.wait()
            return
    proc.terminate()
    try:
        await asyncio.wait_for(proc.wait(), timeout=5)
    except TimeoutError:
        proc.kill()
        with suppress(Exception):
            await proc.wait()


def _atelier_mcp_server() -> McpServerStdio:
    """Atelier's artifact-recording MCP server, stdio shape.

    Same spawn the Amp adapter uses: the backend's own interpreter, so
    the module resolves through the editable install regardless of the
    agent subprocess's cwd.
    """
    return McpServerStdio(
        name=MCP_SERVER_NAME,
        command=sys.executable,
        args=["-m", "src.infrastructure.agents.atelier_mcp_server"],
        env=[],
    )


def _config_options_payload(session_response: Any) -> tuple[dict[str, Any], ...]:
    """JSON-friendly config option metadata from an ACP session response."""
    out: list[dict[str, Any]] = []
    for option in getattr(session_response, "config_options", None) or []:
        option_id = getattr(option, "id", None)
        if not isinstance(option_id, str) or not option_id:
            continue
        choices: list[dict[str, Any]] = []
        for choice in getattr(option, "options", None) or []:
            value = getattr(choice, "value", None)
            if not isinstance(value, (str, bool)):
                continue
            item: dict[str, Any] = {"value": value}
            name = getattr(choice, "name", None)
            if isinstance(name, str) and name:
                item["name"] = name
            description = getattr(choice, "description", None)
            if isinstance(description, str) and description:
                item["description"] = description
            choices.append(item)
        current = getattr(option, "current_value", None)
        item = {
            "id": option_id,
            "name": _string_attr(option, "name") or option_id,
            "type": _string_attr(option, "type") or "select",
            "current_value": current if isinstance(current, (str, bool)) else None,
            "options": tuple(choices),
        }
        category = _string_attr(option, "category")
        if category is not None:
            item["category"] = category
        out.append(item)
    return tuple(out)


def _set_config_current(
    options: tuple[dict[str, Any], ...], config_id: str, value: str | bool
) -> tuple[dict[str, Any], ...]:
    next_options: list[dict[str, Any]] = []
    for option in options:
        if option.get("id") == config_id:
            next_options.append({**option, "current_value": value})
        else:
            next_options.append(option)
    return tuple(next_options)


def _find_config_option(
    options: tuple[dict[str, Any], ...], config_id: str
) -> dict[str, Any] | None:
    for option in options:
        if option.get("id") == config_id:
            return option
    return None


def _string_attr(obj: Any, name: str) -> str | None:
    value = getattr(obj, name, None)
    return value if isinstance(value, str) and value else None


def _is_terminal_connection_error(exc: BaseException) -> bool:
    return isinstance(exc, ConnectionError)


def _advertised_options(session_response: Any) -> dict[str, set[str | bool]]:
    """``{config_id: allowed values}`` from a session response, tolerant
    of agents that return no configOptions at all."""
    out: dict[str, set[str | bool]] = {}
    for option in getattr(session_response, "config_options", None) or []:
        option_id = getattr(option, "id", None)
        if not isinstance(option_id, str) or not option_id:
            continue
        values: set[str | bool] = set()
        for choice in getattr(option, "options", None) or []:
            value = getattr(choice, "value", None)
            if isinstance(value, (str, bool)):
                values.add(value)
        out[option_id] = values
    return out


def _pick_option(
    options: list[PermissionOption], decision: PermissionDecisionValue
) -> PermissionOption | None:
    for kind in _DECISION_KINDS[decision]:
        for option in options:
            if option.kind == kind:
                return option
    return None


_OPTION_TOOL_RE = re.compile(
    r"^Always Allow(?: all)? ([A-Za-z][A-Za-z0-9_]*(?:__[A-Za-z0-9_]+)?)(?:\(|$)"
)


def _provider_name_from_permission_options(
    options: Sequence[PermissionOption],
) -> str | None:
    for option in options:
        if option.kind != "allow_always":
            continue
        match = _OPTION_TOOL_RE.match(option.name.strip())
        if match:
            return match.group(1)
    return None


__all__ = ["AcpAdapter", "AcpConnection"]
