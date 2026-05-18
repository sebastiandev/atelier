"""Unit tests for ``resume._catch_up_detached_agent``.

The function is private but encapsulates the parent-chain merge policy:
cover the dedup branches here so the catch-up's intent is locked down
without spinning up the full WS integration stack.
"""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path
from typing import Any
from unittest.mock import patch

from src.domain.commands.agents.resume import _catch_up_detached_agent
from src.domain.models import Agent, AgentStatus
from src.domain.workstore import WorkStoreService
from tests.unit.domain.workstore._stubs import (
    StubFiles,
    StubRepository,
    StubTranscriptLog,
)


def _make_workstore() -> tuple[WorkStoreService, StubTranscriptLog]:
    repo = StubRepository()
    files = StubFiles()
    log = StubTranscriptLog()
    service = WorkStoreService(
        repo, files, log, clock=lambda: datetime(2026, 5, 6, 12, 0, tzinfo=UTC)
    )
    return service, log


def _agent(
    *,
    session_id: str | None = "sess-current",
    parent_session_id: str | None = None,
    status: AgentStatus = AgentStatus.DETACHED,
) -> Agent:
    return Agent(
        slug="agt-1",
        work_id=1,
        name="A",
        persona="developer",
        role="dev",
        provider="claude-code",
        model="claude-opus-4-7",
        folder=Path("/tmp/work"),
        status=status,
        started_at=datetime(2026, 5, 6, 11, 0, tzinfo=UTC),
        session_id=session_id,
        parent_session_id=parent_session_id,
    )


def _types(events: list[dict[str, Any]]) -> list[str]:
    return [e["type"] for e in events]


def test_catch_up_no_session_id_just_flips_status() -> None:
    workstore, log = _make_workstore()
    agent = _agent(session_id=None)

    _catch_up_detached_agent(workstore, "WRK-001", "agt-1", agent, Path("/wd"))

    assert log.events.get(("WRK-001", "agt-1"), []) == []


def test_catch_up_without_parent_only_merges_current() -> None:
    workstore, log = _make_workstore()
    agent = _agent(parent_session_id=None)

    with (
        patch(
            "src.domain.commands.agents.resume.merge_cli_transcript",
            return_value=[{"type": "user_input", "ts": "t", "text": "hi"}],
        ) as mock_merge,
        patch(
            "src.domain.commands.agents.resume.sdk_cursor_at_detach",
            return_value={"provider": "codex", "line_count": 9},
        ),
    ):
        _catch_up_detached_agent(workstore, "WRK-001", "agt-1", agent, Path("/wd"))

    # Only one merge call — for the current session.
    assert mock_merge.call_count == 1
    assert mock_merge.call_args.args[1] == "sess-current"
    # No sdk_session_merged marker; just the current event + user_reattached.
    assert _types(log.events[("WRK-001", "agt-1")]) == ["user_input", "user_reattached"]
    marker = log.events[("WRK-001", "agt-1")][-1]
    assert marker["sdk_cursor"] == {"provider": "codex", "line_count": 9}


def test_catch_up_uses_latest_reattach_cursor_on_next_merge() -> None:
    workstore, log = _make_workstore()
    log.events[("WRK-001", "agt-1")] = [
        {
            "seq": 1,
            "type": "user_detached",
            "sdk_cursor": {"provider": "codex", "line_count": 10},
        },
        {
            "seq": 2,
            "type": "user_reattached",
            "events_merged": 0,
            "sdk_cursor": {"provider": "codex", "line_count": 20},
        },
    ]
    agent = _agent(parent_session_id=None)

    with (
        patch(
            "src.domain.commands.agents.resume.merge_cli_transcript",
            return_value=[],
        ) as mock_merge,
        patch(
            "src.domain.commands.agents.resume.sdk_cursor_at_detach",
            return_value={"provider": "codex", "line_count": 20},
        ),
    ):
        _catch_up_detached_agent(workstore, "WRK-001", "agt-1", agent, Path("/wd"))

    assert mock_merge.call_args.args[3] == {"provider": "codex", "line_count": 20}


def test_idle_catch_up_without_new_events_does_not_append_marker() -> None:
    workstore, log = _make_workstore()
    log.events[("WRK-001", "agt-1")] = [
        {
            "seq": 1,
            "type": "user_reattached",
            "events_merged": 0,
            "sdk_cursor": {"provider": "codex", "line_count": 20},
        },
    ]
    agent = _agent(parent_session_id=None, status=AgentStatus.IDLE)

    with patch(
        "src.domain.commands.agents.resume.merge_cli_transcript",
        return_value=[],
    ):
        _catch_up_detached_agent(
            workstore,
            "WRK-001",
            "agt-1",
            agent,
            Path("/wd"),
            emit_reattached_marker=False,
        )

    assert log.events[("WRK-001", "agt-1")] == [
        {
            "seq": 1,
            "type": "user_reattached",
            "events_merged": 0,
            "sdk_cursor": {"provider": "codex", "line_count": 20},
        }
    ]


