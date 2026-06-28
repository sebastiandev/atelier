"""Unit tests for AcpAdapter — fake connection, no subprocess, no CLI.

The fake drives the same callback surface the SDK router uses
(``adapter.session_update`` / ``adapter.request_permission``), so the
tests exercise the real queue choreography end to end.
"""

import asyncio
import signal
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import pytest
from acp.schema import (
    AgentMessageChunk,
    ConfigOptionUpdate,
    PermissionOption,
    TextContentBlock,
    ToolCallStart,
    UsageUpdate,
)

from src.domain.agents import (
    AcpAgentConfig,
    AgentStartContext,
    CommonAgentConfig,
    Error,
    MessageComplete,
    PermissionDecision,
    PermissionRequest,
    SessionConfigChanged,
    SessionConfigOptions,
    SessionEstablished,
    StatusChange,
    ToolCall,
    TurnMetrics,
)
from src.infrastructure.agents.acp import AcpAdapter
from src.infrastructure.agents.acp import adapter as acp_adapter

WORKDIR = Path("/tmp/acp-test-ws")


@dataclass(frozen=True, kw_only=True)
class _TestAcpConfig(AcpAgentConfig):
    """Provider-flavoured config standing in for claude-acp/codex-acp."""

    desired_options: tuple[tuple[str, str], ...] = ()
    desired_mode: str | None = None

    def acp_config_values(self) -> tuple[tuple[str, str], ...]:
        return self.desired_options

    def acp_mode_id(self) -> str | None:
        return self.desired_mode


def _config(**kwargs: Any) -> _TestAcpConfig:
    return _TestAcpConfig(
        common=CommonAgentConfig(workdir=WORKDIR, system_prompt="You are agt-1."),
        **kwargs,
    )


@dataclass
class _Obj:
    """Attribute bag for fake protocol responses."""

    def __init__(self, **kw: Any) -> None:
        self.__dict__.update(kw)


def _caps(load_session: bool = False, resume: bool = False) -> Any:
    return _Obj(
        load_session=load_session,
        session_capabilities=_Obj(resume=_Obj() if resume else None),
    )


def _select_option(option_id: str, *values: str, current: str | None = None) -> Any:
    return _Obj(
        id=option_id,
        name=option_id.title(),
        type="select",
        category=option_id,
        current_value=current if current is not None else (values[0] if values else None),
        options=[_Obj(value=v, name=v.title()) for v in values],
    )


@dataclass
class FakeConnection:
    caps: Any = field(default_factory=_caps)
    session_id: str = "sess_1"
    config_options: list[Any] = field(default_factory=list)
    set_config_response_options: list[Any] = field(default_factory=list)
    modes: Any = None
    prompt_script: list[Any] = field(default_factory=list)
    calls: list[tuple[str, dict[str, Any]]] = field(default_factory=list)
    adapter: Any = None
    load_should_fail: bool = False

    async def initialize(self, protocol_version: int, **kw: Any) -> Any:
        self.calls.append(("initialize", {"protocol_version": protocol_version}))
        return _Obj(protocol_version=1, agent_capabilities=self.caps)

    async def new_session(self, cwd: str, **kw: Any) -> Any:
        self.calls.append(("new_session", {"cwd": cwd, **kw}))
        return _Obj(
            session_id=self.session_id,
            config_options=self.config_options,
            modes=self.modes,
        )

    async def load_session(self, cwd: str, session_id: str, **kw: Any) -> Any:
        self.calls.append(("load_session", {"cwd": cwd, "session_id": session_id}))
        if self.load_should_fail:
            raise RuntimeError("no such session")
        # Replay arrives as ordinary updates before the response resolves.
        await self.adapter.session_update(
            session_id,
            AgentMessageChunk(
                session_update="agent_message_chunk",
                content=TextContentBlock(type="text", text="replayed history"),
                message_id="old",
            ),
        )
        return _Obj(session_id=session_id, config_options=[], modes=None)

    async def resume_session(self, cwd: str, session_id: str, **kw: Any) -> Any:
        self.calls.append(("resume_session", {"session_id": session_id}))
        return _Obj(session_id=session_id, config_options=[], modes=None)

    async def prompt(self, prompt: list[Any], session_id: str, **kw: Any) -> Any:
        self.calls.append(("prompt", {"prompt": prompt, "session_id": session_id}))
        step = self.prompt_script.pop(0) if self.prompt_script else None
        if callable(step):
            return await step(self, session_id)
        return _Obj(stop_reason="end_turn", usage=None)

    async def cancel(self, session_id: str, **kw: Any) -> None:
        self.calls.append(("cancel", {"session_id": session_id}))

    async def set_config_option(
        self, config_id: str, session_id: str, value: Any, **kw: Any
    ) -> Any:
        self.calls.append(("set_config_option", {"config_id": config_id, "value": value}))
        return _Obj(config_options=self.set_config_response_options)

    async def set_session_mode(self, mode_id: str, session_id: str, **kw: Any) -> Any:
        self.calls.append(("set_session_mode", {"mode_id": mode_id}))
        return _Obj()

    async def close(self) -> None:
        self.calls.append(("close", {}))

    def called(self, name: str) -> list[dict[str, Any]]:
        return [kw for n, kw in self.calls if n == name]


