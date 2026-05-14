"""Unit tests for the Amp adapter's message-conversion logic + lifecycle.

Live SDK integration is verified by manual smoke test (requires the
``amp`` CLI + ``amp login`` or ``AMP_API_KEY``). These tests exercise
``_convert`` against synthetic StreamMessage objects to lock in the
Amp → AgentEvent mapping, and the full lifecycle through a fake
executor injected at construction time.
"""

import asyncio
from collections.abc import AsyncIterator
from pathlib import Path

import pytest
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
    Usage,
    UserInputMessage,
    UserMessage,
)
from amp_sdk.types import _AssistantMessageDetails, _UserMessageDetails

from src.domain.agents import (
    AgentEvent,
    AgentStartContext,
    AmpAgentConfig,
    AmpMode,
    ArtifactMarker,
    CommonAgentConfig,
    Error,
    HandoffOffered,
    MessageComplete,
    PermissionDecision,
    PermissionRequest,
    SessionEstablished,
    StatusChange,
    ToolCall,
    ToolResult,
    TurnMetrics,
)
from src.infrastructure.agents import amp_adapter as _amp_adapter_module
from src.infrastructure.agents.amp_adapter import (
    AmpAdapter,
    _assistant_prompt_tokens,
    _convert,
    _TurnAccumulator,
)


def _start_context() -> AgentStartContext:
    return AgentStartContext(
        workdir=Path("/tmp/amp-adapter-test"),
        model="smart",
        system_prompt="prompt",
    )


def _config() -> AmpAgentConfig:
    return AmpAgentConfig(
        common=CommonAgentConfig(
            workdir=Path("/tmp/amp-adapter-test"), system_prompt="prompt"
        ),
        mode=AmpMode.SMART,
    )


# --- StreamMessage builders ------------------------------------------------


def _system() -> SystemMessage:
    return SystemMessage(session_id="s-1", cwd="/tmp", tools=["Bash"], mcp_servers=[])


def _assistant(*blocks: object) -> AssistantMessage:
    return AssistantMessage(
        session_id="s-1",
        message=_AssistantMessageDetails(content=list(blocks)),
    )


def _user(*blocks: object) -> UserMessage:
    return UserMessage(
        session_id="s-1",
        message=_UserMessageDetails(content=list(blocks)),
    )


def _result_ok() -> ResultMessage:
    return ResultMessage(
        session_id="s-1", result="done", duration_ms=10, num_turns=1
    )


def _result_err(error: str = "boom") -> ErrorResultMessage:
    return ErrorResultMessage(
        session_id="s-1", error=error, duration_ms=5, num_turns=0
    )


# --- _convert mapping tests ------------------------------------------------


def test_system_message_yields_nothing() -> None:
    assert list(_convert(_system())) == []


def test_user_text_yields_thinking_status() -> None:
    [event] = list(_convert(_user(TextContent(text="hi"))))
    assert isinstance(event, StatusChange)
    assert event.status == "thinking"


def test_user_tool_result_maps_to_tool_result() -> None:
    [event] = list(
        _convert(
            _user(ToolResultContent(tool_use_id="t-1", content="ok", is_error=False))
        )
    )
    assert isinstance(event, ToolResult)
    assert event.tool_id == "t-1"
    assert event.content == "ok"
    assert event.is_error is False


def test_assistant_text_maps_to_message_complete() -> None:
    [event] = list(_convert(_assistant(TextContent(text="hello"))))
    assert isinstance(event, MessageComplete)
    assert event.text == "hello"


def test_assistant_handoff_text_emits_handoff_offered_after_message() -> None:
    """Amp's smart-mode auto-handoff produces an assistant message of the
    form 'Handoff created — work continues in T-...'. The adapter mirrors
    the text as a normal MessageComplete and emits a HandoffOffered so
    the UI can offer the user a one-click switch."""
    text = (
        "Handoff created — work continues in "
        "T-019e2766-01b7-70ce-90d8-be2b8d9cb40f. The new thread has the "
        "full plan."
    )
    events = list(_convert(_assistant(TextContent(text=text))))
    assert len(events) == 2
    msg, handoff = events
    assert isinstance(msg, MessageComplete)
    assert isinstance(handoff, HandoffOffered)
    assert handoff.new_thread_id == "T-019e2766-01b7-70ce-90d8-be2b8d9cb40f"


