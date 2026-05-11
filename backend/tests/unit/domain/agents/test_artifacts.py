"""Unit tests for the artifact tracker action.

These exercise validation per type and the attribution invariant against
a stub WorkStore — the dispatch into the supervisor is covered in the
supervisor service tests.
"""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import pytest

from src.domain.agents import InvalidMarker, record_artifact
from src.domain.models import Artifact
from src.domain.workstore.dtos import RecordArtifactRequest


class _RecorderStore:
    """Captures the RecordArtifactRequest the tracker sent us."""

    def __init__(self) -> None:
        self.calls: list[RecordArtifactRequest] = []
        self._next_id = 1

    def record_artifact(self, req: RecordArtifactRequest) -> Artifact:
        self.calls.append(req)
        artifact = Artifact(
            id=self._next_id,
            slug=f"art-{self._next_id}",
            work_id=1,
            agent_id=42,
            type=req.type,
            title=req.title,
            status=req.status,
            created_at=datetime(2026, 5, 8, tzinfo=UTC),
            url=req.url,
            repo=req.repo,
            doc_path=req.doc_path,
        )
        self._next_id += 1
        return artifact

    def __getattr__(self, name: str) -> Any:
        # Tracker only touches record_artifact; flag any other access.
        raise AssertionError(f"tracker should not call {name!r} on the store")


def _resolve_allowed_roots(_work: str, _agent: str) -> list[Path]:
    return [Path("/never-used")]


# ---------------------------------------------------------------------------
# PR
# ---------------------------------------------------------------------------


def test_pr_happy_path_with_required_fields() -> None:
    store = _RecorderStore()
    artifact = record_artifact(
        "WRK-001",
        "agt-7",
        {
            "type": "pr",
            "url": "https://github.com/x/y/pull/3",
            "title": "Add foo",
            "status": "open",
            "repo": "x/y",
        },
        workstore=store,  # type: ignore[arg-type]
        resolve_allowed_roots=_resolve_allowed_roots,
    )
    assert artifact.type == "pr"
    assert artifact.url == "https://github.com/x/y/pull/3"
    assert artifact.repo == "x/y"
    assert store.calls[0].work_slug == "WRK-001"
    assert store.calls[0].agent_slug == "agt-7"


def test_pr_status_defaults_to_open() -> None:
    store = _RecorderStore()
    record_artifact(
        "WRK-001",
        "agt-7",
        {"type": "pr", "url": "https://x/1", "title": "t"},
        workstore=store,  # type: ignore[arg-type]
        resolve_allowed_roots=_resolve_allowed_roots,
    )
    assert store.calls[0].status == "open"


def test_pr_rejects_invalid_status() -> None:
    with pytest.raises(InvalidMarker, match="invalid status"):
        record_artifact(
            "WRK-001",
            "agt-7",
            {"type": "pr", "url": "https://x/1", "title": "t", "status": "wat"},
            workstore=_RecorderStore(),  # type: ignore[arg-type]
            resolve_allowed_roots=_resolve_allowed_roots,
        )


def test_pr_rejects_missing_url() -> None:
    with pytest.raises(InvalidMarker, match="'url'"):
        record_artifact(
            "WRK-001",
            "agt-7",
            {"type": "pr", "title": "t"},
            workstore=_RecorderStore(),  # type: ignore[arg-type]
            resolve_allowed_roots=_resolve_allowed_roots,
        )


# ---------------------------------------------------------------------------
# Jira
# ---------------------------------------------------------------------------


def test_jira_requires_explicit_status() -> None:
    with pytest.raises(InvalidMarker, match="missing 'status'"):
        record_artifact(
            "WRK-001",
            "agt-7",
            {"type": "jira", "url": "https://j/X-1", "title": "t"},
            workstore=_RecorderStore(),  # type: ignore[arg-type]
            resolve_allowed_roots=_resolve_allowed_roots,
        )


def test_jira_rejects_pr_status_value() -> None:
    with pytest.raises(InvalidMarker, match="invalid status"):
        record_artifact(
            "WRK-001",
            "agt-7",
            {
                "type": "jira",
                "url": "https://j/X-1",
                "title": "t",
                "status": "merged",
            },
            workstore=_RecorderStore(),  # type: ignore[arg-type]
            resolve_allowed_roots=_resolve_allowed_roots,
        )


# ---------------------------------------------------------------------------
# Doc — path validation
# ---------------------------------------------------------------------------


def test_doc_records_resolved_path_when_file_exists(tmp_path: Path) -> None:
    (tmp_path / "docs").mkdir()
    real = tmp_path / "docs" / "design.md"
    real.write_text("# design")

    store = _RecorderStore()

    def resolver(_w: str, _a: str) -> list[Path]:
        return [tmp_path]

    record_artifact(
        "WRK-001",
        "agt-7",
        {"type": "doc", "path": "docs/design.md", "title": "Design"},
        workstore=store,  # type: ignore[arg-type]
        resolve_allowed_roots=resolver,
    )
    assert store.calls[0].doc_path == str(real.resolve())
    assert store.calls[0].status == "draft"