def _build(config: Any = None, **fake_kw: Any) -> tuple[AcpAdapter, FakeConnection]:
    fake = FakeConnection(**fake_kw)

    async def factory(adapter: AcpAdapter) -> FakeConnection:
        fake.adapter = adapter
        return fake

    adapter = AcpAdapter(
        config or _config(),
        argv=("fake-agent",),
        model_label="test-model",
        connect_factory=factory,
    )
    return adapter, fake


async def _collect_turn(adapter: AcpAdapter, text: str) -> list[Any]:
    """Send one input and collect events until the trailing idle."""
    events: list[Any] = []
    await adapter.send_input(text)
    gen = adapter.events()
    async for event in gen:
        events.append(event)
        if isinstance(event, StatusChange) and event.status == "idle":
            break
    await gen.aclose()
    return events


def test_start_creates_session_and_injects_mcp_server() -> None:
    async def scenario() -> None:
        adapter, fake = _build()
        await adapter.start(AgentStartContext(workdir=WORKDIR, model="m", system_prompt="s"))
        (new_session,) = fake.called("new_session")
        servers = new_session["mcp_servers"]
        assert len(servers) == 1
        assert servers[0].name == "atelier"
        assert new_session["cwd"] == str(WORKDIR)
        await adapter.close()

    asyncio.run(scenario())

def test_events_lead_with_session_established() -> None:
    async def scenario() -> None:
        adapter, _fake = _build()
        await adapter.start(AgentStartContext(workdir=WORKDIR, model="m", system_prompt="s"))
        events = await _collect_turn(adapter, "hi")
        assert isinstance(events[0], SessionEstablished)
        assert events[0].session_id == "sess_1"
        await adapter.close()

    asyncio.run(scenario())

def test_config_options_applied_only_when_advertised() -> None:
    async def scenario() -> None:
        config = _config(
            desired_options=(("model", "sonnet"), ("effort", "xhigh")),
            desired_mode="acceptEdits",
        )
        adapter, fake = _build(
            config,
            config_options=[_select_option("model", "default", "sonnet", "haiku")],
            modes=_Obj(
                current_mode_id="default",
                available_modes=[_Obj(id="default"), _Obj(id="acceptEdits")],
            ),
        )
        await adapter.start(AgentStartContext(workdir=WORKDIR, model="m", system_prompt="s"))
        applied = fake.called("set_config_option")
        # "model" is advertised with a matching value → applied; "effort" is
        # not advertised at all → applied optimistically (the agent may
        # accept ids it doesn't list); a *listed* id with a non-listed value
        # would be skipped (covered below).
        assert {a["config_id"] for a in applied} == {"model", "effort"}
        assert fake.called("set_session_mode") == [{"mode_id": "acceptEdits"}]
        await adapter.close()

    asyncio.run(scenario())

