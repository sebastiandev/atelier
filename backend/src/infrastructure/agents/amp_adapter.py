"""Amp Python SDK adapter.

Wraps ``amp_sdk.execute`` so the supervisor can drive an Amp session
through the project's ``AgentAdapter`` Protocol. The SDK shells out to
the local ``amp`` CLI, streaming Claude-Code-compatible JSONL events.

Multi-turn is supported by passing an async iterator of ``UserInputMessage``
as the prompt: the SDK keeps the CLI process alive (via
``--stream-json-input``) and forwards each message we yield. We bridge
``send_input`` → that iterator through an internal ``asyncio.Queue`` so
turns can be issued from outside the ``events()`` coroutine.

Mapping (Amp → AgentEvent):
  system/init                                  → (ignored; session metadata)
  user / TextContent (echo of our send_input)  → StatusChange("thinking")
  user / ToolResultContent                     → ToolResult
  assistant / TextContent                      → MessageComplete
  assistant / ToolUseContent                   → ToolCall
  result / success                             → StatusChange("idle")
  result / error                               → Error + StatusChange("idle")

Permissions (Bash gating via the delegate-bridge mechanism):
  Amp's permissions API has no async callback the way Claude's does —
  the only mid-turn-decision primitive is ``delegate``, which substitutes
  a custom command for the tool's native execution. We exploit that to
  gate ``Bash`` through Atelier's permission UI:

    - On ``start`` we open a per-agent Unix domain socket in a 0700
      tmpdir and pass its path to the CLI via the env var
      ``ATELIER_PERMISSION_SOCKET``.
    - We register one Amp permission rule:
      ``("Bash", "delegate", to=f"{python} {bridge_path}")``. Every other
      tool stays on ``allow`` (auto-approve).
    - When the agent uses Bash, the CLI invokes our shim
      (``amp_permission_bridge.py``) with the same argv it would have
      passed to ``bash``. The shim opens the socket, sends ``{tool, argv}``,
      blocks on the response, then ``execvp``s into bash on allow / exits
      with stderr on deny.
    - On the adapter side, the socket listener is a pump task pushing
      ``PermissionRequest`` events into ``_outgoing``; ``events()`` drains
      that queue (same shape as Claude's adapter, just with a different
      producer). User decisions arrive via ``resolve_permission`` and
      complete the open future, which writes the response back to the
      bridge.

  Limitations:
    - **Only Bash is gated.** Edit/Write/Read/Grep/Glob/etc. are still
      auto-approved because Amp implements them internally — delegating
      them means reimplementing their semantics, with all the drift risk.
      The Bash gate covers ``git commit/push``, ``gh pr create``, file
      deletes, sudo — the real footguns.
    - **No "always allow this exact command".** Allow-always is per-tool
      (so "Bash" → all bash invocations); for fine-grained patterns the
      user clicks Allow per-call.
    - **Bridge is fail-closed.** If the socket vanishes or the response
      is malformed, the bridge exits non-zero with stderr — Amp surfaces
      that as a tool failure.

Auth: the underlying SDK relies on the local ``amp`` CLI's stored
credentials (``amp login``) or ``AMP_API_KEY`` from the environment.
Atelier doesn't inject credentials — Sprint 4's ConnectionStore
follow-up will route an ``amp`` connection's token through
``AmpOptions.env``.
"""

# ruff: noqa: E402

import asyncio
import json
import logging
import os
import re
import shlex
import shutil
import stat
import sys
import tempfile
import uuid
from collections.abc import AsyncIterator, Callable, Iterable
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from amp_sdk import (
    AmpOptions,
    AssistantMessage,
    ErrorResultMessage,
    Permission,
    ResultMessage,
    StreamMessage,
    SystemMessage,
    TextContent,
    ToolResultContent,
    ToolUseContent,
    UserInputMessage,
    UserMessage,
    create_user_message,
    execute,
)
from amp_sdk.types import MCPConfig

# Bump amp_sdk's stdout line-buffer limit at module load — the upstream
# default (64 KiB) is too small for tool-result lines (e.g. ``rg -l``
# against a large tree). See ``_amp_sdk_patch`` for the gory details
# and the upstream-fix reminder.
from src.infrastructure.agents import _amp_sdk_patch as _amp_sdk_patch

_amp_sdk_patch.install()