def test_doc_rejects_nonexistent_path(tmp_path: Path) -> None:
    with pytest.raises(InvalidMarker, match="does not exist"):
        record_artifact(
            "WRK-001",
            "agt-7",
            {"type": "doc", "path": "missing.md", "title": "x"},
            workstore=_RecorderStore(),  # type: ignore[arg-type]
            resolve_allowed_roots=lambda _w, _a: [tmp_path],
        )


def test_doc_tolerates_file_appearing_mid_wait(tmp_path: Path) -> None:
    """Race: Claude can emit Write and record_doc as parallel tool uses
    in the same turn — the file write completes a beat after we see the
    record_doc tool use. The tracker polls briefly so the file lands
    before we fail validation."""
    import threading

    target = tmp_path / "hello.md"

    def write_after_delay() -> None:
        # 100ms is well under the 500ms tracker wait but enough that
        # the first poll will miss the file.
        threading.Event().wait(0.1)
        target.write_text("hello")

    store = _RecorderStore()
    threading.Thread(target=write_after_delay, daemon=True).start()
    record_artifact(
        "WRK-001",
        "agt-7",
        {"type": "doc", "path": "hello.md", "title": "Hello"},
        workstore=store,  # type: ignore[arg-type]
        resolve_allowed_roots=lambda _w, _a: [tmp_path],
    )
    assert store.calls[0].doc_path == str(target.resolve())


def test_doc_accepts_path_under_a_shared_folder_root(tmp_path: Path) -> None:
    """Marker validator now accepts doc paths that resolve under any of
    the registered roots — worktree first, plus the project's shared
    folders. Useful for plans/notes the agent drops in a shared dir."""
    worktree = tmp_path / "worktree"
    worktree.mkdir()
    share = tmp_path / "share"
    share.mkdir()
    (share / "plan.md").write_text("# plan")

    store = _RecorderStore()
    record_artifact(
        "WRK-001",
        "agt-7",
        {"type": "doc", "path": str(share / "plan.md"), "title": "Plan"},
        workstore=store,  # type: ignore[arg-type]
        resolve_allowed_roots=lambda _w, _a: [worktree, share],
    )
    assert store.calls[0].doc_path == str((share / "plan.md").resolve())


def test_doc_rejects_path_outside_all_roots(tmp_path: Path) -> None:
    """A path under no allowed root is rejected, even if the file
    exists. Defends against an agent recording a doc anywhere on disk."""
    worktree = tmp_path / "worktree"
    worktree.mkdir()
    elsewhere = tmp_path / "elsewhere"
    elsewhere.mkdir()
    (elsewhere / "leak.md").write_text("nope")

    with pytest.raises(InvalidMarker, match="escapes"):
        record_artifact(
            "WRK-001",
            "agt-7",
            {"type": "doc", "path": str(elsewhere / "leak.md"), "title": "x"},
            workstore=_RecorderStore(),  # type: ignore[arg-type]
            resolve_allowed_roots=lambda _w, _a: [worktree],
        )


def test_doc_rejects_path_escape_via_dotdot(tmp_path: Path) -> None:
    outside = tmp_path.parent / "outside.md"
    outside.write_text("nope")
    try:
        with pytest.raises(InvalidMarker, match="escapes"):
            record_artifact(
                "WRK-001",
                "agt-7",
                {"type": "doc", "path": "../outside.md", "title": "x"},
                workstore=_RecorderStore(),  # type: ignore[arg-type]
                resolve_allowed_roots=lambda _w, _a: [tmp_path],
            )
    finally:
        outside.unlink(missing_ok=True)


# ---------------------------------------------------------------------------
# Type / generic validation + attribution invariant
# ---------------------------------------------------------------------------


def test_unknown_type_is_rejected() -> None:
    with pytest.raises(InvalidMarker, match="unknown artifact type"):
        record_artifact(
            "WRK-001",
            "agt-7",
            {"type": "release", "title": "x"},
            workstore=_RecorderStore(),  # type: ignore[arg-type]
            resolve_allowed_roots=_resolve_allowed_roots,
        )


def test_missing_title_rejected() -> None:
    with pytest.raises(InvalidMarker, match="'title'"):
        record_artifact(
            "WRK-001",
            "agt-7",
            {"type": "pr", "url": "https://x"},
            workstore=_RecorderStore(),  # type: ignore[arg-type]
            resolve_allowed_roots=_resolve_allowed_roots,
        )


def test_payload_attribution_is_ignored() -> None:
    """The agent cannot forge attribution by lying about agent_id/slug."""
    store = _RecorderStore()
    record_artifact(
        "WRK-001",
        "agt-7",
        {
            "type": "pr",
            "url": "https://x/1",
            "title": "t",
            "agent_id": 999,
            "agent_slug": "agt-impostor",
            "work_slug": "WRK-other",
        },
        workstore=store,  # type: ignore[arg-type]
        resolve_allowed_roots=_resolve_allowed_roots,
    )
    req = store.calls[0]
    assert req.work_slug == "WRK-001"
    assert req.agent_slug == "agt-7"
