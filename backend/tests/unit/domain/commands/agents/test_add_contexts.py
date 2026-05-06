"""Unit tests for ``agents/add_contexts.execute``.

Exercises the snapshot-stable behaviour: existing per-source files are
left alone, only new entries get written. The index is rebuilt from the
merged list.
"""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path

import pytest

from src.domain.commands.agents import add_contexts
from src.domain.models import Context
from src.domain.workstore import AddAgentRequest, CreateWorkRequest, WorkStoreService
from tests.unit.domain.workstore._stubs import (
    StubFiles,
    StubRepository,
    StubTranscriptLog,
)


class _StubConnectionStore:
    """Minimal ConnectionStore stub — only ``fetch_context_body`` is
    needed for add_contexts. Each call returns a deterministic body so
    tests can assert on the file contents written to disk."""

    def __init__(self, *, fail_on: str | None = None) -> None:
        self.calls: list[Context] = []
        self._fail_on = fail_on

    def fetch_context_body(self, context: Context) -> str:
        self.calls.append(context)
        if self._fail_on is not None and context.value == self._fail_on:
            from src.domain.connections import ContextFetchError

            raise ContextFetchError(f"stub fetch failed for {context.value}")
        return f"# {context.type}-{context.value}\n\nstub body for {context.value}\n"


def _make_workstore() -> tuple[WorkStoreService, StubFiles]:
    repo = StubRepository()
    files = StubFiles()
    log = StubTranscriptLog()
    service = WorkStoreService(
        repo, files, log, clock=lambda: datetime(2026, 5, 6, 12, 0, tzinfo=UTC)
    )
    service.create_work(
        CreateWorkRequest(
            name="W",
            description="d",
            contexts=[Context(type="text", value="seed", conn_id=None)],
        )
    )
    service.add_agent_to_work(
        AddAgentRequest(
            work_slug="WRK-001",
            name="A",
            persona="developer",
            role="dev",
            provider="amp",
            model="smart",
            folder=Path("/tmp/work"),
            contexts=(Context(type="text", value="hello existing", conn_id=None),),
        )
    )
    return service, files


def _files_written(stub: StubFiles, work_slug: str, agent_slug: str) -> dict[str, str]:
    """Walk the stub's writes and return per-context-file content."""
    return {
        filename: content
        for (
            ws,
            ag,
            filename,
        ), content in stub.context_files.items()
        if ws == work_slug and ag == agent_slug
    }


def test_add_contexts_no_op_for_empty_request() -> None:
    workstore, _ = _make_workstore()
    conn_store = _StubConnectionStore()
    result = add_contexts.execute(
        workstore,
        conn_store,  # type: ignore[arg-type]
        add_contexts.AddContextsRequest(agent_slug="agt-1", contexts=()),
    )
    assert result.new_filenames == ()
    assert conn_store.calls == []


def test_add_contexts_appends_text_and_returns_new_filename() -> None:
    workstore, files = _make_workstore()
    conn_store = _StubConnectionStore()

    result = add_contexts.execute(
        workstore,
        conn_store,  # type: ignore[arg-type]
        add_contexts.AddContextsRequest(
            agent_slug="agt-1",
            contexts=(Context(type="text", value="new note", conn_id=None),),
        ),
    )

    # Existing was text-1; new should be text-2.
    assert result.new_filenames == ("text-2.md",)
    written = _files_written(files, "WRK-001", "agt-1")
    assert "text-2.md" in written
    # The agent.json now carries both contexts.
    persisted = workstore.get_agent_contexts("WRK-001", "agt-1")
    assert [c.value for c in persisted] == ["hello existing", "new note"]


def test_add_contexts_fetches_only_new_connection_backed_entries() -> None:
    """Snapshot stability: pre-existing connection-backed entries are
    NOT re-fetched on subsequent adds — their files on disk stay."""
    workstore, _ = _make_workstore()
    # Seed an existing jira context directly into agent.json (simulating
    # a context that was already fetched at start time).
    workstore.replace_agent_contexts(
        "WRK-001",
        "agt-1",
        [
            Context(type="text", value="hello existing", conn_id=None),
            Context(type="jira", value="OLD-1", conn_id="con-1"),
        ],
    )

    conn_store = _StubConnectionStore()
    add_contexts.execute(
        workstore,
        conn_store,  # type: ignore[arg-type]
        add_contexts.AddContextsRequest(
            agent_slug="agt-1",
            contexts=(Context(type="jira", value="NEW-2", conn_id="con-1"),),
        ),
    )

    # Only the NEW jira ticket triggered a fetch.
    assert [c.value for c in conn_store.calls] == ["NEW-2"]


def test_add_contexts_unknown_agent_raises() -> None:
    workstore, _ = _make_workstore()
    conn_store = _StubConnectionStore()
    with pytest.raises(add_contexts.AgentNotFound):
        add_contexts.execute(
            workstore,
            conn_store,  # type: ignore[arg-type]
            add_contexts.AddContextsRequest(
                agent_slug="agt-404",
                contexts=(Context(type="text", value="x", conn_id=None),),
            ),
        )


def test_add_contexts_fetch_failure_leaves_state_untouched() -> None:
    """ContextFetchError propagates; nothing on disk or in agent.json
    changes. The user retries after fixing the connection."""
    workstore, files = _make_workstore()
    conn_store = _StubConnectionStore(fail_on="BREAK-1")

    from src.domain.connections import ContextFetchError

    initial_files = dict(files.context_files)
    initial_contexts = workstore.get_agent_contexts("WRK-001", "agt-1")

    with pytest.raises(ContextFetchError):
        add_contexts.execute(
            workstore,
            conn_store,  # type: ignore[arg-type]
            add_contexts.AddContextsRequest(
                agent_slug="agt-1",
                contexts=(Context(type="jira", value="BREAK-1", conn_id="con-1"),),
            ),
        )

    assert files.context_files == initial_files
    assert workstore.get_agent_contexts("WRK-001", "agt-1") == initial_contexts