def test_assistant_text_without_handoff_phrase_emits_no_handoff() -> None:
    """A bare T-id in conversation (e.g. user discussing a thread) must
    not trigger a handoff — detection is anchored on the 'Handoff
    created' phrase to keep false-positives off the rail."""
    text = "I'll continue in T-019e2766-01b7-70ce-90d8-be2b8d9cb40f tomorrow."
    events = list(_convert(_assistant(TextContent(text=text))))
    assert len(events) == 1
    assert isinstance(events[0], MessageComplete)


def test_assistant_handoff_phrase_without_uuid_emits_no_handoff() -> None:
    """A malformed thread id (or none at all) must not produce a partial
    HandoffOffered — we require a properly-shaped UUID to commit to a
    switch target."""
    events = list(
        _convert(_assistant(TextContent(text="Handoff created — see Slack.")))
    )
    assert len(events) == 1
    assert isinstance(events[0], MessageComplete)


def test_assistant_tool_use_maps_to_tool_call() -> None:
    [event] = list(
        _convert(_assistant(ToolUseContent(id="t-1", name="bash", input={"cmd": "ls"})))
    )
    assert isinstance(event, ToolCall)
    assert event.tool_id == "t-1"
    assert event.name == "bash"
    assert event.arguments == {"cmd": "ls"}


def test_atelier_record_jira_tool_use_emits_artifact_marker_then_tool_call() -> None:
    """The artifact-recording tools produce an ArtifactMarker for the
    supervisor's tracker AND a regular ToolCall."""
    events = list(
        _convert(
            _assistant(
                ToolUseContent(
                    id="t-1",
                    name="mcp__atelier__record_jira",
                    input={
                        "url": "https://j/X-1",
                        "title": "Implement bar",
                        "status": "in_progress",
                    },
                )
            )
        )
    )
    assert len(events) == 2
    marker, call = events
    assert isinstance(marker, ArtifactMarker)
    assert marker.payload == {
        "type": "jira",
        "url": "https://j/X-1",
        "title": "Implement bar",
        "status": "in_progress",
    }
    assert isinstance(call, ToolCall)
    assert call.name == "mcp__atelier__record_jira"


def test_multi_block_assistant_yields_in_order() -> None:
    events = list(
        _convert(
            _assistant(
                TextContent(text="thinking-equiv"),
                ToolUseContent(id="t-1", name="bash", input={}),
            )
        )
    )
    assert isinstance(events[0], MessageComplete)
    assert isinstance(events[1], ToolCall)


def test_result_success_yields_metrics_then_idle() -> None:
    events = list(_convert(_result_ok()))
    assert isinstance(events[0], TurnMetrics)
    assert events[0].duration_ms == 10
    assert isinstance(events[1], StatusChange)
    assert events[1].status == "idle"


def test_result_error_yields_error_then_metrics_then_idle() -> None:
    events = list(_convert(_result_err("boom")))
    assert isinstance(events[0], Error)
    assert "boom" in events[0].message
    assert isinstance(events[1], TurnMetrics)
    assert isinstance(events[2], StatusChange)
    assert events[2].status == "idle"


def test_result_metrics_carry_usage_and_model() -> None:
    msg = ResultMessage(
        session_id="s-1",
        result="done",
        duration_ms=4321,
        num_turns=2,
        usage=Usage(
            input_tokens=200,
            output_tokens=80,
            cache_read_input_tokens=10,
            cache_creation_input_tokens=3,
        ),
    )
    [metrics, _idle] = list(_convert(msg, model="rush"))
    assert isinstance(metrics, TurnMetrics)
    assert metrics.duration_ms == 4321
    assert metrics.input_tokens == 200
    assert metrics.output_tokens == 80
    assert metrics.cache_read_input_tokens == 10
    assert metrics.cache_creation_input_tokens == 3
    assert metrics.model == "rush"
    # Pump-tracked value isn't passed in this direct ``_convert`` call, so
    # the field defaults to 0; pump propagation is verified below.
    assert metrics.last_prompt_tokens == 0


