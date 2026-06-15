"""Serialization compat for AgentEvent → transcript/WS dicts.

The ACP story (STORY-033) added optional fields to existing variants and
new variants. Hard requirement: events emitted by pre-ACP adapters must
serialize to byte-identical dicts — new optional keys are omitted when
unset so legacy transcript lines round-trip unchanged through reconcile
and old frontend builds never see keys they don't know.
"""

from datetime import UTC, datetime

from src.domain.agents import (
    ModeChange,
    PermissionRequest,
    PlanUpdate,
    ToolCall,
    ToolCallUpdate,
    ToolResult,
    TurnMetrics,
)
from src.domain.supervisor.service import _event_to_dict

UTC_NOW = datetime(2026, 6, 11, 9, 0, tzinfo=UTC)
ISO_NOW = UTC_NOW.isoformat()


def test_legacy_tool_call_shape_is_unchanged() -> None:
    event = ToolCall(ts=UTC_NOW, tool_id="t1", name="Bash", arguments={"command": "ls"})
    assert _event_to_dict(event) == {
        "type": "tool_call",
        "ts": ISO_NOW,
        "tool_id": "t1",
        "name": "Bash",
        "arguments": {"command": "ls"},
    }


def test_enriched_tool_call_includes_acp_fields() -> None:
    event = ToolCall(
        ts=UTC_NOW,
        tool_id="t1",
        name="Write",
        arguments={"path": "a.txt", "content": "hi"},
        kind="edit",
        title="Write a.txt",
        locations=({"path": "a.txt", "line": 3},),
    )
    d = _event_to_dict(event)
    assert d["kind"] == "edit"
    assert d["title"] == "Write a.txt"
    assert d["locations"] == ({"path": "a.txt", "line": 3},)


def test_legacy_tool_result_shape_is_unchanged() -> None:
    event = ToolResult(ts=UTC_NOW, tool_id="t1", content="done")
    assert _event_to_dict(event) == {
        "type": "tool_result",
        "ts": ISO_NOW,
        "tool_id": "t1",
        "content": "done",
        "is_error": False,
    }


def test_tool_result_diff_present_when_set() -> None:
    event = ToolResult(
        ts=UTC_NOW,
        tool_id="t1",
        content="done",
        diff={"path": "a.txt", "old_text": None, "new_text": "hi"},
    )
    assert _event_to_dict(event)["diff"] == {
        "path": "a.txt",
        "old_text": None,
        "new_text": "hi",
    }


def test_legacy_permission_request_shape_is_unchanged() -> None:
    event = PermissionRequest(
        ts=UTC_NOW, request_id="r1", tool_name="Bash", tool_input={"command": "rm"}
    )
    assert _event_to_dict(event) == {
        "type": "permission_request",
        "ts": ISO_NOW,
        "request_id": "r1",
        "tool_name": "Bash",
        "tool_input": {"command": "rm"},
    }


def test_permission_request_options_present_when_set() -> None:
    event = PermissionRequest(
        ts=UTC_NOW,
        request_id="r1",
        tool_name="Write",
        tool_input={},
        options=({"option_id": "allow", "name": "Allow", "kind": "allow_once"},),
        tool_id="t1",
    )
    d = _event_to_dict(event)
    assert d["tool_id"] == "t1"
    assert d["options"][0]["option_id"] == "allow"


def test_legacy_turn_metrics_shape_is_unchanged() -> None:
    event = TurnMetrics(ts=UTC_NOW, duration_ms=1200, model="m")
    d = _event_to_dict(event)
    assert "cost_usd" not in d
    # Pre-existing omissions still hold.
    assert "context_window" not in d
    assert "git_branch" not in d


def test_turn_metrics_cost_present_when_set() -> None:
    event = TurnMetrics(ts=UTC_NOW, duration_ms=1200, cost_usd=0.39)
    assert _event_to_dict(event)["cost_usd"] == 0.39


def test_new_variants_serialize_with_type_tags() -> None:
    plan = PlanUpdate(
        ts=UTC_NOW,
        entries=({"content": "x", "priority": "high", "status": "pending"},),
    )
    update = ToolCallUpdate(ts=UTC_NOW, tool_id="t1", status="in_progress")
    mode = ModeChange(ts=UTC_NOW, mode_id="plan")
    assert _event_to_dict(plan)["type"] == "plan_update"
    assert _event_to_dict(update)["type"] == "tool_call_update"
    assert _event_to_dict(mode)["type"] == "mode_change"


def test_tool_call_update_omits_unset_fields() -> None:
    update = ToolCallUpdate(ts=UTC_NOW, tool_id="t1", status="completed")
    d = _event_to_dict(update)
    assert d == {
        "type": "tool_call_update",
        "ts": ISO_NOW,
        "tool_id": "t1",
        "status": "completed",
    }
