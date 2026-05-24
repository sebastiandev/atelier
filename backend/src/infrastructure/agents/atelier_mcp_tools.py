"""Shared definitions for the Atelier MCP tools.

Three artifact-recording tools are surfaced to every agent — ``record_pr``,
``record_jira`` and ``record_doc`` — under the MCP server name ``atelier``.
Calling one is the agent's way of saying "I created this artifact"; the
adapter detects the tool use, emits an ``ArtifactMarker`` event, and the
supervisor's tracker runs the validation + persist step.

This module owns the per-type JSON Schemas and the name → payload helper
that's reused by both adapters (Claude in-process MCP and Amp subprocess
MCP). Status enums match ``domain/agents/artifacts.py``.
"""

from __future__ import annotations

import json
import re
from typing import Any

# Server name (mcp_servers key for Claude / mcpConfig key for Amp).
MCP_SERVER_NAME = "atelier"

# Bare tool names registered with the MCP server.
TOOL_RECORD_PR = "record_pr"
TOOL_RECORD_JIRA = "record_jira"
TOOL_RECORD_DOC = "record_doc"

# Most clients surface MCP tools to the model as ``mcp__<server>__<tool>``.
# Both adapters land them at the ToolUseBlock layer; the detector below
# accepts the prefixed and bare forms so we're robust to either client.
_PREFIXES = ("mcp__atelier__", "")


_PR_INPUT_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "url": {
            "type": "string",
            "description": "Full URL of the pull request.",
        },
        "title": {
            "type": "string",
            "description": "Concise human-readable title for the rail.",
        },
        "status": {
            "type": "string",
            "enum": ["open", "draft", "merged", "closed"],
            "description": "Current PR state. Defaults to 'open' if omitted.",
        },
        "repo": {
            "type": "string",
            "description": "Optional 'owner/name' shorthand for grouping.",
        },
    },
    "required": ["url", "title"],
}

_JIRA_INPUT_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "url": {"type": "string"},
        "title": {"type": "string"},
        "status": {
            "type": "string",
            "enum": ["todo", "in_progress", "in_review", "done", "blocked"],
        },
    },
    "required": ["url", "title", "status"],
}

_DOC_INPUT_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "path": {
            "type": "string",
            "description": (
                "Path to the document, relative to your working directory. "
                "Atelier validates the file exists before recording."
            ),
        },
        "title": {"type": "string"},
        "status": {
            "type": "string",
            "enum": ["draft"],
            "description": (
                "Always 'draft' — Atelier derives 'pending' / 'committed' "
                "for worktree-resident docs from git state. Safe to omit; "
                "defaults to 'draft' when missing."
            ),
        },
    },
    "required": ["path", "title"],
}


_PR_DESCRIPTION = (
    "Record a pull request artifact for the current Atelier work. "
    "Call after you've created or updated a PR (e.g. via `gh pr create`)."
)

_JIRA_DESCRIPTION = (
    "Record a Jira ticket artifact for the current Atelier work. "
    "Call after you've created or referenced a Jira issue."
)

_DOC_DESCRIPTION = (
    "Record a document artifact (e.g. design doc, ADR) you authored in "
    "this work's directory. The file MUST already exist on disk — call "
    "Write/Edit to create it, then call this tool with the path. "
    "Atelier validates the file is present before recording."
)


TOOL_SCHEMAS: dict[str, dict[str, Any]] = {
    TOOL_RECORD_PR: _PR_INPUT_SCHEMA,
    TOOL_RECORD_JIRA: _JIRA_INPUT_SCHEMA,
    TOOL_RECORD_DOC: _DOC_INPUT_SCHEMA,
}

TOOL_DESCRIPTIONS: dict[str, str] = {
    TOOL_RECORD_PR: _PR_DESCRIPTION,
    TOOL_RECORD_JIRA: _JIRA_DESCRIPTION,
    TOOL_RECORD_DOC: _DOC_DESCRIPTION,
}


_TOOL_TO_TYPE: dict[str, str] = {
    TOOL_RECORD_PR: "pr",
    TOOL_RECORD_JIRA: "jira",
    TOOL_RECORD_DOC: "doc",
}


def marker_payload_for_tool(
    tool_name: str, arguments: dict[str, Any]
) -> dict[str, Any] | None:
    """Return the ``atelier_artifact`` payload for an MCP-tool invocation,
    or ``None`` if the tool isn't one of ours.

    The adapter calls this on every observed ToolUseBlock — a return of
    ``None`` means "fall through to the regular ToolCall event".
    """
    bare = _strip_known_prefix(tool_name)
    artifact_type = _TOOL_TO_TYPE.get(bare)
    if artifact_type is None:
        return None
    payload: dict[str, Any] = {"type": artifact_type, **dict(arguments)}
    return payload


def _strip_known_prefix(name: str) -> str:
    for prefix in _PREFIXES:
        if name.startswith(prefix):
            return name[len(prefix) :]
    return name


# Match any line whose stripped content starts with ``{"atelier_artifact"``.
# Captures the JSON object (greedy to end of line) so we can json.loads it.
# Multi-line / pretty-printed JSON isn't supported on purpose: the contract
# in the system prompt is "a single JSON line", and trying to match arbitrary
# brace-balanced JSON would interact badly with normal prose. If we ever
# need pretty-printed support, restrict the search to fenced ``json`` blocks.
_MARKER_LINE_RE = re.compile(
    r'^\s*(\{\s*"atelier_artifact"\s*:.*\})\s*$', re.MULTILINE
)


def scan_text_for_artifact_markers(text: str) -> list[dict[str, Any]]:
    """Extract ``atelier_artifact`` payloads from raw assistant text.

    Acts as a belt-and-suspenders fallback when the MCP tool path
    doesn't deliver — e.g. Amp's GPT-backed modes drop some tool calls
    during normalization, so the model's only escape hatch is to emit
    the marker as a JSON line in chat (per the system prompt
    instructions). Returns a list of normalised payloads compatible
    with ``record_artifact``'s ``payload`` argument.

    Defensive: malformed JSON is ignored silently, missing ``type`` is
    ignored, and unknown types fall through to the tracker which will
    raise ``InvalidMarker`` with a clear message.
    """
    found: list[dict[str, Any]] = []
    for match in _MARKER_LINE_RE.finditer(text):
        try:
            parsed = json.loads(match.group(1))
        except json.JSONDecodeError:
            continue
        if not isinstance(parsed, dict):
            continue
        body = parsed.get("atelier_artifact")
        if not isinstance(body, dict):
            continue
        # The system prompt teaches the agent to emit ``type`` inside
        # the body; require it before forwarding. ``record_artifact``
        # does its own per-type validation, so we don't second-guess
        # the shape here.
        if not isinstance(body.get("type"), str):
            continue
        found.append(dict(body))
    return found


__all__ = [
    "MCP_SERVER_NAME",
    "TOOL_DESCRIPTIONS",
    "TOOL_RECORD_DOC",
    "TOOL_RECORD_JIRA",
    "TOOL_RECORD_PR",
    "TOOL_SCHEMAS",
    "marker_payload_for_tool",
    "scan_text_for_artifact_markers",
]
