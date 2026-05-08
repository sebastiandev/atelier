"""Tests for the shared Atelier MCP tool helpers."""

from __future__ import annotations

from src.infrastructure.agents.atelier_mcp_tools import (
    TOOL_RECORD_DOC,
    TOOL_RECORD_JIRA,
    TOOL_RECORD_PR,
    TOOL_SCHEMAS,
    marker_payload_for_tool,
)


def test_marker_payload_for_pr_with_prefixed_name() -> None:
    payload = marker_payload_for_tool(
        "mcp__atelier__record_pr",
        {"url": "https://x/1", "title": "Add foo", "status": "open"},
    )
    assert payload == {
        "type": "pr",
        "url": "https://x/1",
        "title": "Add foo",
        "status": "open",
    }


def test_marker_payload_for_jira_with_bare_name() -> None:
    payload = marker_payload_for_tool(
        "record_jira",
        {"url": "https://j/X-1", "title": "Implement bar", "status": "in_progress"},
    )
    assert payload == {
        "type": "jira",
        "url": "https://j/X-1",
        "title": "Implement bar",
        "status": "in_progress",
    }


def test_marker_payload_for_doc() -> None:
    payload = marker_payload_for_tool(
        "mcp__atelier__record_doc",
        {"path": "docs/design.md", "title": "Design"},
    )
    assert payload == {
        "type": "doc",
        "path": "docs/design.md",
        "title": "Design",
    }


def test_unrelated_tool_returns_none() -> None:
    assert marker_payload_for_tool("Bash", {"command": "ls"}) is None
    assert marker_payload_for_tool("mcp__other__record_pr", {}) is None


def test_schemas_enforce_status_enum() -> None:
    pr_status = TOOL_SCHEMAS[TOOL_RECORD_PR]["properties"]["status"]
    assert pr_status["enum"] == ["open", "draft", "merged", "closed"]

    jira_status = TOOL_SCHEMAS[TOOL_RECORD_JIRA]["properties"]["status"]
    assert "in_progress" in jira_status["enum"]
    assert "merged" not in jira_status["enum"]

    doc_status = TOOL_SCHEMAS[TOOL_RECORD_DOC]["properties"]["status"]
    assert doc_status["enum"] == ["draft", "published"]


def test_required_fields_match_design() -> None:
    assert TOOL_SCHEMAS[TOOL_RECORD_PR]["required"] == ["url", "title"]
    assert TOOL_SCHEMAS[TOOL_RECORD_JIRA]["required"] == ["url", "title", "status"]
    assert TOOL_SCHEMAS[TOOL_RECORD_DOC]["required"] == ["path", "title"]