def test_config_value_not_advertised_is_skipped() -> None:
    async def scenario() -> None:
        config = _config(desired_options=(("model", "gpt-9000"),))
        adapter, fake = _build(
            config, config_options=[_select_option("model", "default", "sonnet")]
        )
        await adapter.start(AgentStartContext(workdir=WORKDIR, model="m", system_prompt="s"))
        assert fake.called("set_config_option") == []
        await adapter.close()

    asyncio.run(scenario())

def test_session_config_options_are_emitted_for_ui() -> None:
    async def scenario() -> None:
        adapter, _fake = _build(
            config_options=[
                _select_option("model", "sonnet", "haiku", current="haiku")
            ]
        )
        await adapter.start(AgentStartContext(workdir=WORKDIR, model="m", system_prompt="s"))
        gen = adapter.events()
        established = await anext(gen)
        options = await anext(gen)
        assert isinstance(established, SessionEstablished)
        assert isinstance(options, SessionConfigOptions)
        assert options.options[0]["id"] == "model"
        assert options.options[0]["current_value"] == "haiku"
        assert options.options[0]["options"][1] == {
            "value": "haiku",
            "name": "Haiku",
        }
        await gen.aclose()
        await adapter.close()

    asyncio.run(scenario())

def test_live_session_config_change_calls_acp_and_emits_event() -> None:
    async def scenario() -> None:
        adapter, fake = _build(
            config_options=[_select_option("model", "sonnet", "haiku")]
        )
        await adapter.start(AgentStartContext(workdir=WORKDIR, model="m", system_prompt="s"))
        gen = adapter.events()
        await anext(gen)  # session_established
        await anext(gen)  # session_config_options

        await adapter.set_config_option("model", "haiku")
        event = await anext(gen)

        assert fake.called("set_config_option")[-1] == {
            "config_id": "model",
            "value": "haiku",
        }
        assert isinstance(event, SessionConfigChanged)
        assert event.config_id == "model"
        assert event.value == "haiku"
        await gen.aclose()
        await adapter.close()

    asyncio.run(scenario())


def test_live_session_config_change_emits_updated_options_from_response() -> None:
    async def scenario() -> None:
        adapter, fake = _build(
            config_options=[_select_option("model", "sonnet", "haiku")],
            set_config_response_options=[
                _select_option("model", "sonnet", "haiku", "gpt", current="haiku")
            ],
        )
        await adapter.start(AgentStartContext(workdir=WORKDIR, model="m", system_prompt="s"))
        gen = adapter.events()
        await anext(gen)  # session_established
        await anext(gen)  # session_config_options

        await adapter.set_config_option("model", "haiku")
        options = await anext(gen)
        changed = await anext(gen)

        assert fake.called("set_config_option")[-1] == {
            "config_id": "model",
            "value": "haiku",
        }
        assert isinstance(options, SessionConfigOptions)
        assert [choice["value"] for choice in options.options[0]["options"]] == [
            "sonnet",
            "haiku",
            "gpt",
        ]
        assert options.options[0]["current_value"] == "haiku"
        assert isinstance(changed, SessionConfigChanged)
        assert changed.value == "haiku"
        await gen.aclose()
        await adapter.close()

    asyncio.run(scenario())


def test_refresh_session_config_options_reapplies_current_value() -> None:
    async def scenario() -> None:
        adapter, fake = _build(
            config_options=[_select_option("model", "sonnet", "haiku")],
            set_config_response_options=[
                _select_option("model", "sonnet", "haiku", "gpt", current="sonnet")
            ],
        )
        await adapter.start(AgentStartContext(workdir=WORKDIR, model="m", system_prompt="s"))
        gen = adapter.events()
        await anext(gen)  # session_established
        await anext(gen)  # session_config_options

        await adapter.refresh_config_options("model")
        options = await anext(gen)

        assert fake.called("set_config_option")[-1] == {
            "config_id": "model",
            "value": "sonnet",
        }
        assert isinstance(options, SessionConfigOptions)
        assert [choice["value"] for choice in options.options[0]["options"]] == [
            "sonnet",
            "haiku",
            "gpt",
        ]
        await gen.aclose()
        await adapter.close()

    asyncio.run(scenario())


