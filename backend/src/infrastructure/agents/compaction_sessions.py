"""Provider session operations used by domain compaction commands."""

from __future__ import annotations

import asyncio
import dataclasses
from contextlib import suppress

from src.domain.agents import AgentAdapter, AgentEvent, AgentStartContext
from src.domain.agents.compactions import (
    BreadcrumbResult,
    CompactionSessionStartResult,
)
from src.domain.agents.configs import (
    AgentConfig,
    AmpAgentConfig,
    ClaudeAgentConfig,
    ClaudePermissionMode,
    CodexAgentConfig,
    CodexApprovalMode,
    CodexSandbox,
)
from src.domain.agents.events import (
    Error,
    MessageComplete,
    MessageDelta,
    SessionEstablished,
    StatusChange,
    ToolCall,
)
from src.domain.agents.handoffs import SUMMARY_SYSTEM_PROMPT
from src.infrastructure.agents.factory import build_adapter
from src.settings import Settings

_START_TIMEOUT_SECONDS = 180.0
_BREADCRUMB_TIMEOUT_SECONDS = 60.0
_SUMMARY_TIMEOUT_SECONDS = 180.0
_IDLE_GRACE_SECONDS = 30.0


class AdapterCompactionSessionClient:
    """Use the registered provider adapter to seed and breadcrumb sessions.

    The domain command defines the port; this implementation is the only
    place that knows provider configs become concrete Claude/Amp/Codex
    adapters. It consumes events privately because these setup turns are
    provider-session maintenance, not normal user turns.
    """

    def __init__(self, settings: Settings) -> None:
        self._settings = settings

    async def summarize_transcript(
        self,
        *,
        config: AgentConfig,
        context: AgentStartContext,
        prompt: str,
    ) -> str:
        summary_config = _summary_config(config)
        adapter = build_adapter(summary_config, self._settings)
        summary_context = dataclasses.replace(
            context,
            session_id=None,
            system_prompt=SUMMARY_SYSTEM_PROMPT,
        )
        return await _send_and_collect_text(
            adapter,
            summary_context,
            prompt,
            timeout=_SUMMARY_TIMEOUT_SECONDS,
        )

    async def start_fresh_session(
        self,
        *,
        config: AgentConfig,
        context: AgentStartContext,
        seed_message: str,
    ) -> CompactionSessionStartResult:
        adapter = build_adapter(config, self._settings)
        fresh_context = dataclasses.replace(context, session_id=None)
        session_id = await _send_and_wait(
            adapter,
            fresh_context,
            seed_message,
            require_session=True,
            timeout=_START_TIMEOUT_SECONDS,
        )
        if session_id is None:
            raise RuntimeError("fresh provider session did not report a session id")
        return CompactionSessionStartResult(session_id=session_id)

    async def write_breadcrumb(
        self,
        *,
        config: AgentConfig,
        context: AgentStartContext,
        old_session_id: str,
        breadcrumb: str,
    ) -> BreadcrumbResult:
        adapter = build_adapter(config, self._settings)
        old_context = dataclasses.replace(context, session_id=old_session_id)
        try:
            await _send_and_wait(
                adapter,
                old_context,
                breadcrumb,
                require_session=False,
                timeout=_BREADCRUMB_TIMEOUT_SECONDS,
            )
        except Exception as exc:
            return BreadcrumbResult(written=False, error=repr(exc))
        return BreadcrumbResult(written=True)


def _summary_config(config: AgentConfig) -> AgentConfig:
    common = dataclasses.replace(
        config.common,
        system_prompt=SUMMARY_SYSTEM_PROMPT,
        writable_roots=(),
    )
    if isinstance(config, ClaudeAgentConfig):
        return dataclasses.replace(
            config,
            common=common,
            permission_mode=ClaudePermissionMode.PLAN,
            allowed_tools=(),
            summary_only=True,
        )
    if isinstance(config, AmpAgentConfig):
        return dataclasses.replace(config, common=common, summary_only=True)
    if isinstance(config, CodexAgentConfig):
        return dataclasses.replace(
            config,
            common=common,
            sandbox=CodexSandbox.READ_ONLY,
            approval_mode=CodexApprovalMode.NEVER,
            summary_only=True,
        )
    raise TypeError(f"unsupported agent config: {type(config)!r}")


