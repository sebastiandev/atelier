"""Unit tests for the ACP session-update → AgentEvent mapper.

Frame shapes mirror the captured claude-agent-acp spike payloads in
``tests/fixtures/acp/claude_agent_acp_spike.json``.
"""

from acp.schema import (
    AgentMessageChunk,
    AgentPlanUpdate,
    AgentThoughtChunk,
    AvailableCommandsUpdate,
    CurrentModeUpdate,
    FileEditToolCallContent,
    PlanEntry,
    TextContentBlock,
    ToolCallLocation,
    ToolCallProgress,
    ToolCallStart,
    UsageUpdate,
)

from src.domain.agents import (
    ArtifactMarker,
    MessageComplete,
    MessageDelta,
    ModeChange,
    PlanUpdate,
    ProviderContextCompacted,
    ThinkingComplete,
    ThinkingDelta,
    ToolCall,
    ToolCallUpdate,
    ToolResult,
)
from src.infrastructure.agents.acp import AcpUpdateMapper


def _text(text: str) -> TextContentBlock:
    return TextContentBlock(type="text", text=text)


def _msg(text: str, mid: str = "m1") -> AgentMessageChunk:
    return AgentMessageChunk(
        session_update="agent_message_chunk", content=_text(text), message_id=mid
    )


def test_message_chunks_stream_deltas_and_flush_complete() -> None:
    mapper = AcpUpdateMapper()
    events = mapper.handle(_msg("hel")) + mapper.handle(_msg("lo"))
    assert [type(e) for e in events] == [MessageDelta, MessageDelta]
    flushed = mapper.flush_turn()
    assert [type(e) for e in flushed] == [MessageComplete]
    assert flushed[0].text == "hello"


def test_message_id_boundary_flushes_previous_message() -> None:
    mapper = AcpUpdateMapper()
    mapper.handle(_msg("first", mid="m1"))
    events = mapper.handle(_msg("second", mid="m2"))
    completes = [e for e in events if isinstance(e, MessageComplete)]
    assert len(completes) == 1 and completes[0].text == "first"
    assert mapper.flush_turn()[0].text == "second"


def test_thought_chunks_map_to_thinking_events() -> None:
    mapper = AcpUpdateMapper()
    update = AgentThoughtChunk(
        session_update="agent_thought_chunk", content=_text("hmm"), message_id="t1"
    )
    events = mapper.handle(update)
    assert [type(e) for e in events] == [ThinkingDelta]
    flushed = mapper.flush_turn()
    assert [type(e) for e in flushed] == [ThinkingComplete]
    assert flushed[0].text == "hmm"


def _thought(text: str, mid: str = "t1") -> AgentThoughtChunk:
    return AgentThoughtChunk(
        session_update="agent_thought_chunk", content=_text(text), message_id=mid
    )


def test_thinking_interleave_does_not_duplicate_message_text() -> None:
    """Regression (claude-acp + haiku): text → thinking → text with the
    SAME message_id must split into two complete messages at the
    thinking boundary — the frontend closes the assistant bubble on a
    thinking chunk, so a turn-end MessageComplete carrying the full
    joined text would render the first segment twice."""
    mapper = AcpUpdateMapper()
    mapper.handle(_msg("part one. ", mid="m1"))
    events = mapper.handle(_thought("let me reconsider"))
    completes = [e for e in events if isinstance(e, MessageComplete)]
    assert [c.text for c in completes] == ["part one. "]
    mapper.handle(_msg("part two.", mid="m1"))
    flushed = mapper.flush_turn()
    final = [e for e in flushed if isinstance(e, MessageComplete)]
    assert [f.text for f in final] == ["part two."]
    # The joined "part one. part two." must never appear anywhere.


def test_message_chunk_flushes_open_thought() -> None:
    mapper = AcpUpdateMapper()
    mapper.handle(_thought("pondering"))
    events = mapper.handle(_msg("answer"))
    thoughts = [e for e in events if isinstance(e, ThinkingComplete)]
    assert [t.text for t in thoughts] == ["pondering"]
    assert mapper.flush_turn()[0].text == "answer"


def test_message_artifact_marker_fallback_scan() -> None:
    mapper = AcpUpdateMapper()
    marker = '{"atelier_artifact": {"type": "pr", "url": "https://x/pull/1"}}'
    mapper.handle(_msg(marker))
    flushed = mapper.flush_turn()
    assert any(isinstance(e, ArtifactMarker) for e in flushed)


def test_context_compacted_message_emits_provider_compaction_marker() -> None:
    mapper = AcpUpdateMapper()
    mapper.handle(_msg("Context compacted\nContinuing from saved state."))
    flushed = mapper.flush_turn()

    assert any(isinstance(e, MessageComplete) for e in flushed)
    marker = next(e for e in flushed if isinstance(e, ProviderContextCompacted))
    assert marker.provider == "acp"


