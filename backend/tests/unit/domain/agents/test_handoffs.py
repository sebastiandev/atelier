"""Tests for the handoff orchestrator + structural summarizer fallback."""

from __future__ import annotations

from collections.abc import Iterator
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import pytest

from src.domain.agents.handoffs import (
    BuildHandoffRequest,
    SummaryContext,
    build_handoff,
    structural_summarizer,
)
from src.domain.models import Agent, Handoff, Work
from src.domain.workstore.dtos import RecordHandoffRequest, WorkRecord


# ---------------------------------------------------------------------------
# Structural summarizer
# ---------------------------------------------------------------------------


def _ctx() -> SummaryContext:
    return SummaryContext(
        work_name="Migrate auth",
        work_description="Move session tokens to encrypted storage.",
        source_agent_name="Aria",
        source_agent_role="planner",
    )


def test_structural_summarizer_includes_required_sections() -> None:
    out = structural_summarizer([], _ctx())
    for header in (
        "## Goal",
        "## Decisions",
        "## Open questions",
        "## Key files",
        "## Blockers",
    ):
        assert header in out


def test_structural_summarizer_extracts_user_inputs_as_decisions() -> None:
    events = [
        {"type": "user_input", "text": "Use AES-256-GCM for the new format."},
        {"type": "user_input", "text": "Migrate live sessions in batches of 100."},
    ]
    out = structural_summarizer(events, _ctx())
    assert "AES-256-GCM" in out
    assert "batches of 100" in out


def test_structural_summarizer_lists_files_from_tool_calls() -> None:
    events = [
        {"type": "tool_call", "name": "Edit", "arguments": {"file_path": "src/auth.py"}},
        {"type": "tool_call", "name": "Write", "arguments": {"path": "docs/migration.md"}},
        {"type": "tool_call", "name": "Bash", "arguments": {"command": "ls"}},
    ]
    out = structural_summarizer(events, _ctx())
    assert "src/auth.py" in out
    assert "docs/migration.md" in out


def test_structural_summarizer_lists_errors_as_blockers() -> None:
    events = [
        {"type": "error", "message": "DB migration failed: lock timeout"},
        {
            "type": "permission_decision",
            "tool_name": "Bash",
            "decision": "deny",
        },
    ]
    out = structural_summarizer(events, _ctx())
    assert "lock timeout" in out
    assert "Bash" in out


# ---------------------------------------------------------------------------
# build_handoff orchestrator
# ---------------------------------------------------------------------------


class _Workstore:
    def __init__(self) -> None:
        self.recorded: list[RecordHandoffRequest] = []
        self._work = Work(
            id=1,
            slug="WRK-001",
            name="Migrate auth",
            description="Move session tokens to encrypted storage.",
            status="active",
            created_at=datetime(2026, 5, 1, tzinfo=UTC),
        )
        self._agent = Agent(
            id=1,
            slug="agt-1",
            work_id=1,
            name="Aria",
            persona="architect",
            role="planner",
            provider="claude_code",
            model="opus",
            folder=Path("/repo"),
            status="active",
            started_at=datetime(2026, 5, 1, tzinfo=UTC),
        )

    def get_work(self, slug: str):
        if slug != self._work.slug:
            return None
        return WorkRecord(work=self._work, contexts=[])

    def list_agents_for_work(self, _slug: str) -> list[Agent]:
        return [self._agent]

    def record_handoff(self, req: RecordHandoffRequest) -> Handoff:
        self.recorded.append(req)
        return Handoff(
            id=1,
            slug="hnd-1",
            work_id=1,
            source_agent_id=1,
            doc_path=Path(f"/handoffs/{req.doc_filename}"),
            created_at=datetime(2026, 5, 8, 12, 0, tzinfo=UTC),
            target_dialog=req.target_dialog,
        )


class _TranscriptLog:
    def __init__(self, events: list[dict[str, Any]]) -> None:
        self._events = events

    def read_from_cursor(
        self, _w: str, _a: str, _c: int
    ) -> Iterator[dict[str, Any]]:
        return iter(self._events)


def _summarizer_recording(captured: dict[str, Any]):
    def call(events: list[dict[str, Any]], context: SummaryContext) -> str:
        captured["events"] = events
        captured["context"] = context
        return "# Handoff doc"

    return call


def test_build_handoff_writes_via_workstore_and_returns_row() -> None:
    workstore = _Workstore()
    log = _TranscriptLog(
        [{"type": "user_input", "text": "go", "seq": 1, "ts": "x"}]
    )
    captured: dict[str, Any] = {}
    handoff = build_handoff(
        BuildHandoffRequest(work_slug="WRK-001", source_agent_slug="agt-1"),
        workstore=workstore,
        transcript_log=log,
        summarizer=_summarizer_recording(captured),
        clock=lambda: datetime(2026, 5, 8, 12, 30, 45, tzinfo=UTC),
    )
    assert handoff.slug == "hnd-1"
    assert workstore.recorded[0].source_agent_slug == "agt-1"
    assert workstore.recorded[0].target_dialog == "new-agent"
    assert workstore.recorded[0].doc_text == "# Handoff doc"
    # Filename is timestamp-stamped so multiple handoffs from the same
    # source don't clobber each other.
    assert workstore.recorded[0].doc_filename == "agt-1-handoff-20260508-123045.md"


def test_build_handoff_caps_transcript_at_200_events() -> None:
    workstore = _Workstore()
    events = [
        {"type": "user_input", "text": str(i), "seq": i, "ts": "x"}
        for i in range(500)
    ]
    log = _TranscriptLog(events)
    captured: dict[str, Any] = {}
    build_handoff(
        BuildHandoffRequest(work_slug="WRK-001", source_agent_slug="agt-1"),
        workstore=workstore,
        transcript_log=log,
        summarizer=_summarizer_recording(captured),
        clock=lambda: datetime(2026, 5, 8, tzinfo=UTC),
    )
    fed_events = captured["events"]
    assert len(fed_events) == 200
    # Tail-cap: oldest kept event is the most recent 200 (seq >= 300).
    assert fed_events[0]["seq"] == 300
    assert fed_events[-1]["seq"] == 499


def test_build_handoff_rejects_unknown_work() -> None:
    workstore = _Workstore()
    with pytest.raises(ValueError, match="work not found"):
        build_handoff(
            BuildHandoffRequest(
                work_slug="WRK-404", source_agent_slug="agt-1"
            ),
            workstore=workstore,
            transcript_log=_TranscriptLog([]),
            summarizer=_summarizer_recording({}),
            clock=lambda: datetime(2026, 5, 8, tzinfo=UTC),
        )


def test_build_handoff_rejects_unknown_source_agent() -> None:
    workstore = _Workstore()
    with pytest.raises(ValueError, match="source agent not found"):
        build_handoff(
            BuildHandoffRequest(
                work_slug="WRK-001", source_agent_slug="agt-impostor"
            ),
            workstore=workstore,
            transcript_log=_TranscriptLog([]),
            summarizer=_summarizer_recording({}),
            clock=lambda: datetime(2026, 5, 8, tzinfo=UTC),
        )
