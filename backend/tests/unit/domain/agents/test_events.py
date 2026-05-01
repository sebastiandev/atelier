"""Unit tests for the AgentEvent tagged union."""

from datetime import UTC, datetime

import pytest

from src.domain.agents import (
    AgentEvent,
    ArtifactMarker,
    Error,
    MessageComplete,
    MessageDelta,
    StatusChange,
    ToolCall,
    ToolResult,
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


def test_events_are_frozen() -> None:
    from dataclasses import FrozenInstanceError

    event = MessageDelta(ts=UTC_NOW, text="hi")
    with pytest.raises(FrozenInstanceError):
        event.text = "no"  # type: ignore[misc]


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