def test_config_option_update_notification_emits_options() -> None:
    async def scenario() -> None:
        adapter, _fake = _build(config_options=[_select_option("model", "sonnet")])
        await adapter.start(AgentStartContext(workdir=WORKDIR, model="m", system_prompt="s"))
        gen = adapter.events()
        await anext(gen)  # session_established
        await anext(gen)  # session_config_options

        await adapter.session_update(
            "sess_1",
            ConfigOptionUpdate(
                sessionUpdate="config_option_update",
                configOptions=[
                    {
                        "id": "model",
                        "name": "Model",
                        "type": "select",
                        "currentValue": "gpt",
                        "options": [
                            {"value": "sonnet", "name": "Sonnet"},
                            {"value": "gpt", "name": "Gpt"},
                        ],
                    }
                ],
            ),
        )
        options = await anext(gen)

        assert isinstance(options, SessionConfigOptions)
        assert options.options[0]["current_value"] == "gpt"
        assert [choice["value"] for choice in options.options[0]["options"]] == [
            "sonnet",
            "gpt",
        ]
        await gen.aclose()
        await adapter.close()

    asyncio.run(scenario())


def test_live_session_config_change_rejects_unadvertised_value() -> None:
    async def scenario() -> None:
        adapter, fake = _build(
            config_options=[_select_option("model", "sonnet", "haiku")]
        )
        await adapter.start(AgentStartContext(workdir=WORKDIR, model="m", system_prompt="s"))

        with pytest.raises(ValueError, match="does not allow"):
            await adapter.set_config_option("model", "gpt-9000")

        assert fake.called("set_config_option") == []
        await adapter.close()

    asyncio.run(scenario())

def test_first_prompt_carries_system_context_block() -> None:
    async def scenario() -> None:
        adapter, fake = _build()
        await adapter.start(AgentStartContext(workdir=WORKDIR, model="m", system_prompt="s"))
        await _collect_turn(adapter, "first")
        await adapter.send_input("second")
        # Drain the second turn directly off the queue-backed generator.
        events: list[Any] = []
        gen = adapter.events()
        async for event in gen:
            events.append(event)
            if isinstance(event, StatusChange) and event.status == "idle":
                break
        await gen.aclose()
        first, second = fake.called("prompt")
        assert len(first["prompt"]) == 2
        assert "<atelier-context>" in first["prompt"][0]["text"]
        assert "You are agt-1." in first["prompt"][0]["text"]
        assert first["prompt"][1]["text"] == "first"
        assert len(second["prompt"]) == 1
        assert second["prompt"][0]["text"] == "second"
        await adapter.close()

    asyncio.run(scenario())

def test_turn_streams_updates_and_folds_usage_into_metrics() -> None:
    async def scenario() -> None:
        async def scripted_prompt(fake: FakeConnection, session_id: str) -> Any:
            await fake.adapter.session_update(
                session_id,
                UsageUpdate(
                    session_update="usage_update",
                    used=24193,
                    size=1_000_000,
                    cost={"amount": 0.39, "currency": "USD"},
                ),
            )
            await fake.adapter.session_update(
                session_id,
                AgentMessageChunk(
                    session_update="agent_message_chunk",
                    content=TextContentBlock(type="text", text="done!"),
                    message_id="m1",
                ),
            )
            return _Obj(
                stop_reason="end_turn",
                usage=_Obj(
                    input_tokens=2200,
                    output_tokens=124,
                    cached_read_tokens=41557,
                    cached_write_tokens=4491,
                    total_tokens=48302,
                ),
            )

        adapter, _fake = _build(prompt_script=[scripted_prompt])
        await adapter.start(AgentStartContext(workdir=WORKDIR, model="m", system_prompt="s"))
        events = await _collect_turn(adapter, "go")
        assert any(isinstance(e, MessageComplete) and e.text == "done!" for e in events)
        (metrics,) = [e for e in events if isinstance(e, TurnMetrics)]
        assert metrics.input_tokens == 2200
        assert metrics.output_tokens == 124
        assert metrics.cache_read_input_tokens == 41557
        assert metrics.cache_creation_input_tokens == 4491
        assert metrics.last_prompt_tokens == 24193
        assert metrics.context_window == 1_000_000
        assert metrics.cost_usd == 0.39
        assert metrics.model == "test-model"
        assert isinstance(events[-1], StatusChange) and events[-1].status == "idle"
        await adapter.close()

    asyncio.run(scenario())

