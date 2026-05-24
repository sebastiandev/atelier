"""Tests for the shared Atelier MCP tool helpers."""

from __future__ import annotations

from src.infrastructure.agents.atelier_mcp_tools import (
    TOOL_RECORD_DOC,
    TOOL_RECORD_JIRA,
    TOOL_RECORD_PR,
    TOOL_SCHEMAS,
    marker_payload_for_tool,
    scan_text_for_artifact_markers,
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
    # ``pending`` and ``committed`` are derived by Atelier from git state
    # (see ``domain/artifacts/status.py``) — agents only ever set ``draft``.
    assert doc_status["enum"] == ["draft"]


def test_required_fields_match_design() -> None:
    assert TOOL_SCHEMAS[TOOL_RECORD_PR]["required"] == ["url", "title"]
    assert TOOL_SCHEMAS[TOOL_RECORD_JIRA]["required"] == ["url", "title", "status"]
    assert TOOL_SCHEMAS[TOOL_RECORD_DOC]["required"] == ["path", "title"]


# --- scan_text_for_artifact_markers ----------------------------------------


def test_scan_text_extracts_doc_marker() -> None:
    text = (
        "I've drafted the design doc and saved it. "
        "Here's the marker for tracking:\n"
        '{"atelier_artifact": {"type": "doc", "path": "docs/design.md", '
        '"title": "API design", "status": "draft"}}\n'
        "Let me know if you want changes."
    )
    [payload] = scan_text_for_artifact_markers(text)
    assert payload == {
        "type": "doc",
        "path": "docs/design.md",
        "title": "API design",
        "status": "draft",
    }


def test_scan_text_extracts_multiple_markers() -> None:
    text = (
        '{"atelier_artifact": {"type": "pr", "url": "https://x/1", "title": "A"}}\n'
        "Some prose in between.\n"
        '{"atelier_artifact": {"type": "doc", "path": "n.md", "title": "B"}}'
    )
    payloads = scan_text_for_artifact_markers(text)
    assert [p["type"] for p in payloads] == ["pr", "doc"]


def test_scan_text_returns_empty_when_no_marker() -> None:
    assert scan_text_for_artifact_markers("just regular prose.") == []


def test_scan_text_ignores_malformed_json() -> None:
    """A line that looks like an atelier_artifact but isn't valid JSON
    is silently skipped — we'd rather miss the marker than crash the
    pump on a malformed payload."""
    assert (
        scan_text_for_artifact_markers(
            '{"atelier_artifact": {"type": "doc", "path": "x.md"'  # missing close
        )
        == []
    )


def test_scan_text_ignores_marker_without_type() -> None:
    text = '{"atelier_artifact": {"title": "no type", "path": "x.md"}}'
    assert scan_text_for_artifact_markers(text) == []


def test_scan_text_indented_line_still_matches() -> None:
    """Some models prefix the marker with a few spaces. The contract is
    'one line', not 'flush left'."""
    text = (
        "Plan: write the README.\n"
        '   {"atelier_artifact": {"type": "doc", "path": "README.md", "title": "T"}}'
    )
    [payload] = scan_text_for_artifact_markers(text)
    assert payload["path"] == "README.md"