async def _send_and_collect_text(
    adapter: AgentAdapter,
    context: AgentStartContext,
    text: str,
    *,
    timeout: float,
) -> str:
    idle_seen = asyncio.Event()
    complete_messages: list[str] = []
    delta_parts: list[str] = []
    error_seen: asyncio.Future[BaseException] = (
        asyncio.get_running_loop().create_future()
    )

    async def consume() -> None:
        async for event in adapter.events():
            _notice_summary_event(
                event,
                complete_messages=complete_messages,
                delta_parts=delta_parts,
                idle_seen=idle_seen,
                error_seen=error_seen,
            )
            if idle_seen.is_set():
                return

    task: asyncio.Task[None] | None = None
    try:
        await adapter.start(context)
        task = asyncio.create_task(consume(), name="compaction-session-summary")
        await adapter.send_input(text)
        idle_task = asyncio.create_task(idle_seen.wait())
        error_task = asyncio.create_task(_raise_when_set(error_seen))
        done, pending = await asyncio.wait(
            {idle_task, error_task},
            timeout=timeout,
            return_when=asyncio.FIRST_COMPLETED,
        )
        for pending_task in pending:
            pending_task.cancel()
            with suppress(asyncio.CancelledError):
                await pending_task
        if not done:
            raise TimeoutError("provider summary timed out")
        for done_task in done:
            await done_task
        summary = "\n\n".join(part.strip() for part in complete_messages if part.strip())
        if not summary:
            summary = "".join(delta_parts).strip()
        if not summary:
            raise RuntimeError("provider summary produced no assistant text")
        return summary
    finally:
        if task is not None:
            task.cancel()
            with suppress(asyncio.CancelledError):
                await task
        await adapter.close()


async def _raise_when_set(error_seen: asyncio.Future[BaseException]) -> None:
    raise await error_seen


def _notice_summary_event(
    event: AgentEvent,
    *,
    complete_messages: list[str],
    delta_parts: list[str],
    idle_seen: asyncio.Event,
    error_seen: asyncio.Future[BaseException],
) -> None:
    if isinstance(event, MessageComplete):
        complete_messages.append(event.text)
    elif isinstance(event, MessageDelta):
        delta_parts.append(event.text)
    elif isinstance(event, ToolCall):
        # Summary-only configs reject all provider tools. Some providers
        # still surface the attempted call before continuing from the
        # rejection; do not abort early or we force the weaker fallback
        # summarizer even when the model can still produce the summary.
        return
    elif isinstance(event, Error) and not error_seen.done():
        error_seen.set_result(RuntimeError(event.message))
    elif isinstance(event, StatusChange) and event.status == "idle":
        idle_seen.set()


async def _send_and_wait(
    adapter: AgentAdapter,
    context: AgentStartContext,
    text: str,
    *,
    require_session: bool,
    timeout: float,
) -> str | None:
    session_seen: asyncio.Future[str] = asyncio.get_running_loop().create_future()
    idle_seen = asyncio.Event()

    async def consume() -> None:
        async for event in adapter.events():
            _notice_event(event, session_seen, idle_seen)
            if idle_seen.is_set() and (session_seen.done() or not require_session):
                return

    task: asyncio.Task[None] | None = None
    try:
        await adapter.start(context)
        task = asyncio.create_task(consume(), name="compaction-session-seed")
        await adapter.send_input(text)
        if require_session:
            session_id = await asyncio.wait_for(session_seen, timeout=timeout)
        else:
            session_id = None
        with suppress(asyncio.TimeoutError):
            await asyncio.wait_for(idle_seen.wait(), timeout=_IDLE_GRACE_SECONDS)
        return session_id
    finally:
        if task is not None:
            task.cancel()
            with suppress(asyncio.CancelledError):
                await task
        await adapter.close()


def _notice_event(
    event: AgentEvent, session_seen: asyncio.Future[str], idle_seen: asyncio.Event
) -> None:
    if isinstance(event, SessionEstablished) and not session_seen.done():
        session_seen.set_result(event.session_id)
    elif isinstance(event, StatusChange) and event.status == "idle":
        idle_seen.set()


__all__ = ["AdapterCompactionSessionClient"]