def test_refusal_stop_reason_surfaces_error() -> None:
    async def scenario() -> None:
        async def refusing_prompt(fake: FakeConnection, session_id: str) -> Any:
            return _Obj(stop_reason="refusal", usage=None)

        adapter, _fake = _build(prompt_script=[refusing_prompt])
        await adapter.start(AgentStartContext(workdir=WORKDIR, model="m", system_prompt="s"))
        events = await _collect_turn(adapter, "go")
        assert any(isinstance(e, Error) and "refused" in e.message for e in events)
        assert isinstance(events[-1], StatusChange) and events[-1].status == "idle"
        await adapter.close()

    asyncio.run(scenario())


def test_connection_closed_prompt_terminates_event_stream() -> None:
    async def scenario() -> None:
        async def closed_prompt(fake: FakeConnection, session_id: str) -> Any:
            raise ConnectionError("Connection closed")

        adapter, _fake = _build(prompt_script=[closed_prompt])
        await adapter.start(AgentStartContext(workdir=WORKDIR, model="m", system_prompt="s"))

        events: list[Any] = []
        await adapter.send_input("go")
        gen: Any = adapter.events()
        while True:
            try:
                event = await asyncio.wait_for(anext(gen), timeout=1)
            except StopAsyncIteration:
                break
            events.append(event)

        assert any(isinstance(e, Error) and e.message == "Connection closed" for e in events)
        assert not any(
            isinstance(e, StatusChange) and e.status == "idle" for e in events
        )
        await adapter.close()

    asyncio.run(scenario())


def test_connection_closed_prompt_rejects_followup_before_stream_drains() -> None:
    async def scenario() -> None:
        async def closed_prompt(fake: FakeConnection, session_id: str) -> Any:
            raise ConnectionError("Connection closed")

        adapter, _fake = _build(prompt_script=[closed_prompt])
        await adapter.start(AgentStartContext(workdir=WORKDIR, model="m", system_prompt="s"))

        await adapter.send_input("go")
        gen: Any = adapter.events()
        events: list[Any] = []
        while True:
            event = await asyncio.wait_for(anext(gen), timeout=1)
            events.append(event)
            if isinstance(event, Error) and event.message == "Connection closed":
                break

        with pytest.raises(ConnectionError, match="Connection closed"):
            await adapter.send_input("again")

        assert not any(
            isinstance(e, StatusChange) and e.status == "idle" for e in events
        )
        with pytest.raises(StopAsyncIteration):
            await asyncio.wait_for(anext(gen), timeout=1)
        await adapter.close()

    asyncio.run(scenario())


def test_connection_closed_prompt_recovers_by_resuming_session() -> None:
    async def scenario() -> None:
        async def closed_prompt(fake: FakeConnection, session_id: str) -> Any:
            raise ConnectionError("Connection closed")

        adapter, fake = _build(
            caps=_caps(load_session=True),
            prompt_script=[closed_prompt, None],
        )
        await adapter.start(AgentStartContext(workdir=WORKDIR, model="m", system_prompt="s"))

        events: list[Any] = []
        await adapter.send_input("go")
        gen: Any = adapter.events()
        async for event in gen:
            events.append(event)
            if isinstance(event, StatusChange) and event.status == "idle":
                break
        await gen.aclose()

        assert not any(
            isinstance(e, Error) and e.message == "Connection closed" for e in events
        )
        assert any(isinstance(e, TurnMetrics) for e in events)
        assert fake.called("load_session") == [
            {"cwd": str(WORKDIR), "session_id": "sess_1"}
        ]
        prompts = fake.called("prompt")
        assert prompts[1]["prompt"] == [
            {
                "type": "text",
                "text": (
                    "Continue from where you left off after the transport reconnect. "
                    "Do not repeat completed tool calls unless their results are missing."
                ),
            }
        ]
        await adapter.close()

    asyncio.run(scenario())


