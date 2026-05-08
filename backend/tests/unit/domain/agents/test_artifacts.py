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


def _resolve_workdir(_work: str, _agent: str) -> Path:
    return Path("/never-used")


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
        resolve_workdir=_resolve_workdir,
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
        resolve_workdir=_resolve_workdir,
    )
    assert store.calls[0].status == "open"


def test_pr_rejects_invalid_status() -> None:
    with pytest.raises(InvalidMarker, match="invalid status"):
        record_artifact(
            "WRK-001",
            "agt-7",
            {"type": "pr", "url": "https://x/1", "title": "t", "status": "wat"},
            workstore=_RecorderStore(),  # type: ignore[arg-type]
            resolve_workdir=_resolve_workdir,
        )


def test_pr_rejects_missing_url() -> None:
    with pytest.raises(InvalidMarker, match="'url'"):
        record_artifact(
            "WRK-001",
            "agt-7",
            {"type": "pr", "title": "t"},
            workstore=_RecorderStore(),  # type: ignore[arg-type]
            resolve_workdir=_resolve_workdir,
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
            resolve_workdir=_resolve_workdir,
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
            resolve_workdir=_resolve_workdir,
        )


# ---------------------------------------------------------------------------
# Doc — path validation
# ---------------------------------------------------------------------------


def test_doc_records_resolved_path_when_file_exists(tmp_path: Path) -> None:
    (tmp_path / "docs").mkdir()
    real = tmp_path / "docs" / "design.md"
    real.write_text("# design")

    store = _RecorderStore()

    def resolver(_w: str, _a: str) -> Path:
        return tmp_path

    record_artifact(
        "WRK-001",
        "agt-7",
        {"type": "doc", "path": "docs/design.md", "title": "Design"},
        workstore=store,  # type: ignore[arg-type]
        resolve_workdir=resolver,
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
            resolve_workdir=lambda _w, _a: tmp_path,
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
                resolve_workdir=lambda _w, _a: tmp_path,
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
            resolve_workdir=_resolve_workdir,
        )


def test_missing_title_rejected() -> None:
    with pytest.raises(InvalidMarker, match="'title'"):
        record_artifact(
            "WRK-001",
            "agt-7",
            {"type": "pr", "url": "https://x"},
            workstore=_RecorderStore(),  # type: ignore[arg-type]
            resolve_workdir=_resolve_workdir,
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
        resolve_workdir=_resolve_workdir,
    )
    req = store.calls[0]
    assert req.work_slug == "WRK-001"
    assert req.agent_slug == "agt-7"
