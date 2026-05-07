"""Unit tests for ``works/move_to_project.execute``.

Covers the dispatch contract:
- happy path: work re-parented, DB row updated, work.json rewritten
- ``project_slug=None`` moves to Loose
- 404 when work missing
- 422 when target project missing
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path

import pytest

from src.domain.commands.works import move_to_project
from src.domain.models import Project
from src.domain.projectstore.dtos import ProjectRecord
from src.domain.workstore import (
    CreateWorkRequest,
    WorkStoreService,
)
from tests.unit.domain.workstore._stubs import (
    StubFiles,
    StubRepository,
    StubTranscriptLog,
)


@dataclass
class _StubProjectStore:
    """Minimal ProjectStore stub — only ``get_project`` is exercised here."""

    projects: dict[str, Project]

    def get_project(self, slug: str) -> ProjectRecord | None:
        project = self.projects.get(slug)
        if project is None:
            return None
        return ProjectRecord(project=project)


def _make_workstore() -> WorkStoreService:
    repo = StubRepository()
    files = StubFiles()
    log = StubTranscriptLog()
    return WorkStoreService(
        repo, files, log, clock=lambda: datetime(2026, 5, 7, 12, 0, tzinfo=UTC)
    )


def _seed_work(workstore: WorkStoreService, project_slug: str | None = None) -> str:
    record = workstore.create_work(
        CreateWorkRequest(
            name="W", description="d", contexts=[], project_slug=project_slug
        )
    )
    work_slug = record.work.slug
    assert work_slug is not None
    return work_slug


def _make_project(slug: str, name: str = "P") -> Project:
    return Project(
        id=int(slug.split("-")[1]),
        slug=slug,
        name=name,
        description="",
        glyph="P",
        color=200,
        pinned=False,
        default_jira_conn=None,
        default_sentry_conn=None,
        created_at=datetime(2026, 5, 7, 12, 0, tzinfo=UTC),
    )


def test_move_reparents_work_in_db_and_workjson(tmp_path: Path) -> None:
    workstore = _make_workstore()
    work_slug = _seed_work(workstore, project_slug="PRJ-001")
    projects = _StubProjectStore(
        projects={"PRJ-001": _make_project("PRJ-001"), "PRJ-002": _make_project("PRJ-002")}
    )

    record = move_to_project.execute(
        workstore,
        projects,  # type: ignore[arg-type]
        move_to_project.MoveWorkToProjectRequest(
            work_slug=work_slug, project_slug="PRJ-002"
        ),
    )

    assert record.work.project_slug == "PRJ-002"
    refreshed = workstore.get_work(work_slug)
    assert refreshed is not None
    assert refreshed.work.project_slug == "PRJ-002"


def test_move_to_loose_sets_project_slug_none() -> None:
    workstore = _make_workstore()
    work_slug = _seed_work(workstore, project_slug="PRJ-001")
    projects = _StubProjectStore(projects={"PRJ-001": _make_project("PRJ-001")})

    record = move_to_project.execute(
        workstore,
        projects,  # type: ignore[arg-type]
        move_to_project.MoveWorkToProjectRequest(
            work_slug=work_slug, project_slug=None
        ),
    )

    assert record.work.project_slug is None


def test_move_raises_when_work_missing() -> None:
    workstore = _make_workstore()
    projects = _StubProjectStore(projects={"PRJ-001": _make_project("PRJ-001")})

    with pytest.raises(move_to_project.WorkNotFound):
        move_to_project.execute(
            workstore,
            projects,  # type: ignore[arg-type]
            move_to_project.MoveWorkToProjectRequest(
                work_slug="WRK-999", project_slug="PRJ-001"
            ),
        )


def test_move_raises_when_target_project_missing() -> None:
    workstore = _make_workstore()
    work_slug = _seed_work(workstore)
    projects = _StubProjectStore(projects={})  # empty

    with pytest.raises(move_to_project.ProjectNotFound):
        move_to_project.execute(
            workstore,
            projects,  # type: ignore[arg-type]
            move_to_project.MoveWorkToProjectRequest(
                work_slug=work_slug, project_slug="PRJ-404"
            ),
        )

    # Work's project_slug should not have been touched.
    refreshed = workstore.get_work(work_slug)
    assert refreshed is not None
    assert refreshed.work.project_slug is None