def test_restored_session_connection_loop_falls_back_to_fresh_session() -> None:
    async def scenario() -> None:
        async def closed_prompt(fake: FakeConnection, session_id: str) -> Any:
            raise ConnectionError("Connection closed")

        adapter, fake = _build(
            caps=_caps(load_session=True),
            prompt_script=[closed_prompt, closed_prompt, None],
        )
        await adapter.start(
            AgentStartContext(
                workdir=WORKDIR, model="m", system_prompt="s", session_id="sess_old"
            )
        )

        events = await _collect_turn(adapter, "go")

        established = [e.session_id for e in events if isinstance(e, SessionEstablished)]
        assert established == ["sess_old", "sess_1"]
        assert any(
            isinstance(e, Error) and "started a fresh session" in e.message
            for e in events
        )
        assert any(isinstance(e, TurnMetrics) for e in events)
        assert fake.called("load_session") == [
            {"cwd": str(WORKDIR), "session_id": "sess_old"},
            {"cwd": str(WORKDIR), "session_id": "sess_old"},
        ]
        assert len(fake.called("new_session")) == 1
        prompts = fake.called("prompt")
        assert prompts[0]["session_id"] == "sess_old"
        assert prompts[0]["prompt"] == [{"type": "text", "text": "go"}]
        assert prompts[1]["session_id"] == "sess_old"
        assert prompts[1]["prompt"] == [
            {
                "type": "text",
                "text": (
                    "Continue from where you left off after the transport reconnect. "
                    "Do not repeat completed tool calls unless their results are missing."
                ),
            }
        ]
        assert prompts[2]["session_id"] == "sess_1"
        assert len(prompts[2]["prompt"]) == 2
        assert "<atelier-context>" in prompts[2]["prompt"][0]["text"]
        assert prompts[2]["prompt"][1] == {"type": "text", "text": "go"}
        await adapter.close()

    asyncio.run(scenario())


def test_non_connection_prompt_error_keeps_event_stream_open() -> None:
    async def scenario() -> None:
        async def bad_turn(fake: FakeConnection, session_id: str) -> Any:
            raise RuntimeError("provider rejected turn")

        adapter, _fake = _build(prompt_script=[bad_turn, None])
        await adapter.start(AgentStartContext(workdir=WORKDIR, model="m", system_prompt="s"))

        events: list[Any] = []
        await adapter.send_input("bad")
        gen: Any = adapter.events()
        async for event in gen:
            events.append(event)
            if isinstance(event, StatusChange) and event.status == "idle":
                break

        assert any(
            isinstance(e, Error) and e.message == "provider rejected turn"
            for e in events
        )

        await adapter.send_input("recover")
        recovered: list[Any] = []
        async for event in gen:
            recovered.append(event)
            if isinstance(event, StatusChange) and event.status == "idle":
                break

        assert any(isinstance(e, TurnMetrics) for e in recovered)
        await gen.aclose()
        await adapter.close()

    asyncio.run(scenario())


def test_permission_round_trip_allow() -> None:
    async def scenario() -> None:
        response_holder: dict[str, Any] = {}

        async def prompting_prompt(fake: FakeConnection, session_id: str) -> Any:
            tool_call = ToolCallStart(
                session_update="tool_call",
                tool_call_id="t1",
                title="Write /ws/a.txt",
                kind="edit",
                raw_input={"file_path": "/ws/a.txt", "content": "x"},
                field_meta={"claudeCode": {"toolName": "Write"}},
            )
            await fake.adapter.session_update(session_id, tool_call)
            response_holder["permission"] = await fake.adapter.request_permission(
                options=[
                    PermissionOption(
                        option_id="allow_always", name="Always", kind="allow_always"
                    ),
                    PermissionOption(option_id="allow", name="Allow", kind="allow_once"),
                    PermissionOption(option_id="reject", name="Reject", kind="reject_once"),
                ],
                session_id=session_id,
                tool_call=tool_call,
            )
            return _Obj(stop_reason="end_turn", usage=None)

        adapter, _fake = _build(prompt_script=[prompting_prompt])
        await adapter.start(AgentStartContext(workdir=WORKDIR, model="m", system_prompt="s"))

        events: list[Any] = []
        await adapter.send_input("write it")
        gen = adapter.events()
        async for event in gen:
            events.append(event)
            if isinstance(event, PermissionRequest):
                await adapter.resolve_permission(event.request_id, "allow")
            if isinstance(event, StatusChange) and event.status == "idle":
                break
        await gen.aclose()

        (request,) = [e for e in events if isinstance(e, PermissionRequest)]
        assert request.tool_name == "Write"
        assert request.tool_id == "t1"
        assert request.options is not None and len(request.options) == 3
        (decision,) = [e for e in events if isinstance(e, PermissionDecision)]
        assert decision.decision == "allow"
        outcome = response_holder["permission"].outcome
        assert outcome.outcome == "selected"
        assert outcome.option_id == "allow"
        # The ToolCall event must precede the PermissionRequest so the FE
        # can anchor the prompt to its card.
        assert events.index(
            next(e for e in events if isinstance(e, ToolCall))
        ) < events.index(request)
        await adapter.close()

    asyncio.run(scenario())


