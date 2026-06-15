"""Unit tests for the AgentEvent tagged union."""

from datetime import UTC, datetime

import pytest

from src.domain.agents import (
    AgentEvent,
    ArtifactMarker,
    Error,
    MessageComplete,
    MessageDelta,
    ModeChange,
    PermissionRequest,
    PlanUpdate,
    ProviderContextCompacted,
    StatusChange,
    ToolCall,
    ToolCallUpdate,
    ToolResult,
    TurnMetrics,
)

UTC_NOW = datetime(2026, 5, 1, 13, 49, tzinfo=UTC)


def test_message_delta_carries_discriminator() -> None:
    event = MessageDelta(ts=UTC_NOW, text="hello")
    assert event.type == "message_delta"
    assert event.text == "hello"


def test_message_complete() -> None:
    event = MessageComplete(ts=UTC_NOW, text="done")
    assert event.type == "message_complete"


def test_tool_call_carries_args() -> None:
    event = ToolCall(
        ts=UTC_NOW,
        tool_id="t-1",
        name="bash",
        arguments={"command": "ls"},
    )
    assert event.type == "tool_call"
    assert event.arguments == {"command": "ls"}


def test_tool_result_defaults_is_error_false() -> None:
    event = ToolResult(ts=UTC_NOW, tool_id="t-1", content="output")
    assert event.is_error is False


def test_tool_result_can_signal_error() -> None:
    event = ToolResult(ts=UTC_NOW, tool_id="t-1", content="fail", is_error=True)
    assert event.is_error is True


def test_status_change_constrained_to_known_states() -> None:
    StatusChange(ts=UTC_NOW, status="live")
    StatusChange(ts=UTC_NOW, status="thinking")
    StatusChange(ts=UTC_NOW, status="idle")


def test_artifact_marker_payload() -> None:
    payload = {"type": "pr", "url": "https://github.com/owner/repo/pull/1"}
    event = ArtifactMarker(ts=UTC_NOW, payload=payload)
    assert event.type == "artifact_marker"
    assert event.payload == payload


def test_error_event() -> None:
    event = Error(ts=UTC_NOW, message="boom")
    assert event.type == "error"
    assert event.message == "boom"


def test_provider_context_compacted_event() -> None:
    event = ProviderContextCompacted(ts=UTC_NOW, provider="codex")
    assert event.type == "provider_context_compacted"
    assert event.provider == "codex"
    assert event.reason == "auto"


def test_events_are_frozen() -> None:
    from dataclasses import FrozenInstanceError

    event = MessageDelta(ts=UTC_NOW, text="hi")
    with pytest.raises(FrozenInstanceError):
        event.text = "no"  # type: ignore[misc]


def test_tool_call_acp_enrichment_defaults_to_none() -> None:
    event = ToolCall(ts=UTC_NOW, tool_id="t-1", name="Bash", arguments={})
    assert event.kind is None
    assert event.title is None
    assert event.locations is None


def test_tool_call_carries_acp_enrichment() -> None:
    event = ToolCall(
        ts=UTC_NOW,
        tool_id="t-1",
        name="Write",
        arguments={"path": "a.txt", "content": "hi"},
        kind="edit",
        title="Write a.txt",
        locations=({"path": "a.txt"},),
    )
    assert event.kind == "edit"
    assert event.locations == ({"path": "a.txt"},)


def test_tool_call_update_event() -> None:
    event = ToolCallUpdate(ts=UTC_NOW, tool_id="t-1", status="in_progress")
    assert event.type == "tool_call_update"
    assert event.status == "in_progress"
    assert event.title is None
    assert event.kind is None
    assert event.locations is None


def test_tool_result_structured_diff_defaults_none() -> None:
    event = ToolResult(ts=UTC_NOW, tool_id="t-1", content="ok")
    assert event.diff is None
    rich = ToolResult(
        ts=UTC_NOW,
        tool_id="t-1",
        content="ok",
        diff={"path": "a.txt", "old_text": None, "new_text": "hi"},
    )
    assert rich.diff is not None and rich.diff["new_text"] == "hi"


def test_plan_update_event() -> None:
    event = PlanUpdate(
        ts=UTC_NOW,
        entries=(
            {"content": "read code", "priority": "high", "status": "completed"},
            {"content": "write fix", "priority": "medium", "status": "pending"},
        ),
    )
    assert event.type == "plan_update"
    assert len(event.entries) == 2


def test_mode_change_event() -> None:
    event = ModeChange(ts=UTC_NOW, mode_id="plan")
    assert event.type == "mode_change"
    assert event.mode_id == "plan"


def test_permission_request_acp_options_default_none() -> None:
    event = PermissionRequest(
        ts=UTC_NOW, request_id="r1", tool_name="Bash", tool_input={}
    )
    assert event.options is None
    assert event.tool_id is None
    rich = PermissionRequest(
        ts=UTC_NOW,
        request_id="r1",
        tool_name="Write",
        tool_input={},
        options=({"option_id": "allow", "name": "Allow", "kind": "allow_once"},),
        tool_id="t-1",
    )
    assert rich.options is not None and rich.options[0]["kind"] == "allow_once"


def test_turn_metrics_cost_usd_defaults_none() -> None:
    event = TurnMetrics(ts=UTC_NOW, duration_ms=100)
    assert event.cost_usd is None
    priced = TurnMetrics(ts=UTC_NOW, duration_ms=100, cost_usd=0.39)
    assert priced.cost_usd == 0.39


def test_match_dispatch_over_union() -> None:
    """Demonstrates the consumer pattern: pattern-match by type."""
    events: list[AgentEvent] = [
        MessageDelta(ts=UTC_NOW, text="a"),
        ToolCall(ts=UTC_NOW, tool_id="t1", name="x", arguments={}),
        Error(ts=UTC_NOW, message="!"),
    ]
    seen: list[str] = []
    for ev in events:
        match ev:
            case MessageDelta():
                seen.append("delta")
            case ToolCall():
                seen.append("call")
            case Error():
                seen.append("err")
            case _:
                seen.append("other")
    assert seen == ["delta", "call", "err"]