def test_assistant_prompt_tokens_pulls_from_message_usage() -> None:
    msg = AssistantMessage(
        session_id="s-1",
        message=_AssistantMessageDetails(
            content=[],
            usage=Usage(
                input_tokens=180,
                output_tokens=40,
                cache_read_input_tokens=11_500,
                cache_creation_input_tokens=320,
            ),
        ),
    )
    # Output excluded; the rest sums to the prompt size for that call.
    assert _assistant_prompt_tokens(msg) == 12_000


def test_assistant_prompt_tokens_returns_none_without_usage() -> None:
    assert _assistant_prompt_tokens(_assistant(TextContent(text="hi"))) is None
    assert _assistant_prompt_tokens(_result_ok()) is None


def test_convert_propagates_last_prompt_tokens_onto_amp_metrics() -> None:
    msg = ResultMessage(
        session_id="s-1", result="done", duration_ms=10, num_turns=1
    )
    [metrics, _idle] = list(
        _convert(msg, model="smart", last_prompt_tokens=98_400)
    )
    assert isinstance(metrics, TurnMetrics)
    assert metrics.last_prompt_tokens == 98_400


# --- Lifecycle tests with a fake executor ----------------------------------


def _make_fake_executor(scripted: list[StreamMessage]):
    """Return an executor that drains the prompt iterator (so close() works)
    while yielding the scripted StreamMessages."""

    async def fake(
        prompt: AsyncIterator[UserInputMessage], options: AmpOptions
    ) -> AsyncIterator[StreamMessage]:
        # Drain any inputs concurrently so close() (which pushes the
        # shutdown sentinel) actually unblocks the iterator.
        async def _drain() -> None:
            async for _ in prompt:
                pass

        drain_task = asyncio.create_task(_drain())
        try:
            for msg in scripted:
                yield msg
        finally:
            drain_task.cancel()
            try:
                await drain_task
            except BaseException:
                pass

    return fake


def test_full_lifecycle_translates_scripted_session() -> None:
    scripted: list[StreamMessage] = [
        _system(),
        _user(TextContent(text="hi")),
        _assistant(TextContent(text="hello back")),
        _result_ok(),
    ]
    adapter = AmpAdapter(_config(), executor=_make_fake_executor(scripted))

    async def session() -> list[AgentEvent]:
        await adapter.start(_start_context())
        await adapter.send_input("hi")
        events = [ev async for ev in adapter.events()]
        await adapter.close()
        return events

    events = asyncio.run(session())
    # system → SessionEstablished (first message carries session_id);
    # user/text → thinking; assistant/text → message_complete;
    # result → turn_metrics + idle.
    assert [type(e) for e in events] == [
        SessionEstablished,
        StatusChange,
        MessageComplete,
        TurnMetrics,
        StatusChange,
    ]
    assert events[0].session_id == "s-1"
    assert events[1].status == "thinking"
    assert events[4].status == "idle"


def test_close_is_idempotent() -> None:
    adapter = AmpAdapter(_config(), executor=_make_fake_executor([]))

    async def session() -> None:
        await adapter.start(_start_context())
        await adapter.close()
        await adapter.close()  # must not raise

    asyncio.run(session())


def test_start_twice_raises() -> None:
    adapter = AmpAdapter(_config(), executor=_make_fake_executor([]))

    async def session() -> None:
        await adapter.start(_start_context())
        with pytest.raises(RuntimeError, match="start\\(\\) called twice"):
            await adapter.start(_start_context())

    asyncio.run(session())


def test_events_before_start_raises() -> None:
    adapter = AmpAdapter(_config(), executor=_make_fake_executor([]))

    async def session() -> None:
        with pytest.raises(RuntimeError, match="events\\(\\) called before start"):
            async for _ in adapter.events():
                pass

    asyncio.run(session())


# --- End-of-turn synthesis (deep / GPT-backed mode) -----------------------


def _assistant_with_usage(
    *blocks: object,
    input_tokens: int = 0,
    output_tokens: int = 0,
    stop_reason: str | None = None,
) -> AssistantMessage:
    return AssistantMessage(
        session_id="s-1",
        message=_AssistantMessageDetails(
            content=list(blocks),
            usage=Usage(
                input_tokens=input_tokens,
                output_tokens=output_tokens,
            ),
            stop_reason=stop_reason,
        ),
    )