def test_permission_request_uses_tool_identity_not_action_title() -> None:
    async def scenario() -> None:
        async def prompting_prompt(fake: FakeConnection, session_id: str) -> Any:
            tool_call = ToolCallStart(
                session_update="tool_call",
                tool_call_id="t1",
                title='"weather in Galway today"',
                kind="fetch",
                raw_input={"query": "weather in Galway today"},
                field_meta={"claudeCode": {"toolName": "WebSearch"}},
            )
            await fake.adapter.session_update(session_id, tool_call)
            await fake.adapter.request_permission(
                options=[
                    PermissionOption(
                        option_id="allow_always",
                        name="Always Allow all WebSearch",
                        kind="allow_always",
                    ),
                    PermissionOption(option_id="allow", name="Allow", kind="allow_once"),
                    PermissionOption(option_id="reject", name="Reject", kind="reject_once"),
                ],
                session_id=session_id,
                tool_call=tool_call,
            )
            return _Obj(stop_reason="end_turn", usage=None)

        adapter, _fake = _build(prompt_script=[prompting_prompt])
        await adapter.start(AgentStartContext(workdir=WORKDIR, model="m", system_prompt="s"))

        events: list[Any] = []
        await adapter.send_input("search")
        gen = adapter.events()
        async for event in gen:
            events.append(event)
            if isinstance(event, PermissionRequest):
                await adapter.resolve_permission(event.request_id, "allow")
            if isinstance(event, StatusChange) and event.status == "idle":
                break
        await gen.aclose()

        (request,) = [e for e in events if isinstance(e, PermissionRequest)]
        assert request.tool_name == "WebSearch"
        assert request.tool_input == {"query": "weather in Galway today"}
        await adapter.close()

    asyncio.run(scenario())


def test_stop_turn_cancels_pending_permission_with_cancelled_outcome() -> None:
    async def scenario() -> None:
        response_holder: dict[str, Any] = {}

        async def prompting_prompt(fake: FakeConnection, session_id: str) -> Any:
            tool_call = ToolCallStart(
                session_update="tool_call",
                tool_call_id="t1",
                title="Bash",
                kind="execute",
                raw_input={"command": "rm -rf /"},
            )
            response_holder["permission"] = await fake.adapter.request_permission(
                options=[
                    PermissionOption(option_id="allow", name="Allow", kind="allow_once"),
                    PermissionOption(option_id="reject", name="Reject", kind="reject_once"),
                ],
                session_id=session_id,
                tool_call=tool_call,
            )
            return _Obj(stop_reason="cancelled", usage=None)

        adapter, fake = _build(prompt_script=[prompting_prompt])
        await adapter.start(AgentStartContext(workdir=WORKDIR, model="m", system_prompt="s"))

        events: list[Any] = []
        await adapter.send_input("dangerous")
        gen = adapter.events()
        async for event in gen:
            events.append(event)
            if isinstance(event, PermissionRequest):
                await adapter.stop_turn()
            if isinstance(event, StatusChange) and event.status == "idle":
                break
        await gen.aclose()

        assert fake.called("cancel") == [{"session_id": "sess_1"}]
        assert response_holder["permission"].outcome.outcome == "cancelled"
        # Transcript still pairs the request with a decision.
        (decision,) = [e for e in events if isinstance(e, PermissionDecision)]
        assert decision.decision == "deny"
        await adapter.close()

    asyncio.run(scenario())

