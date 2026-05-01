"""Integration tests for FsTranscriptLog against a real tmp workspace."""

from __future__ import annotations

from pathlib import Path

import pytest

from src.infrastructure.filesystem import FsTranscriptLog, WorkspacePaths


@pytest.fixture
def log(tmp_path: Path) -> FsTranscriptLog:
    return FsTranscriptLog(WorkspacePaths(workspace_root=tmp_path / "Atelier"))


def test_append_then_read_round_trip(log: FsTranscriptLog) -> None:
    log.append("WRK-001", "agt-1", {"seq": 1, "type": "user", "text": "hi"})
    log.append("WRK-001", "agt-1", {"seq": 2, "type": "assistant", "text": "hello"})
    events = list(log.read_from_cursor("WRK-001", "agt-1", 0))
    assert [e["seq"] for e in events] == [1, 2]


def test_read_from_cursor_filters(log: FsTranscriptLog) -> None:
    for i in range(1, 6):
        log.append("WRK-001", "agt-1", {"seq": i})
    events = list(log.read_from_cursor("WRK-001", "agt-1", 3))
    assert [e["seq"] for e in events] == [4, 5]


def test_missing_log_file_yields_empty(log: FsTranscriptLog) -> None:
    assert list(log.read_from_cursor("WRK-001", "agt-404", 0)) == []


def test_invalid_slugs_rejected(log: FsTranscriptLog) -> None:
    with pytest.raises(ValueError):
        log.append("../escape", "agt-1", {"seq": 1})
    with pytest.raises(ValueError):
        log.append("WRK-001", "..", {"seq": 1})