def test_turn_accumulator_signals_end_when_no_tool_use() -> None:
    """Text-only AssistantMessage → end_signaled (short quiescence)."""
    acc = _TurnAccumulator()
    acc.observe(_assistant_with_usage(TextContent(text="done"), input_tokens=100))
    assert acc.has_activity
    assert acc.next_quiescence_timeout() == _amp_adapter_module._QUIESCENCE_END_SIGNALED


def test_turn_accumulator_waits_long_when_tool_use_pending() -> None:
    """Tool-use AssistantMessage → expecting_tool_result (long quiescence)."""
    acc = _TurnAccumulator()
    acc.observe(
        _assistant_with_usage(
            ToolUseContent(id="t-1", name="bash", input={}),
            input_tokens=100,
        )
    )
    assert acc.next_quiescence_timeout() == _amp_adapter_module._QUIESCENCE_TOOL_PENDING


def test_turn_accumulator_tool_result_clears_pending() -> None:
    acc = _TurnAccumulator()
    acc.observe(
        _assistant_with_usage(
            ToolUseContent(id="t-1", name="bash", input={}),
            input_tokens=100,
        )
    )
    acc.observe(
        UserMessage(
            session_id="s-1",
            message=_UserMessageDetails(
                content=[
                    ToolResultContent(tool_use_id="t-1", content="ok", is_error=False)
                ]
            ),
        )
    )
    # Tool came back; we're no longer expecting one — fall back to default.
    assert acc.next_quiescence_timeout() == _amp_adapter_module._QUIESCENCE_DEFAULT


def test_turn_accumulator_no_activity_blocks_indefinitely() -> None:
    """No usage observed → next_quiescence_timeout returns None so the
    pump blocks on the iterator instead of synth-emitting a phantom
    turn close."""
    acc = _TurnAccumulator()
    assert acc.next_quiescence_timeout() is None