def test_restore_uses_load_session_and_suppresses_replay() -> None:
    async def scenario() -> None:
        adapter, fake = _build(caps=_caps(load_session=True))
        await adapter.start(
            AgentStartContext(
                workdir=WORKDIR, model="m", system_prompt="s", session_id="sess_old"
            )
        )
        assert fake.called("load_session") == [
            {"cwd": str(WORKDIR), "session_id": "sess_old"}
        ]
        assert fake.called("new_session") == []
        events = await _collect_turn(adapter, "continue")
        assert isinstance(events[0], SessionEstablished)
        assert events[0].session_id == "sess_old"
        # The replayed history chunk must NOT re-enter the event stream.
        assert not any(
            isinstance(e, MessageComplete) and "replayed" in e.text for e in events
        )
        await adapter.close()

    asyncio.run(scenario())

def test_restore_falls_back_to_fresh_session_with_warning() -> None:
    async def scenario() -> None:
        adapter, fake = _build(caps=_caps(load_session=True), load_should_fail=True)
        await adapter.start(
            AgentStartContext(
                workdir=WORKDIR, model="m", system_prompt="s", session_id="sess_gone"
            )
        )
        assert len(fake.called("new_session")) == 1
        events = await _collect_turn(adapter, "hello")
        assert isinstance(events[0], Error)
        assert "not restored" in events[0].message
        assert any(
            isinstance(e, SessionEstablished) and e.session_id == "sess_1" for e in events
        )
        await adapter.close()

    asyncio.run(scenario())

def test_restore_uses_resume_when_load_not_supported() -> None:
    async def scenario() -> None:
        adapter, fake = _build(caps=_caps(load_session=False, resume=True))
        await adapter.start(
            AgentStartContext(
                workdir=WORKDIR, model="m", system_prompt="s", session_id="sess_old"
            )
        )
        assert fake.called("resume_session") == [{"session_id": "sess_old"}]
        assert fake.called("new_session") == []
        await adapter.close()

    asyncio.run(scenario())


def test_terminate_process_tree_kills_process_group_on_posix(monkeypatch: Any) -> None:
    if acp_adapter.os.name != "posix":
        return

    class Proc:
        pid = 123
        returncode = None

        async def wait(self) -> None:
            self.returncode = 0

    killed: list[tuple[int, int]] = []
    monkeypatch.setattr(acp_adapter.os, "getpgid", lambda pid: 456)
    monkeypatch.setattr(
        acp_adapter.os,
        "killpg",
        lambda pgid, sig: killed.append((pgid, sig)),
    )

    asyncio.run(acp_adapter._terminate_process_tree(Proc()))  # type: ignore[arg-type]

    assert killed == [(456, signal.SIGTERM)]


def test_close_is_idempotent_and_closes_connection() -> None:
    async def scenario() -> None:
        adapter, fake = _build()
        await adapter.start(AgentStartContext(workdir=WORKDIR, model="m", system_prompt="s"))
        await adapter.close()
        await adapter.close()
        assert len(fake.called("close")) == 1

    asyncio.run(scenario())

def test_protocol_version_mismatch_fails_loudly() -> None:
    async def scenario() -> None:
        class BadVersionConnection(FakeConnection):
            async def initialize(self, protocol_version: int, **kw: Any) -> Any:
                return _Obj(protocol_version=2, agent_capabilities=_caps())

        fake = BadVersionConnection()

        async def factory(adapter: AcpAdapter) -> FakeConnection:
            fake.adapter = adapter
            return fake

        adapter = AcpAdapter(_config(), argv=("fake",), connect_factory=factory)
        with pytest.raises(RuntimeError, match="protocol version"):
            await adapter.start(
                AgentStartContext(workdir=WORKDIR, model="m", system_prompt="s")
            )

    asyncio.run(scenario())
