"""Tests for Planning materialization provider options."""

from src.domain.planning.materialization import materialization_options


def test_materializer_auto_accepts_claude_file_edits() -> None:
    assert materialization_options("claude-code", {})["permission_mode"] == "acceptEdits"
    assert materialization_options("claude-acp", {})["permission_mode"] == "acceptEdits"