def test_synth_close_fires_when_amp_omits_result(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The motivating Amp-deep-mode bug: CLI never emits ResultMessage,
    so the pump must synthesise TurnMetrics + idle on its own once the
    stream goes quiet."""
    # Squeeze the timeouts so the test runs in milliseconds.
    monkeypatch.setattr(
        _amp_adapter_module, "_QUIESCENCE_END_SIGNALED", 0.05
    )
    monkeypatch.setattr(_amp_adapter_module, "_QUIESCENCE_DEFAULT", 0.05)

    async def gpt_mode_executor(
        prompt: AsyncIterator[UserInputMessage], options: AmpOptions
    ) -> AsyncIterator[StreamMessage]:
        async def _drain() -> None:
            async for _ in prompt:
                pass

        drain = asyncio.create_task(_drain())
        try:
            yield _system()
            yield _user(TextContent(text="hi"))
            yield _assistant_with_usage(
                TextContent(text="hello back"),
                input_tokens=200,
                output_tokens=50,
            )
            # NO ResultMessage — mimics openai-responses path in Amp.
            # Block until close() shuts us down.
            await asyncio.sleep(10)
        finally:
            drain.cancel()
            try:
                await drain
            except BaseException:
                pass

    adapter = AmpAdapter(_config(), executor=gpt_mode_executor)

    async def session() -> list[AgentEvent]:
        await adapter.start(_start_context())
        await adapter.send_input("hi")
        events: list[AgentEvent] = []
        async for ev in adapter.events():
            events.append(ev)
            # Stop once we've seen the synthetic idle so close() can run.
            if isinstance(ev, StatusChange) and ev.status == "idle":
                break
        await adapter.close()
        return events

    events = asyncio.run(session())
    assert isinstance(events[0], SessionEstablished)
    assert isinstance(events[1], StatusChange) and events[1].status == "thinking"
    assert isinstance(events[2], MessageComplete)
    metrics, idle = events[3], events[4]
    assert isinstance(metrics, TurnMetrics)
    assert metrics.input_tokens == 200
    assert metrics.output_tokens == 50
    assert isinstance(idle, StatusChange) and idle.status == "idle"


def test_executor_exception_yields_error_then_idle() -> None:
    async def boom_executor(
        prompt: AsyncIterator[UserInputMessage], options: AmpOptions
    ) -> AsyncIterator[StreamMessage]:
        # Drain one input so send_input doesn't block forever.
        async def _drain() -> None:
            async for _ in prompt:
                return

        drain_task = asyncio.create_task(_drain())
        try:
            raise RuntimeError("amp blew up")
            yield  # pragma: no cover — make this an async generator
        finally:
            drain_task.cancel()
            try:
                await drain_task
            except BaseException:
                pass

    adapter = AmpAdapter(_config(), executor=boom_executor)

    async def session() -> list[AgentEvent]:
        await adapter.start(_start_context())
        events = [ev async for ev in adapter.events()]
        await adapter.close()
        return events

    events = asyncio.run(session())
    assert isinstance(events[0], Error)
    assert "amp blew up" in events[0].message
    assert isinstance(events[1], StatusChange)
    assert events[1].status == "idle"


# --- Resume / session_id tests --------------------------------------------


def _capturing_executor(scripted: list[StreamMessage], captured: dict[str, AmpOptions]):
    """Executor that records the AmpOptions it was called with."""

    async def fake(
        prompt: AsyncIterator[UserInputMessage], options: AmpOptions
    ) -> AsyncIterator[StreamMessage]:
        captured["options"] = options

        async def _drain() -> None:
            async for _ in prompt:
                pass

        drain_task = asyncio.create_task(_drain())
        try:
            for msg in scripted:
                yield msg
        finally:
            drain_task.cancel()
            try:
                await drain_task
            except BaseException:
                pass

    return fake


def test_passes_continue_thread_when_session_id_set() -> None:
    captured: dict[str, AmpOptions] = {}
    adapter = AmpAdapter(_config(), executor=_capturing_executor([_result_ok()], captured))

    async def session() -> None:
        ctx = AgentStartContext(
            workdir=Path("/tmp/amp-adapter-test"),
            model="smart",
            system_prompt="prompt",
            session_id="thread-xyz",
        )
        await adapter.start(ctx)
        await adapter.send_input("hi")
        async for _ in adapter.events():
            pass
        await adapter.close()

    asyncio.run(session())
    # Pydantic field is camelCase under the hood, but the snake_case
    # attribute is exposed on the model instance.
    assert captured["options"].continue_thread == "thread-xyz"


def test_omits_continue_thread_when_session_id_unset() -> None:
    captured: dict[str, AmpOptions] = {}
    adapter = AmpAdapter(_config(), executor=_capturing_executor([_result_ok()], captured))

    async def session() -> None:
        await adapter.start(_start_context())  # session_id default None
        await adapter.send_input("hi")
        async for _ in adapter.events():
            pass
        await adapter.close()

    asyncio.run(session())
    # Default for unset Union[bool, str, None] is None.
    assert captured["options"].continue_thread is None


def test_session_established_emitted_only_once_for_repeat_id() -> None:
    # Two messages carrying the same session_id should yield exactly one
    # SessionEstablished — the supervisor doesn't need to round-trip
    # the workstore on every turn.
    scripted: list[StreamMessage] = [
        _system(),  # session_id="s-1"
        _assistant(TextContent(text="a")),  # session_id="s-1"
        _result_ok(),
    ]
    adapter = AmpAdapter(_config(), executor=_make_fake_executor(scripted))

    async def session() -> list[AgentEvent]:
        await adapter.start(_start_context())
        await adapter.send_input("hi")
        events = [ev async for ev in adapter.events()]
        await adapter.close()
        return events

    events = asyncio.run(session())
    sess_events = [e for e in events if isinstance(e, SessionEstablished)]
    assert len(sess_events) == 1
    assert sess_events[0].session_id == "s-1"


# --- Permission lifecycle / orphan prevention ------------------------------
#
# These tests pin the contract that every ``PermissionRequest`` published to
# the transcript is eventually paired with a matching ``PermissionDecision``,
# even when the user closes the adapter (or the agent dies) while a prompt
# is still open. Without this guarantee the frontend rebuilds
# ``pendingPermissions`` from the transcript on reconnect and shows a
# perpetually-stuck "Allow ...?" prompt that no button can dismiss.


def _make_idle_executor():
    """Executor that stays open until the prompt iterator is closed
    (i.e. ``close()`` pushes ``_SHUTDOWN`` into ``_user_inputs``). Used
    by the permission-lifecycle tests so the SDK pump doesn't push its
    own ``_SHUTDOWN`` into ``_outgoing`` and prematurely terminate the
    consumer before the prompt + decision events arrive."""

    async def fake(
        prompt: AsyncIterator[UserInputMessage], options: AmpOptions
    ) -> AsyncIterator[StreamMessage]:
        async for _ in prompt:  # blocks until the adapter closes the iterator
            pass
        return
        yield  # type: ignore[unreachable]  # makes this a generator

    return fake


def test_resolve_permission_emits_exactly_one_decision() -> None:
    """Happy path: user clicks Allow → adapter publishes one matching
    ``PermissionDecision`` and the request is removed from ``_pending``."""
    adapter = AmpAdapter(_config(), executor=_make_idle_executor())

    async def session() -> tuple[list[AgentEvent], str]:
        await adapter.start(_start_context())
        events_got: list[AgentEvent] = []

        async def consumer() -> None:
            async for ev in adapter.events():
                events_got.append(ev)

        consumer_task = asyncio.create_task(consumer())
        perm_task = asyncio.create_task(
            adapter._decide_permission("Bash", ["-c", "ls"])
        )

        # Wait for the prompt to land in the queue and reach the consumer.
        for _ in range(50):
            await asyncio.sleep(0.01)
            if any(isinstance(e, PermissionRequest) for e in events_got):
                break
        request_id = next(
            e.request_id for e in events_got if isinstance(e, PermissionRequest)
        )
        await adapter.resolve_permission(request_id, "allow")
        decision = await perm_task

        await adapter.close()
        await consumer_task
        return events_got, decision

    events, decision = asyncio.run(session())
    assert decision == "allow"
    requests = [e for e in events if isinstance(e, PermissionRequest)]
    decisions = [e for e in events if isinstance(e, PermissionDecision)]
    assert len(requests) == 1
    assert len(decisions) == 1
    assert decisions[0].request_id == requests[0].request_id
    assert decisions[0].decision == "allow"
    # No duplicate publish from close()'s orphan-prevention path.
    assert adapter._decided == {requests[0].request_id}
    assert adapter._pending == {}


def test_close_publishes_decision_for_pending_permission() -> None:
    """Orphan prevention: ``close()`` with a pending prompt MUST publish a
    synthetic ``PermissionDecision(deny)`` so the transcript stays balanced.
    Without this, the frontend's transcript-replay rebuild leaves the
    "Allow ...?" prompt visible forever."""
    adapter = AmpAdapter(_config(), executor=_make_idle_executor())

    async def session() -> tuple[list[AgentEvent], str]:
        await adapter.start(_start_context())
        events_got: list[AgentEvent] = []

        async def consumer() -> None:
            async for ev in adapter.events():
                events_got.append(ev)

        consumer_task = asyncio.create_task(consumer())
        perm_task = asyncio.create_task(
            adapter._decide_permission("Bash", ["-c", "rm -rf /"])
        )

        for _ in range(50):
            await asyncio.sleep(0.01)
            if any(isinstance(e, PermissionRequest) for e in events_got):
                break
        # Sanity: the prompt is queued and the decider is still suspended.
        assert any(isinstance(e, PermissionRequest) for e in events_got)
        assert not perm_task.done()

        # Tear down with the prompt still open — exactly the bug scenario.
        await adapter.close()
        decision = await perm_task
        await consumer_task
        return events_got, decision

    events, decision = asyncio.run(session())
    assert decision == "deny"
    requests = [e for e in events if isinstance(e, PermissionRequest)]
    decisions = [e for e in events if isinstance(e, PermissionDecision)]
    # Exactly one request and one matching decision — no orphan, no
    # duplicate from the late-waking ``_decide_permission`` task.
    assert len(requests) == 1
    assert len(decisions) == 1
    assert decisions[0].request_id == requests[0].request_id
    assert decisions[0].decision == "deny"


def test_close_with_no_pending_permissions_emits_no_decisions() -> None:
    """Guardrail: the orphan-prevention loop in ``close()`` must not fire
    spuriously when there's nothing pending."""
    adapter = AmpAdapter(_config(), executor=_make_fake_executor([]))

    async def session() -> list[AgentEvent]:
        await adapter.start(_start_context())
        events_got: list[AgentEvent] = []

        async def consumer() -> None:
            async for ev in adapter.events():
                events_got.append(ev)

        consumer_task = asyncio.create_task(consumer())
        await adapter.close()
        await consumer_task
        return events_got

    events = asyncio.run(session())
    assert [e for e in events if isinstance(e, PermissionDecision)] == []