def _tool_start_empty() -> ToolCallStart:
    # The Claude wrapper opens with an empty raw_input (spike fixture).
    return ToolCallStart(
        session_update="tool_call",
        tool_call_id="t1",
        title="Write",
        kind="edit",
        status="pending",
        raw_input={},
        content=[],
        locations=[],
        field_meta={"claudeCode": {"toolName": "Write"}},
    )


def _tool_args_update() -> ToolCallProgress:
    return ToolCallProgress(
        session_update="tool_call_update",
        tool_call_id="t1",
        title="Write /ws/spike.txt",
        raw_input={"file_path": "/ws/spike.txt", "content": "ok"},
        content=[
            FileEditToolCallContent(
                type="diff", path="/ws/spike.txt", old_text=None, new_text="ok"
            )
        ],
        locations=[ToolCallLocation(path="/ws/spike.txt")],
        field_meta={"claudeCode": {"toolName": "Write"}},
    )


def test_tool_call_emission_deferred_until_args_arrive() -> None:
    mapper = AcpUpdateMapper()
    assert mapper.handle(_tool_start_empty()) == []
    events = mapper.handle(_tool_args_update())
    calls = [e for e in events if isinstance(e, ToolCall)]
    assert len(calls) == 1
    call = calls[0]
    assert call.name == "Write"
    assert call.arguments == {"path": "/ws/spike.txt", "content": "ok"}
    assert call.kind == "edit"
    assert call.title == "Write /ws/spike.txt"
    assert call.locations == ({"path": "/ws/spike.txt"},)


def test_tool_terminal_status_emits_result_with_diff() -> None:
    mapper = AcpUpdateMapper()
    mapper.handle(_tool_start_empty())
    mapper.handle(_tool_args_update())
    events = mapper.handle(
        ToolCallProgress(
            session_update="tool_call_update",
            tool_call_id="t1",
            status="completed",
            raw_output="File created successfully",
        )
    )
    results = [e for e in events if isinstance(e, ToolResult)]
    assert len(results) == 1
    assert results[0].content == "File created successfully"
    assert results[0].is_error is False
    assert results[0].diff == {
        "path": "/ws/spike.txt",
        "old_text": None,
        "new_text": "ok",
    }
    # Turn flush must not double-emit the result.
    assert not any(isinstance(e, ToolResult) for e in mapper.flush_turn())


def test_tool_failed_status_marks_result_error() -> None:
    mapper = AcpUpdateMapper()
    mapper.handle(
        ToolCallStart(
            session_update="tool_call",
            tool_call_id="t2",
            title="Bash",
            kind="execute",
            raw_input={"command": "false"},
        )
    )
    events = mapper.handle(
        ToolCallProgress(
            session_update="tool_call_update",
            tool_call_id="t2",
            status="failed",
            raw_output="exit 1",
        )
    )
    results = [e for e in events if isinstance(e, ToolResult)]
    assert results[0].is_error is True


def test_tool_call_with_args_emits_immediately() -> None:
    mapper = AcpUpdateMapper()
    events = mapper.handle(
        ToolCallStart(
            session_update="tool_call",
            tool_call_id="t3",
            title="Read file",
            kind="read",
            raw_input={"file_path": "/ws/a.txt"},
        )
    )
    assert any(isinstance(e, ToolCall) for e in events)


def test_status_only_update_emits_coalesced_tool_call_update() -> None:
    mapper = AcpUpdateMapper()
    mapper.handle(
        ToolCallStart(
            session_update="tool_call",
            tool_call_id="t4",
            title="Bash",
            kind="execute",
            raw_input={"command": "sleep 1"},
            status="pending",
        )
    )
    events = mapper.handle(
        ToolCallProgress(
            session_update="tool_call_update", tool_call_id="t4", status="in_progress"
        )
    )
    assert [type(e) for e in events] == [ToolCallUpdate]
    assert events[0].status == "in_progress"
    assert events[0].title is None  # unchanged fields stay unset
    # A frame that changes nothing emits nothing.
    assert (
        mapper.handle(
            ToolCallProgress(
                session_update="tool_call_update",
                tool_call_id="t4",
                status="in_progress",
            )
        )
        == []
    )


def test_artifact_mcp_tool_call_emits_marker() -> None:
    mapper = AcpUpdateMapper()
    events = mapper.handle(
        ToolCallStart(
            session_update="tool_call",
            tool_call_id="t5",
            title="record_pr",
            kind="other",
            raw_input={"url": "https://x/pull/2", "title": "Fix", "status": "open"},
            field_meta={"claudeCode": {"toolName": "mcp__atelier__record_pr"}},
        )
    )
    markers = [e for e in events if isinstance(e, ArtifactMarker)]
    assert len(markers) == 1
    assert markers[0].payload["type"] == "pr"
    assert markers[0].payload["url"] == "https://x/pull/2"


