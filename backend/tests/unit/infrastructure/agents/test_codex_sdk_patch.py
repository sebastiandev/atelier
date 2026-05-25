"""Tests for the ``_codex_sdk_patch`` shim.

The patch keeps Codex ``file_change`` stream frames parseable when the
CLI reports an intermediate ``in_progress`` status that the current Python
SDK's strict ``FileChangeItem`` model does not accept.
"""

from __future__ import annotations

import importlib

import openai_codex_sdk.parsing as _codex_parsing
import pytest
from pydantic import ValidationError

from src.infrastructure.agents import _codex_sdk_patch


def _reload_parsing() -> None:
    importlib.reload(_codex_parsing)


def test_upstream_parser_rejects_in_progress_file_change_without_patch() -> None:
    _reload_parsing()
    with pytest.raises(ValidationError, match="completed' or 'failed"):
        _codex_parsing.parse_thread_event(
            {
                "type": "item.started",
                "item": {
                    "id": "fc-1",
                    "type": "file_change",
                    "status": "in_progress",
                    "changes": [{"path": "artifact.md", "kind": "add"}],
                },
            }
        )


def test_install_allows_in_progress_file_change_items() -> None:
    _reload_parsing()
    _codex_sdk_patch.install()

    event = _codex_parsing.parse_thread_event(
        {
            "type": "item.started",
            "item": {
                "id": "fc-1",
                "type": "file_change",
                "status": "in_progress",
                "changes": [{"path": "artifact.md", "kind": "add"}],
            },
        }
    )

    assert event.type == "item.started"
    assert event.item.type == "file_change"
    assert event.item.status == "in_progress"
    assert event.item.changes == [{"path": "artifact.md", "kind": "add"}]

    before = _codex_parsing._ITEM_MODELS["file_change"]
    _codex_sdk_patch.install()
    assert _codex_parsing._ITEM_MODELS["file_change"] is before