def test_idle_catch_up_with_new_events_advances_cursor() -> None:
    workstore, log = _make_workstore()
    agent = _agent(parent_session_id=None, status=AgentStatus.IDLE)

    with (
        patch(
            "src.domain.commands.agents.resume.merge_cli_transcript",
            return_value=[{"type": "user_input", "ts": "t", "text": "late"}],
        ),
        patch(
            "src.domain.commands.agents.resume.sdk_cursor_at_detach",
            return_value={"provider": "codex", "line_count": 25},
        ),
    ):
        _catch_up_detached_agent(
            workstore,
            "WRK-001",
            "agt-1",
            agent,
            Path("/wd"),
            emit_reattached_marker=False,
        )

    assert _types(log.events[("WRK-001", "agt-1")]) == ["user_input", "user_reattached"]
    assert log.events[("WRK-001", "agt-1")][-1]["sdk_cursor"] == {
        "provider": "codex",
        "line_count": 25,
    }


def test_catch_up_with_parent_full_exports_when_unseen() -> None:
    workstore, log = _make_workstore()
    agent = _agent(parent_session_id="sess-parent")

    def fake_merge(_provider, session_id, _workdir, cursor):  # type: ignore[no-untyped-def]
        if session_id == "sess-parent":
            return [{"type": "message_complete", "ts": "tp", "text": "old"}]
        return [{"type": "user_input", "ts": "tc", "text": "new"}]

    with patch(
        "src.domain.commands.agents.resume.merge_cli_transcript",
        side_effect=fake_merge,
    ) as mock_merge:
        _catch_up_detached_agent(workstore, "WRK-001", "agt-1", agent, Path("/wd"))

    # Two merge calls: parent (cursor=None) then current (cursor=last detach).
    assert mock_merge.call_count == 2
    parent_call, current_call = mock_merge.call_args_list
    assert parent_call.args[1] == "sess-parent"
    assert parent_call.args[3] is None
    assert current_call.args[1] == "sess-current"

    types = _types(log.events[("WRK-001", "agt-1")])
    assert types == [
        "message_complete",
        "sdk_session_merged",
        "user_input",
        "user_reattached",
    ]
    marker = next(
        e for e in log.events[("WRK-001", "agt-1")] if e["type"] == "sdk_session_merged"
    )
    assert marker["session_id"] == "sess-parent"
    assert marker["events_merged"] == 1


def test_catch_up_skips_parent_when_session_established_already_in_ndjson() -> None:
    """Steady-state re-attach: the supervisor streamed the parent session
    live, so NDJSON already has session_established(sess-parent).
    Re-exporting it would duplicate every message."""
    workstore, log = _make_workstore()
    log.events[("WRK-001", "agt-1")] = [
        {"seq": 1, "type": "session_established", "session_id": "sess-parent"},
    ]
    agent = _agent(parent_session_id="sess-parent")

    with patch(
        "src.domain.commands.agents.resume.merge_cli_transcript",
        return_value=[],
    ) as mock_merge:
        _catch_up_detached_agent(workstore, "WRK-001", "agt-1", agent, Path("/wd"))

    # Only the current session is merged. Parent is dedup'd.
    assert mock_merge.call_count == 1
    assert mock_merge.call_args.args[1] == "sess-current"
    types = _types(log.events[("WRK-001", "agt-1")])
    assert "sdk_session_merged" not in types


def test_catch_up_skips_parent_when_sdk_session_merged_marker_exists() -> None:
    """A previous re-attach already exported the parent in full —
    don't re-import it on the next cycle."""
    workstore, log = _make_workstore()
    log.events[("WRK-001", "agt-1")] = [
        {
            "seq": 1,
            "type": "sdk_session_merged",
            "session_id": "sess-parent",
            "events_merged": 5,
        },
    ]
    agent = _agent(parent_session_id="sess-parent")

    with patch(
        "src.domain.commands.agents.resume.merge_cli_transcript",
        return_value=[],
    ) as mock_merge:
        _catch_up_detached_agent(workstore, "WRK-001", "agt-1", agent, Path("/wd"))

    assert mock_merge.call_count == 1
    assert mock_merge.call_args.args[1] == "sess-current"


def test_catch_up_flips_status_to_idle() -> None:
    workstore, _log = _make_workstore()
    # Pre-populate the agent so set_agent_status has something to update.
    repo: StubRepository = workstore._repo  # type: ignore[assignment]
    repo.add_agent(
        Agent(
            work_id=1,
            name="A",
            persona="developer",
            role="dev",
            provider="claude-code",
            model="claude-opus-4-7",
            folder=Path("/tmp/work"),
            status=AgentStatus.DETACHED,
            started_at=datetime(2026, 5, 6, 11, 0, tzinfo=UTC),
        )
    )
    agent = repo.agents["agt-1"]
    agent.session_id = "sess-current"

    with patch(
        "src.domain.commands.agents.resume.merge_cli_transcript",
        return_value=[],
    ):
        _catch_up_detached_agent(workstore, "WRK-001", "agt-1", agent, Path("/wd"))

    assert repo.agents["agt-1"].status == AgentStatus.IDLE