def test_artifact_mcp_tool_call_emits_marker_for_acp_wrapped_shape() -> None:
    mapper = AcpUpdateMapper()
    events = mapper.handle(
        ToolCallStart(
            session_update="tool_call",
            tool_call_id="t6",
            title="Tool: atelier/record_pr",
            kind="other",
            raw_input={
                "server": "atelier",
                "tool": "record_pr",
                "arguments": {
                    "url": "https://x/pull/3",
                    "title": "Fix",
                    "status": "open",
                    "repo": "x/y",
                },
            },
        )
    )
    markers = [e for e in events if isinstance(e, ArtifactMarker)]
    assert len(markers) == 1
    assert markers[0].payload == {
        "type": "pr",
        "url": "https://x/pull/3",
        "title": "Fix",
        "status": "open",
        "repo": "x/y",
    }


def test_artifact_marker_recovers_from_generic_tool_output() -> None:
    mapper = AcpUpdateMapper()
    assert (
        mapper.handle(
            ToolCallStart(
                session_update="tool_call",
                tool_call_id="t7",
                title="tool",
                kind="other",
                raw_input={},
            )
        )
        == []
    )
    events = mapper.handle(
        ToolCallProgress(
            session_update="tool_call_update",
            tool_call_id="t7",
            status="completed",
            raw_output=(
                "Wall time: 0.0109 seconds\n"
                'Output:\n[{"type":"text","text":"Artifact will be recorded by Atelier.\\n'
                '{\\"atelier_artifact\\":{\\"type\\":\\"pr\\",'
                '\\"url\\":\\"https://x/pull/4\\",\\"title\\":\\"Fix\\",'
                '\\"status\\":\\"draft\\"}}"}]'
            ),
        )
    )
    assert [type(e) for e in events] == [ToolCall, ArtifactMarker, ToolResult]
    marker = next(e for e in events if isinstance(e, ArtifactMarker))
    assert marker.payload == {
        "type": "pr",
        "url": "https://x/pull/4",
        "title": "Fix",
        "status": "draft",
    }


def test_plan_maps_to_plan_update() -> None:
    mapper = AcpUpdateMapper()
    events = mapper.handle(
        AgentPlanUpdate(
            session_update="plan",
            entries=[
                PlanEntry(content="read", priority="high", status="completed"),
                PlanEntry(content="fix", priority="medium", status="pending"),
            ],
        )
    )
    assert [type(e) for e in events] == [PlanUpdate]
    assert events[0].entries == (
        {"content": "read", "priority": "high", "status": "completed"},
        {"content": "fix", "priority": "medium", "status": "pending"},
    )


def test_mode_change_dedupes() -> None:
    mapper = AcpUpdateMapper()
    first = mapper.handle(
        CurrentModeUpdate(session_update="current_mode_update", current_mode_id="plan")
    )
    assert [type(e) for e in first] == [ModeChange]
    assert (
        mapper.handle(
            CurrentModeUpdate(
                session_update="current_mode_update", current_mode_id="plan"
            )
        )
        == []
    )


def test_usage_updates_fold_into_state() -> None:
    mapper = AcpUpdateMapper()
    assert mapper.handle(
        UsageUpdate(session_update="usage_update", used=21873, size=1_000_000)
    ) == []
    mapper.handle(
        UsageUpdate(
            session_update="usage_update",
            used=24193,
            size=1_000_000,
            cost={"amount": 0.39, "currency": "USD"},
        )
    )
    assert mapper.usage.used == 24193
    assert mapper.usage.size == 1_000_000
    assert mapper.usage.cost_usd == 0.39


def test_tool_call_flushes_open_message_buffer() -> None:
    """codex-acp streams chunks without message ids; a tool call must
    close the open bubble or pre/post-tool text concatenates."""
    mapper = AcpUpdateMapper()
    mapper.handle(
        AgentMessageChunk(
            session_update="agent_message_chunk",
            content=_text("Creating it now."),
            message_id=None,
        )
    )
    events = mapper.handle(
        ToolCallStart(
            session_update="tool_call",
            tool_call_id="t9",
            title="Edit /ws/x.txt",
            kind="edit",
            raw_input={"path": "/ws/x.txt"},
        )
    )
    completes = [e for e in events if isinstance(e, MessageComplete)]
    assert [c.text for c in completes] == ["Creating it now."]
    mapper.handle(
        AgentMessageChunk(
            session_update="agent_message_chunk",
            content=_text("Created."),
            message_id=None,
        )
    )
    assert mapper.flush_turn()[0].text == "Created."


def test_unknown_updates_are_ignored() -> None:
    mapper = AcpUpdateMapper()
    update = AvailableCommandsUpdate(
        session_update="available_commands_update", available_commands=[]
    )
    assert mapper.handle(update) == []
