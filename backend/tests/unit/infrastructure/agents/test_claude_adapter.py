"""Unit tests for the Claude adapter's message-conversion logic.

Live SDK integration is verified by manual smoke test (requires Claude
Code CLI + ANTHROPIC_API_KEY). These tests exercise ``_convert`` against
synthetic SDK message objects to lock in the Claude → AgentEvent mapping.
"""

from claude_agent_sdk import (
    AssistantMessage,
    ResultMessage,
    TextBlock,
    ThinkingBlock,
    ToolResultBlock,
    ToolUseBlock,
)

from src.domain.agents import (
    Error,
    MessageComplete,
    StatusChange,
    ThinkingComplete,
    ToolCall,
    ToolResult,
)
from src.infrastructure.agents.claude_code_adapter import _convert


def _assistant(*blocks: object) -> AssistantMessage:
    return AssistantMessage(
        content=list(blocks),
        model="claude-opus-4-7",
        parent_tool_use_id=None,
        error=None,
        usage=None,
        message_id="m-1",
        stop_reason=None,
        session_id="s-1",
        uuid="u-1",
    )


def _result(*, is_error: bool = False, errors: list[str] | None = None) -> ResultMessage:
    return ResultMessage(
        subtype="success" if not is_error else "error_during_execution",
        duration_ms=10,
        duration_api_ms=5,
        is_error=is_error,
        num_turns=1,
        session_id="s-1",
        stop_reason="end_turn",
        total_cost_usd=0.01,
        usage=None,
        result=None,
        structured_output=None,
        model_usage=None,
        permission_denials=None,
        errors=errors,
        uuid="u-2",
    )


def test_text_block_maps_to_message_complete() -> None:
    [event] = list(_convert(_assistant(TextBlock(text="hello"))))
    assert isinstance(event, MessageComplete)
    assert event.text == "hello"


def test_thinking_block_maps_to_thinking_complete() -> None:
    [event] = list(_convert(_assistant(ThinkingBlock(thinking="reason", signature="sig"))))
    assert isinstance(event, ThinkingComplete)
    assert event.text == "reason"


def test_tool_use_block_maps_to_tool_call() -> None:
    [event] = list(
        _convert(_assistant(ToolUseBlock(id="t-1", name="bash", input={"cmd": "ls"})))
    )
    assert isinstance(event, ToolCall)
    assert event.tool_id == "t-1"
    assert event.name == "bash"
    assert event.arguments == {"cmd": "ls"}


def test_tool_result_block_string_content() -> None:
    [event] = list(
        _convert(
            _assistant(ToolResultBlock(tool_use_id="t-1", content="ok", is_error=False))
        )
    )
    assert isinstance(event, ToolResult)
    assert event.tool_id == "t-1"
    assert event.content == "ok"
    assert event.is_error is False


def test_tool_result_block_structured_content_serialised() -> None:
    [event] = list(
        _convert(
            _assistant(
                ToolResultBlock(
                    tool_use_id="t-2",
                    content=[{"type": "text", "text": "out"}],
                    is_error=True,
                )
            )
        )
    )
    assert isinstance(event, ToolResult)
    assert event.is_error is True
    # Structured content is JSON-serialized into the content string.
    assert "out" in event.content


def test_multi_block_message_yields_in_order() -> None:
    events = list(
        _convert(
            _assistant(
                ThinkingBlock(thinking="hmm", signature=""),
                TextBlock(text="answer"),
            )
        )
    )
    assert isinstance(events[0], ThinkingComplete)
    assert isinstance(events[1], MessageComplete)


def test_result_message_success_yields_idle_status() -> None:
    [event] = list(_convert(_result()))
    assert isinstance(event, StatusChange)
    assert event.status == "idle"


def test_result_message_error_yields_error_then_idle() -> None:
    events = list(_convert(_result(is_error=True, errors=["boom"])))
    assert isinstance(events[0], Error)
    assert "boom" in events[0].message
    assert isinstance(events[1], StatusChange)
    assert events[1].status == "idle"