from src.domain.agents import (
    AMP_DEFAULT_AUTO_ALLOWED_TOOLS,
    AgentAdapter,
    AgentEvent,
    AgentStartContext,
    AmpAgentConfig,
    AmpPermissionMode,
    ArtifactMarker,
    Error,
    HandoffOffered,
    MessageComplete,
    PermissionDecision,
    PermissionDecisionValue,
    PermissionRequest,
    SessionEstablished,
    StatusChange,
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
_BRIDGE_PATH = str(Path(__file__).with_name("amp_permission_bridge.py"))

# Amp's CLI auto-hands-off the conversation to a new thread when the
# current one approaches its context limit; the existing SDK stream
# typically ends with an assistant message of the shape:
#   "Handoff created — work continues in T-019e2766-01b7-70ce-90d8-be2b8d9cb40f."
# The new thread is seeded with a plan but the SDK process driving the
# old thread does NOT swap over — left alone the agent appears stuck on
# the closed thread. We pattern-match the assistant text and emit a
# HandoffOffered event so the UI can offer a one-click switch that
# rebuilds the adapter with ``continue_thread=<new_id>``.
#
# Anchored on the literal "Handoff created" phrase + a UUID-shaped Amp
# thread id (T-{8}-{4}-{4}-{4}-{12} hex) so a regular conversation that
# happens to quote a T-id doesn't trigger.
_HANDOFF_PATTERN = re.compile(
    r"Handoff created[\s\S]{0,200}?"
    r"(T-[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}"
    r"-[0-9a-fA-F]{4}-[0-9a-fA-F]{12})",
)


def _detect_handoff(text: str) -> str | None:
    match = _HANDOFF_PATTERN.search(text)
    return match.group(1) if match else None


# The Amp ``handoff`` tool returns a JSON payload like
# ``{"newThreadID": "T-...", "message": "Created handoff thread T-..."}``.
# That's a stronger signal than the assistant text fallback above: the
# model sometimes paraphrases ("Handed off to a new thread…") and the
# regex misses, but the tool result is structured so we can't be wrong
# about what happened.
_HANDOFF_THREAD_ID_PATTERN = re.compile(
    r"T-[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}"
    r"-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}"
)


def _detect_handoff_from_tool_result(content: Any) -> str | None:
    """Extract a new thread id from the Amp ``handoff`` tool result.

    ``content`` may be a JSON-encoded string, a dict, or a list of
    content blocks (Amp's SDK shape varies by content type). We try the
    structured paths first; anything that yields a ``newThreadID`` of
    the canonical T-UUID shape is accepted.
    """

    def _from_dict(d: dict[str, Any]) -> str | None:
        candidate = d.get("newThreadID")
        if isinstance(candidate, str) and _HANDOFF_THREAD_ID_PATTERN.fullmatch(
            candidate
        ):
            return candidate
        return None

    if isinstance(content, dict):
        return _from_dict(content)

    if isinstance(content, list):
        # Content blocks: scan each for an embedded JSON payload.
        for block in content:
            if isinstance(block, dict):
                hit = _from_dict(block)
                if hit:
                    return hit
                text = block.get("text")
                if isinstance(text, str):
                    hit = _detect_handoff_from_tool_result(text)
                    if hit:
                        return hit
        return None

    if isinstance(content, str):
        try:
            parsed = json.loads(content)
        except (json.JSONDecodeError, ValueError):
            return None
        if isinstance(parsed, dict):
            return _from_dict(parsed)
    return None

# DI seam: the executor matches ``amp_sdk.execute``'s signature so tests
# can supply a fake that yields scripted StreamMessages without spawning
# the real CLI subprocess.
ExecuteFn = Callable[
    [AsyncIterator[UserInputMessage], AmpOptions],
    AsyncIterator[StreamMessage],
]


class AmpAdapter:
    """Adapter that streams an Amp CLI session as AgentEvents."""

    def __init__(
        self,
        config: AmpAgentConfig,
        *,
        executor: ExecuteFn = execute,
    ) -> None:
        self._config = config
        self._executor = executor
        self._user_inputs: asyncio.Queue[str | object] = asyncio.Queue()
        # Pump-pattern buffer: SDK events AND permission events both land
        # here; ``events()`` is a pure drain. Lets the socket-listener
        # task emit ``PermissionRequest`` while the SDK pump is mid-turn.
        self._outgoing: asyncio.Queue[AgentEvent | object] = asyncio.Queue()
        self._started = False
        self._closed = False
        # Track the Amp thread: the value passed to ``continue_thread``
        # (if any) and the value last seen on an incoming message.
        self._resume_thread_id: str | None = None
        self._reported_session_id: str | None = None
        # Permission state — same model as the Claude adapter. ``_pending``
        # holds open futures keyed by request_id; ``_allow_always`` is the
        # session-only set of tool names the user has chosen to skip the
        # prompt for (cleared on close).
        self._pending: dict[str, asyncio.Future[PermissionDecisionValue]] = {}
        # request_ids whose ``PermissionDecision`` event has already been
        # enqueued for publish — guards against duplicate decisions when
        # ``close()`` proactively decides on behalf of in-flight prompts.
        self._decided: set[str] = set()
        self._allow_always: set[str] = set()
        # Per-agent Unix socket the bridge connects back through. Created
        # in a 0700 tmpdir so the path itself is the secret.
        self._socket_dir: str | None = None
        self._socket_path: str | None = None
        # Tiny shim shell script written into ``_socket_dir`` whose only
        # job is to ``exec`` the Python interpreter + bridge module with
        # whatever argv the CLI hands it. Amp's ``Permission(to=...)``
        # is treated as a single executable path by Node's
        # ``child_process.spawn`` — passing ``"python" "bridge.py"``
        # as a quoted string trips ENOENT because the literal quotes
        # land in the binary lookup. The shim sidesteps that.
        self._bridge_wrapper_path: str | None = None
        self._server: asyncio.base_events.Server | None = None
        self._pump_task: asyncio.Task[None] | None = None

    async def start(self, context: AgentStartContext) -> None:
        # ``context`` carries ``session_id`` so a previously-assigned Amp
        # thread can be resumed. The CLI subprocess itself spawns lazily
        # on the first stdin write inside ``events()``.
        if self._started:
            raise RuntimeError("start() called twice")
        self._resume_thread_id = context.session_id
        # The permission socket is only needed when Bash is gated through
        # the bridge (DEFAULT and CUSTOM modes). ALLOW_ALL passes
        # ``--dangerously-allow-all`` and never invokes our shim.
        if (
            self._config.permission_mode is not AmpPermissionMode.ALLOW_ALL
            and not self._config.summary_only
        ):
            # Stand it up before any CLI invocation so the agent's first
            # Bash can't outrace our bind. ``mkdtemp`` mode is 0700.
            self._socket_dir = tempfile.mkdtemp(prefix="atelier-amp-")
            self._socket_path = os.path.join(self._socket_dir, "permission.sock")
            self._server = await asyncio.start_unix_server(
                self._handle_bridge_connection, path=self._socket_path
            )
            # Materialise the shim that Amp's CLI will exec for every
            # gated Bash call. Lives in the per-agent socket dir so it
            # gets cleaned up automatically on close().
            self._bridge_wrapper_path = os.path.join(self._socket_dir, "bridge.sh")
            with open(self._bridge_wrapper_path, "w", encoding="utf-8") as f:
                f.write(
                    "#!/bin/sh\n"
                    f"exec {shlex.quote(sys.executable)} "
                    f'{shlex.quote(_BRIDGE_PATH)} "$@"\n'
                )
            os.chmod(self._bridge_wrapper_path, stat.S_IRWXU)
        self._started = True

    async def send_input(self, text: str) -> None:
        await self._user_inputs.put(text)

    async def stop_turn(self) -> None:
        # No-op for v1. ``amp_sdk.execute`` exposes no control-protocol
        # interrupt — the only cancel is ``task.cancel()``, which kills
        # the CLI subprocess and ends ``events()``, taking the whole
        # adapter down. A real interrupt for Amp needs ``events()`` to
        # spawn a fresh executor per turn so the cancel scope is per-turn
        # rather than per-adapter; tracked as a follow-up. The stop frame
        # still reaches the transcript via the supervisor's ``user_stop``
        # line, so the user sees their intent recorded.
        #
        # We DO deny any in-flight permission prompts here so a stop while
        # the user is still deciding cleanly unblocks the bridge — same
        # safety detail as the Claude adapter.
        for fut in list(self._pending.values()):
            if not fut.done():
                fut.set_result("deny")

    async def resolve_permission(
        self, request_id: str, decision: PermissionDecisionValue
    ) -> None:
        fut = self._pending.get(request_id)
        if fut is None or fut.done():
            return
        fut.set_result(decision)

    async def _prompt_iter(self) -> AsyncIterator[UserInputMessage]:
        while True:
            text = await self._user_inputs.get()
            if text is _SHUTDOWN:
                return
            assert isinstance(text, str)
            yield create_user_message(text)

    async def events(self) -> AsyncIterator[AgentEvent]:
        if not self._started:
            raise RuntimeError("events() called before start()")
        # Spawn the SDK pump (forwards Amp StreamMessages → AgentEvents
        # into ``_outgoing``). The socket listener is already running
        # from ``start()``. ``events()`` itself is a pure drain so
        # ``PermissionRequest`` events from the listener can interleave
        # naturally with SDK output.
        self._pump_task = asyncio.create_task(self._run_sdk_pump(), name="amp-sdk-pump")
        try:
            while True:
                item = await self._outgoing.get()
                if item is _SHUTDOWN:
                    return
                yield item  # type: ignore[misc]
        finally:
            if self._pump_task is not None and not self._pump_task.done():
                self._pump_task.cancel()
                try:
                    await self._pump_task
                except asyncio.CancelledError:
                    pass

    async def _run_sdk_pump(self) -> None:
        """Drain the Amp executor into ``_outgoing``.

        Terminates ``events()`` by pushing ``_SHUTDOWN`` once the executor
        finishes (success or error). Without that, ``events()`` would
        hang on the queue after the SDK has nothing more to say.

        End-of-turn synthesis: Amp's CLI in GPT-backed modes (deep/large
        → openai-responses) does NOT emit a trailing ``result`` JSON
        line at end of turn. Without it, ``TurnMetrics`` + the
        ``status_change("idle")`` pill never fire and the FE shows
        "thinking" forever. We carry a small per-turn accumulator that:

        - sums per-AssistantMessage usage as the turn streams,
        - tracks whether a tool result is still expected (so we don't
          declare end-of-turn while the CLI is between sub-streams),
        - on quiescence (the SDK iterator goes silent past a state-
          dependent timeout), synthesises a TurnMetrics + idle on the
          way back to the prompt iterator.

        Anthropic-backed modes keep their existing fast path: when a
        real ``ResultMessage`` arrives, ``_convert`` yields TurnMetrics
        + idle and we reset the accumulator with nothing to synth.
        """
        opts = self._build_amp_options()
        options = AmpOptions.model_validate(opts)
        model = self._config.mode.value
        # Track the prompt size of the latest sub-call for ctx% — see
        # ``TurnMetrics`` doc + the same comment in claude_code_adapter.
        last_prompt_tokens = 0
        turn = _TurnAccumulator()
        iterator = self._executor(self._prompt_iter(), options).__aiter__()
        next_task: asyncio.Task[StreamMessage] | None = None
        try:
            while True:
                if next_task is None:
                    next_task = asyncio.create_task(_anext(iterator))
                timeout = turn.next_quiescence_timeout()
                try:
                    if timeout is None:
                        await next_task
                    else:
                        done, _pending = await asyncio.wait(
                            {next_task}, timeout=timeout
                        )
                        if not done:
                            # Iterator went quiet past the state-dependent
                            # timeout → turn is over. Synth-emit and reset;
                            # do NOT cancel ``next_task`` so the SDK stream
                            # keeps draining when the user sends the next
                            # input.
                            for ev in turn.synth_close(
                                now=datetime.now(UTC),
                                model=model,
                                last_prompt_tokens=last_prompt_tokens,
                            ):
                                await self._outgoing.put(ev)
                            turn.reset()
                            last_prompt_tokens = 0
                            continue
                    msg = next_task.result()
                except StopAsyncIteration:
                    break
                next_task = None

                sid = getattr(msg, "session_id", None)
                if sid is not None and sid != self._reported_session_id:
                    self._reported_session_id = sid
                    await self._outgoing.put(
                        SessionEstablished(ts=datetime.now(UTC), session_id=sid)
                    )
                turn.observe(msg)
                per_call = _assistant_prompt_tokens(msg)
                if per_call is not None:
                    last_prompt_tokens = per_call
                for ev in _convert(
                    msg, model=model, last_prompt_tokens=last_prompt_tokens
                ):
                    await self._outgoing.put(ev)
                if isinstance(msg, ResultMessage | ErrorResultMessage):
                    # Anthropic-mode result path: ``_convert`` already
                    # emitted real TurnMetrics + idle. Drop accumulator
                    # state so a stale synth close can't pile on.
                    last_prompt_tokens = 0
                    turn.reset()
                    if isinstance(msg, ErrorResultMessage):
                        # Amp error results often leave the stream-json
                        # input handler unusable. End this adapter so the
                        # supervisor evicts it and the reconnect path
                        # rebuilds a fresh process before the next send.
                        break
        except Exception as e:
            await self._outgoing.put(Error(ts=datetime.now(UTC), message=str(e)))
            await self._outgoing.put(
                StatusChange(ts=datetime.now(UTC), status="idle")
            )
        finally:
            if next_task is not None and not next_task.done():
                next_task.cancel()
            await self._outgoing.put(_SHUTDOWN)

    def _build_amp_options(self) -> dict[str, object]:
        """AmpOptions kwargs, switched per ``permission_mode``.

        - ``ALLOW_ALL``: ``--dangerously-allow-all``, no permission rules,
          no socket. Old pre-permission behaviour. Maximum risk, zero
          friction.
        - ``DEFAULT`` / ``CUSTOM``: ``dangerously_allow_all=False`` plus an
          explicit rule list. Bash → delegate to bridge; allowlisted tools
          → allow; ``*`` → allow as a catch-all (matches Amp's post-Neo
          default for un-matched tools — set explicitly so the contract
          is visible). The UI surfaces this trade-off in the Permissions
          section: only Bash is user-prompted; all other tools, including
          new/MCP ones, auto-run.
        """
        opts: dict[str, object] = {
            "cwd": str(self._config.common.workdir),
            "mode": self._config.mode.value,
            # Atelier's artifact-recording tools are exposed via a
            # subprocess MCP server Amp spawns alongside its CLI. The
            # tool body is a no-op acknowledgement; the side-effect is
            # driven by ArtifactMarker emission in ``_convert``.
            "mcp_config": _build_atelier_mcp_config(),
        }
        if self._resume_thread_id is not None:
            opts["continue_thread"] = self._resume_thread_id

        if self._config.summary_only:
            return {
                "cwd": str(self._config.common.workdir),
                "mode": self._config.mode.value,
                "dangerously_allow_all": False,
                "permissions": [
                    Permission(
                        tool="*",
                        matches=None,
                        action="reject",
                        context=None,
                        to=None,
                    )
                ],
            }

        if self._config.permission_mode is AmpPermissionMode.ALLOW_ALL:
            opts["dangerously_allow_all"] = True
            return opts

        if self._config.permission_mode is AmpPermissionMode.CUSTOM:
            allowed = tuple(self._config.custom_allowed_tools)
        else:
            allowed = AMP_DEFAULT_AUTO_ALLOWED_TOOLS
        opts["dangerously_allow_all"] = False
        # ``self._bridge_wrapper_path`` is set in ``start()`` whenever
        # we're not in ALLOW_ALL mode (the only mode that skips the
        # bridge entirely), so this can't be None here.
        assert self._bridge_wrapper_path is not None
        opts["permissions"] = _build_permissions(
            allowed, self._bridge_wrapper_path
        )
        opts["env"] = {"ATELIER_PERMISSION_SOCKET": self._socket_path or ""}
        return opts

    async def _handle_bridge_connection(
        self,
        reader: asyncio.StreamReader,
        writer: asyncio.StreamWriter,
    ) -> None:
        """One bridge invocation = one connection. Handshake is one
        line in, one line out, then close."""
        try:
            raw = await reader.readline()
        except OSError:
            writer.close()
            return
        try:
            request = json.loads(raw.decode("utf-8").strip())
        except (json.JSONDecodeError, UnicodeDecodeError):
            _log.warning("malformed bridge request: %r", raw)
            await _close_writer(writer)
            return

        tool_name = request.get("tool") if isinstance(request, dict) else None
        argv = request.get("argv") if isinstance(request, dict) else None
        if not isinstance(tool_name, str) or not isinstance(argv, list):
            await _close_writer(writer)
            return

        decision = await self._decide_permission(tool_name, argv)
        try:
            writer.write((json.dumps({"decision": decision}) + "\n").encode("utf-8"))
            await writer.drain()
        except OSError:
            pass  # bridge may have already exited
        await _close_writer(writer)

    async def _decide_permission(
        self, tool_name: str, argv: list[Any]
    ) -> PermissionDecisionValue:
        """Emit a PermissionRequest and await the user's decision.

        Mirrors the Claude adapter's ``_can_use_tool``. Auto-allow if the
        user previously said "always allow" for this tool name.
        """
        canon_name, canon_input = canonicalize_tool(
            tool_name, _structured_input_from_argv(argv)
        )
        if canon_name in self._allow_always:
            return "allow"
        request_id = uuid.uuid4().hex
        loop = asyncio.get_running_loop()
        fut: asyncio.Future[PermissionDecisionValue] = loop.create_future()
        self._pending[request_id] = fut
        # The bridge sends raw argv (e.g. ``["-c","ls -la"]``); we
        # structure it into ``{command: ...}`` (already canonical for
        # Bash) before emitting the prompt event.
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
                decision = "deny"
        finally:
            self._pending.pop(request_id, None)
        # ``close()`` may have already published a synthetic deny for this
        # request_id before tearing us down — skip the duplicate so the
        # transcript stays clean.
        if request_id not in self._decided:
            self._decided.add(request_id)
            # Shield the put so a late cancellation (e.g. server.close()
            # racing with our resume) can't drop the decision and leave
            # an orphan ``permission_request`` in the transcript — the
            # exact bug that produced stuck "Allow Bash?" prompts.
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
        # Free any in-flight bridge connections so they exit instead of
        # hanging on socket read while we tear down. We also publish a
        # synthetic ``PermissionDecision(deny)`` for each pending request
        # BEFORE resolving the future — that way the transcript always
        # has a matching decision for every request, even if the
        # ``_decide_permission`` task is cancelled before it can publish
        # itself. Without this, the frontend rebuilds ``pendingPermissions``
        # from the transcript on reconnect and the prompt stays "stuck".
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
        # Closing the prompt iterator (via _SHUTDOWN) ends stdin, which
        # makes the CLI exit, which lets execute() return.
        await self._user_inputs.put(_SHUTDOWN)
        if self._server is not None:
            self._server.close()
            try:
                await self._server.wait_closed()
            except Exception:
                pass
            self._server = None
        if self._socket_dir is not None and os.path.isdir(self._socket_dir):
            shutil.rmtree(self._socket_dir, ignore_errors=True)
            self._socket_dir = None
        # Unblock events() if it's still draining.
        await self._outgoing.put(_SHUTDOWN)


async def _close_writer(writer: asyncio.StreamWriter) -> None:
    try:
        writer.close()
        await writer.wait_closed()
    except OSError:
        pass


def _build_atelier_mcp_config() -> MCPConfig:
    """The MCP config Amp uses to spawn Atelier's artifact-tool server.

    Spawned via the same Python interpreter as the running backend so the
    server module resolves through Atelier's editable install. Each agent
    gets its own short-lived subprocess; no shared state to worry about.
    """
    return MCPConfig.model_validate(
        {
            "servers": {
                MCP_SERVER_NAME: {
                    "command": sys.executable,
                    "args": [
                        "-m",
                        "src.infrastructure.agents.atelier_mcp_server",
                    ],
                }
            }
        }
    )


def _build_permissions(
    allowed_tools: tuple[str, ...], bridge_cmd: str
) -> list[Permission]:
    """Permission rules for the Amp CLI.

    Bash → delegate to our bridge (gated by Atelier's prompt UI). Each
    tool in ``allowed_tools`` → allow. A trailing ``*`` → allow rule
    mirrors Amp's post-Neo default for un-matched tools (anything new /
    MCP-provided / not on our allowlist auto-runs); we set it
    explicitly so behaviour stays stable if the CLI's implicit default
    ever moves again. Stream-json mode has no path to surface ``ask``
    to our UI — only ``delegate`` does — so we don't try to gate
    anything beyond Bash here.

    ``bridge_cmd`` is the absolute path to a per-agent shim script that
    exec's the Python interpreter + bridge module. Built by the adapter
    in ``start()`` and threaded through here so Amp's
    ``Permission(to=...)`` sees a single executable path — embedding a
    shell-style ``"python" "bridge.py"`` in ``to`` breaks Node's
    ``child_process.spawn`` (literal quotes land in the binary lookup
    → ENOENT).

    ``"Bash"`` in ``allowed_tools`` is silently dropped: the user must
    not be able to disable shell gating from the UI. (Defeating that
    would defeat the entire reason this knob exists.)
    """
    rules: list[Permission] = [
        Permission(
            tool="Bash",
            matches=None,
            action="delegate",
            context=None,
            to=bridge_cmd,
        ),
    ]
    for tool in allowed_tools:
        if tool == "Bash":
            continue
        rules.append(
            Permission(
                tool=tool,
                matches=None,
                action="allow",
                context=None,
                to=None,
            )
        )
    rules.append(
        Permission(
            tool="*",
            matches=None,
            action="allow",
            context=None,
            to=None,
        )
    )
    return rules


def _structured_input_from_argv(argv: list[Any]) -> dict[str, Any]:
    """Lift ``["-c","<command>"]`` into ``{"command": "<command>"}``.

    Falls back to ``{"argv": [...]}`` for any other shape so the prompt
    panel never loses information. This keeps the existing
    ``summariseToolInput`` Bash branch on the frontend working without
    a special-case for Amp.
    """
    if len(argv) == 2 and argv[0] == "-c" and isinstance(argv[1], str):
        return {"command": argv[1]}
    return {"argv": [str(a) for a in argv]}


def _convert(
    msg: StreamMessage,
    *,
    model: str | None = None,
    last_prompt_tokens: int = 0,
) -> Iterable[AgentEvent]:
    """Map an Amp StreamMessage onto our AgentEvent union.

    ``model`` lets the adapter stamp per-turn metrics with the
    user-selected mode (Amp's "primary selector" — smart/rush/deep/large).

    ``last_prompt_tokens`` is the prompt size of the last AssistantMessage
    in the turn — which equals the total context currently in the
    model's window, since every sub-call's prompt replays the full
    conversation history. See ``TurnMetrics`` for the full rationale.
    Passed in by the pump so emitted ``TurnMetrics`` carry the running
    total alongside the cumulative-for-cost counts from
    ``ResultMessage.usage``.
    """
    now = datetime.now(UTC)
    if isinstance(msg, SystemMessage):
        return  # session-init metadata; nothing for the supervisor.
    if isinstance(msg, UserMessage):
        for user_block in msg.message.content:
            if isinstance(user_block, TextContent):
                # Amp echoes our own input back at the start of each turn.
                # Use that as the canonical "thinking starts now" marker.
                yield StatusChange(ts=now, status="thinking")
            elif isinstance(user_block, ToolResultContent):
                raw_content = user_block.content
                yield ToolResult(
                    ts=now,
                    tool_id=user_block.tool_use_id,
                    content=_sanitize_tool_result_content(raw_content),
                    is_error=user_block.is_error,
                )
                # Structured handoff signal: the ``handoff`` tool's
                # JSON result carries ``newThreadID`` directly. More
                # reliable than the assistant-text regex fallback,
                # which misses when the model paraphrases.
                handoff_id = _detect_handoff_from_tool_result(raw_content)
                if handoff_id is not None:
                    yield HandoffOffered(ts=now, new_thread_id=handoff_id)
        return
    if isinstance(msg, AssistantMessage):
        for asst_block in msg.message.content:
            if isinstance(asst_block, TextContent):
                yield MessageComplete(ts=now, text=asst_block.text)
                # Belt-and-suspenders fallback: GPT-backed modes drop
                # some MCP tool calls during normalization, so the
                # model may resort to emitting an ``atelier_artifact``
                # JSON line in chat (per the system prompt). Scan
                # every text block for one — duplicate emissions are
                # rare in practice (the model picks one path or the
                # other), and the tracker layer de-dupes per work via
                # its own validation.
                for payload in scan_text_for_artifact_markers(asst_block.text):
                    yield ArtifactMarker(ts=now, payload=payload)
                handoff_id = _detect_handoff(asst_block.text)
                if handoff_id is not None:
                    yield HandoffOffered(ts=now, new_thread_id=handoff_id)
            elif isinstance(asst_block, ToolUseContent):
                # Atelier artifact tools produce a marker on the side; the
                # ToolCall still flows so the chat shows the agent's call.
                marker_payload = marker_payload_for_tool(
                    asst_block.name, dict(asst_block.input)
                )
                if marker_payload is not None:
                    yield ArtifactMarker(ts=now, payload=marker_payload)
                canon_name, canon_args = canonicalize_tool(
                    asst_block.name, dict(asst_block.input)
                )
                yield ToolCall(
                    ts=now,
                    tool_id=asst_block.id,
                    name=canon_name,
                    arguments=canon_args,
                )
        return
    if isinstance(msg, ErrorResultMessage):
        yield Error(ts=now, message=msg.error or "(unknown error)")
        yield from _metrics_from_result(msg, now, model, last_prompt_tokens)
        yield StatusChange(ts=now, status="idle")
        return
    if isinstance(msg, ResultMessage):
        yield from _metrics_from_result(msg, now, model, last_prompt_tokens)
        yield StatusChange(ts=now, status="idle")
        return


def _metrics_from_result(
    msg: ResultMessage | ErrorResultMessage,
    now: datetime,
    model: str | None,
    last_prompt_tokens: int,
) -> Iterable[TurnMetrics]:
    usage = msg.usage
    yield TurnMetrics(
        ts=now,
        duration_ms=msg.duration_ms,
        input_tokens=usage.input_tokens if usage else 0,
        output_tokens=usage.output_tokens if usage else 0,
        cache_read_input_tokens=usage.cache_read_input_tokens if usage else 0,
        cache_creation_input_tokens=usage.cache_creation_input_tokens if usage else 0,
        last_prompt_tokens=last_prompt_tokens,
        model=model,
    )


def _sanitize_tool_result_content(content: str) -> str:
    """Drop Amp guidance file bodies from persisted tool results.

    Amp can attach ``discoveredGuidanceFiles`` to otherwise small tool
    results. Those entries may include full source files referenced by
    AGENTS.md; persisting them verbatim makes Atelier transcripts,
    compaction prompts, and replay paths balloon invisibly. Keep metadata
    and byte counts so the debug trail still explains what happened.
    """
    try:
        payload = json.loads(content)
    except (TypeError, ValueError):
        return content
    if not isinstance(payload, dict):
        return content

    guidance = payload.get("discoveredGuidanceFiles")
    if not isinstance(guidance, list):
        return content

    changed = False
    sanitized_guidance: list[object] = []
    for entry in guidance:
        if not isinstance(entry, dict):
            sanitized_guidance.append(entry)
            continue
        sanitized = dict(entry)
        file_content = sanitized.pop("content", None)
        if isinstance(file_content, str):
            sanitized["content_chars"] = len(file_content)
            sanitized["content_omitted"] = True
            changed = True
        sanitized_guidance.append(sanitized)

    if not changed:
        return content
    sanitized_payload = dict(payload)
    sanitized_payload["discoveredGuidanceFiles"] = sanitized_guidance
    return json.dumps(sanitized_payload, separators=(",", ":"))


def _assistant_prompt_tokens(msg: StreamMessage) -> int | None:
    """Same idea as the Claude helper: pull the per-call prompt size out
    of an Amp ``AssistantMessage``. The Amp SDK wraps the Anthropic
    Message under ``msg.message`` and exposes ``usage`` there with the
    standard input / cache_read / cache_creation breakdown."""
    if not isinstance(msg, AssistantMessage):
        return None
    details = getattr(msg, "message", None)
    usage = getattr(details, "usage", None) if details is not None else None
    if usage is None:
        return None
    return (
        int(getattr(usage, "input_tokens", 0) or 0)
        + int(getattr(usage, "cache_read_input_tokens", 0) or 0)
        + int(getattr(usage, "cache_creation_input_tokens", 0) or 0)
    )


# Quiescence timeouts for the synthetic end-of-turn detector. Picked to
# cover the typical Amp deep-mode rhythm: most auto-allowed tools return
# within ~3s, permission prompts emit their own PermissionRequest /
# PermissionDecision events (which reset the timer), and the inter-
# sub-stream gap when the CLI is composing the next Responses.create
# call is sub-second. _QUIESCENCE_TOOL_PENDING gives ourselves a wide
# berth for slow shell commands; tune down if it starts feeling laggy.
_QUIESCENCE_END_SIGNALED = 1.5
_QUIESCENCE_DEFAULT = 4.0
_QUIESCENCE_TOOL_PENDING = 60.0


class _TurnAccumulator:
    """Per-turn token tally + tool-pending state for the synth-close path.

    Lives only for the GPT-backed-mode case where Amp's CLI omits the
    trailing ``result`` line. Anthropic-mode turns end via ResultMessage
    and ``reset()`` here without ever emitting a synth close.
    """

    def __init__(self) -> None:
        self.input_tokens: int = 0
        self.output_tokens: int = 0
        self.cache_read_input_tokens: int = 0
        self.cache_creation_input_tokens: int = 0
        self._start: datetime | None = None
        self._expecting_tool_result: bool = False
        self._end_signaled: bool = False

    def reset(self) -> None:
        self.input_tokens = 0
        self.output_tokens = 0
        self.cache_read_input_tokens = 0
        self.cache_creation_input_tokens = 0
        self._start = None
        self._expecting_tool_result = False
        self._end_signaled = False

    @property
    def has_activity(self) -> bool:
        return (
            self.input_tokens > 0
            or self.output_tokens > 0
            or self.cache_read_input_tokens > 0
            or self.cache_creation_input_tokens > 0
        )

    def next_quiescence_timeout(self) -> float | None:
        """Seconds to wait for the next SDK message before declaring
        end-of-turn. ``None`` → block indefinitely (no in-flight turn)."""
        if not self.has_activity:
            return None
        if self._expecting_tool_result:
            return _QUIESCENCE_TOOL_PENDING
        if self._end_signaled:
            return _QUIESCENCE_END_SIGNALED
        return _QUIESCENCE_DEFAULT

    def observe(self, msg: StreamMessage) -> None:
        """Fold a SDK message into the turn state.

        AssistantMessage accumulates usage and flips ``_expecting_tool_
        result`` based on whether the message contains a ToolUseContent.
        UserMessage with ToolResultContent clears the wait flag; with
        TextContent we mark the previous turn done so the synth close
        fires promptly when the user sends a follow-up.
        """
        if isinstance(msg, AssistantMessage):
            details = getattr(msg, "message", None)
            usage = getattr(details, "usage", None) if details is not None else None
            if usage is not None:
                self.input_tokens += int(getattr(usage, "input_tokens", 0) or 0)
                self.output_tokens += int(getattr(usage, "output_tokens", 0) or 0)
                self.cache_read_input_tokens += int(
                    getattr(usage, "cache_read_input_tokens", 0) or 0
                )
                self.cache_creation_input_tokens += int(
                    getattr(usage, "cache_creation_input_tokens", 0) or 0
                )
            content = list(getattr(details, "content", []) or []) if details else []
            has_tool_use = any(isinstance(b, ToolUseContent) for b in content)
            stop_reason = getattr(details, "stop_reason", None) if details else None
            self._expecting_tool_result = has_tool_use
            # ``stop_reason="end_turn"`` is the model's explicit "I'm
            # done" signal. ``None`` is common in GPT-mode where Amp
            # doesn't synthesise the stop_reason — treat absence as a
            # weaker but still useful "no more tools planned" if the
            # message has no ToolUseContent.
            self._end_signaled = (not has_tool_use) and stop_reason in (
                None,
                "end_turn",
            )
            if self._start is None and self.has_activity:
                self._start = datetime.now(UTC)
            return
        if isinstance(msg, UserMessage):
            content = msg.message.content
            if any(isinstance(b, ToolResultContent) for b in content):
                self._expecting_tool_result = False

    def synth_close(
        self,
        *,
        now: datetime,
        model: str | None,
        last_prompt_tokens: int,
    ) -> Iterable[AgentEvent]:
        """Yield the synthesised end-of-turn pair: TurnMetrics + idle.

        Caller invokes this when ``next_quiescence_timeout`` elapses
        without a new SDK message; we assume the turn is over and the
        CLI is now blocked on the prompt iterator for the next user
        input.
        """
        duration_ms = (
            int((now - self._start).total_seconds() * 1000)
            if self._start is not None
            else 0
        )
        yield TurnMetrics(
            ts=now,
            duration_ms=duration_ms,
            input_tokens=self.input_tokens,
            output_tokens=self.output_tokens,
            cache_read_input_tokens=self.cache_read_input_tokens,
            cache_creation_input_tokens=self.cache_creation_input_tokens,
            last_prompt_tokens=last_prompt_tokens,
            model=model,
        )
        yield StatusChange(ts=now, status="idle")


async def _anext(iterator: AsyncIterator[StreamMessage]) -> StreamMessage:
    """Wrap ``__anext__`` as a regular coroutine so ``create_task`` can
    schedule it. Required because ``asyncio.wait`` needs Tasks, not
    bare awaitable iterator protocols."""
    return await iterator.__anext__()


@build_adapter.register
def _build_amp_adapter(config: AmpAgentConfig, settings: Settings) -> AgentAdapter:
    return AmpAdapter(config)


__all__ = ["AmpAdapter", "ExecuteFn"]
