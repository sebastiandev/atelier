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

Auth: the underlying SDK relies on the local ``amp`` CLI's stored
credentials (``amp login``) or ``AMP_API_KEY`` from the environment.
Atelier doesn't inject credentials — Sprint 4's ConnectionStore
follow-up will route an ``amp`` connection's token through
``AmpOptions.env``.
"""

import asyncio
from collections.abc import AsyncIterator, Callable, Iterable
from datetime import UTC, datetime

from amp_sdk import (
    AmpOptions,
    AssistantMessage,
    ErrorResultMessage,
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

from src.domain.agents import (
    AgentAdapter,
    AgentEvent,
    AgentStartContext,
    AmpAgentConfig,
    Error,
    MessageComplete,
    StatusChange,
    ToolCall,
    ToolResult,
    TurnMetrics,
)
from src.infrastructure.agents.factory import build_adapter
from src.settings import Settings

_SHUTDOWN = object()  # sentinel pushed onto the queue by close()

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
        self._started = False
        self._closed = False

    async def start(self, context: AgentStartContext) -> None:
        # ``context`` is part of the Protocol contract but the adapter is
        # already fully configured via ``AmpAgentConfig`` at construction
        # time, so we ignore it (mirrors the Claude adapter). The SDK
        # spawns the CLI subprocess lazily — first stdin write happens
        # when ``events()`` begins draining, so there's no eager connect.
        if self._started:
            raise RuntimeError("start() called twice")
        self._started = True

    async def send_input(self, text: str) -> None:
        await self._user_inputs.put(text)

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
        # Atelier mediates approvals at the orchestration layer; the CLI
        # would otherwise block on stdin TTY prompts that we can't answer.
        # The Pydantic field uses a camelCase alias, so build via dict to
        # keep mypy happy without forcing the alias name on the call site.
        options = AmpOptions.model_validate(
            {
                "cwd": str(self._config.common.workdir),
                "mode": self._config.mode.value,
                "dangerously_allow_all": True,
            }
        )
        model = self._config.mode.value
        try:
            async for msg in self._executor(self._prompt_iter(), options):
                for ev in _convert(msg, model=model):
                    yield ev
        except Exception as e:
            yield Error(ts=datetime.now(UTC), message=str(e))
            yield StatusChange(ts=datetime.now(UTC), status="idle")

    async def close(self) -> None:
        if self._closed:
            return
        self._closed = True
        # Closing the prompt iterator (via _SHUTDOWN) ends stdin, which
        # makes the CLI exit, which lets execute() return.
        await self._user_inputs.put(_SHUTDOWN)


def _convert(msg: StreamMessage, *, model: str | None = None) -> Iterable[AgentEvent]:
    """Map an Amp StreamMessage onto our AgentEvent union.

    ``model`` lets the adapter stamp per-turn metrics with the
    user-selected mode (Amp's "primary selector" — smart/rush/deep/large).
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
                yield ToolResult(
                    ts=now,
                    tool_id=user_block.tool_use_id,
                    content=user_block.content,
                    is_error=user_block.is_error,
                )
        return
    if isinstance(msg, AssistantMessage):
        for asst_block in msg.message.content:
            if isinstance(asst_block, TextContent):
                yield MessageComplete(ts=now, text=asst_block.text)
            elif isinstance(asst_block, ToolUseContent):
                yield ToolCall(
                    ts=now,
                    tool_id=asst_block.id,
                    name=asst_block.name,
                    arguments=asst_block.input,
                )
        return
    if isinstance(msg, ErrorResultMessage):
        yield Error(ts=now, message=msg.error or "(unknown error)")
        yield from _metrics_from_result(msg, now, model)
        yield StatusChange(ts=now, status="idle")
        return
    if isinstance(msg, ResultMessage):
        yield from _metrics_from_result(msg, now, model)
        yield StatusChange(ts=now, status="idle")
        return


def _metrics_from_result(
    msg: ResultMessage | ErrorResultMessage, now: datetime, model: str | None
) -> Iterable[TurnMetrics]:
    usage = msg.usage
    yield TurnMetrics(
        ts=now,
        duration_ms=msg.duration_ms,
        input_tokens=usage.input_tokens if usage else 0,
        output_tokens=usage.output_tokens if usage else 0,
        cache_read_input_tokens=usage.cache_read_input_tokens if usage else 0,
        cache_creation_input_tokens=usage.cache_creation_input_tokens if usage else 0,
        model=model,
    )


@build_adapter.register
def _build_amp_adapter(config: AmpAgentConfig, settings: Settings) -> AgentAdapter:
    return AmpAdapter(config)


__all__ = ["AmpAdapter", "ExecuteFn"]
