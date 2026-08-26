"""Which directory a launcher acts on, per entity.

Reveal, console and editor all resolve through here, so a difference
between them would mean the same run opening two directories.
"""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path

import pytest

from src.domain.loop.dtos import LoopStatus, LoopTargetKind
from src.domain.loop.models import LoopRunRecord
from src.infrastructure.filesystem.paths import WorkspacePaths
from src.infrastructure.filesystem.workspace_targets import (
    WorkspaceNotFound,
    agent_workspace,
    run_workspace,
)

NOW = datetime(2026, 8, 26, tzinfo=UTC)


class _Agent:
    def __init__(self, slug: str, folder: Path, worktree_slug: str | None = None):
        self.slug = slug
        self.folder = folder
        self.worktree_slug = worktree_slug


class _WorkStore:
    def __init__(self, agents: dict[str, _Agent], work_slug: str = "WRK-001"):
        self._agents = agents
        self._work_slug = work_slug

    def get_work_slug_for_agent(self, agent_slug: str) -> str | None:
        return self._work_slug if agent_slug in self._agents else None

    def list_agents_for_work(self, work_slug: str) -> list[_Agent]:
        return list(self._agents.values())


def _record(**state: object) -> LoopRunRecord:
    return LoopRunRecord(
        run_key="loop-objective-run-001",
        work_slug="WRK-001",
        target_kind=LoopTargetKind.OBJECTIVE,
        target_ref="ship it",
        artifact_id=None,
        plan_run_id="run-001",
        definition_id="atelier-fast",
        definition_revision="rev-1",
        definition_snapshot={},
        status=LoopStatus.RUNNING,
        current_step_id=None,
        state=dict(state),
        started_at=NOW,
        updated_at=NOW,
    )


def test_agent_workspace_prefers_a_provisioned_worktree(tmp_path: Path) -> None:
    paths = WorkspacePaths(workspace_root=tmp_path)
    worktree = paths.worktree_dir("WRK-001", "agt-1")
    worktree.mkdir(parents=True)
    store = _WorkStore({"agt-1": _Agent("agt-1", tmp_path / "src")})

    assert agent_workspace(store, paths, "agt-1") == worktree


def test_agent_workspace_falls_back_to_the_source_folder(tmp_path: Path) -> None:
    """No worktree on disk means the adapter ran in the source folder."""
    paths = WorkspacePaths(workspace_root=tmp_path)
    store = _WorkStore({"agt-1": _Agent("agt-1", tmp_path / "src")})

    assert agent_workspace(store, paths, "agt-1") == tmp_path / "src"


def test_agent_workspace_rejects_an_unknown_agent(tmp_path: Path) -> None:
    paths = WorkspacePaths(workspace_root=tmp_path)

    with pytest.raises(WorkspaceNotFound):
        agent_workspace(_WorkStore({}), paths, "agt-404")


def test_run_workspace_prefers_the_runs_own_workspace(tmp_path: Path) -> None:
    """A run seeded from another keeps the source run's workspace, which
    is why the run's record wins over any agent's worktree."""
    paths = WorkspacePaths(workspace_root=tmp_path)
    worktree = paths.worktree_dir("WRK-001", "agt-1")
    worktree.mkdir(parents=True)
    store = _WorkStore({"agt-1": _Agent("agt-1", tmp_path / "src")})
    record = _record(workspace_path="/tmp/run-workspace", agent_slug="agt-1")

    assert run_workspace(record, store, paths) == Path("/tmp/run-workspace")


def test_run_workspace_falls_back_to_the_runs_agent(tmp_path: Path) -> None:
    """Planning runs record no workspace: their stages execute in the
    worktree of the agent that owns the run."""
    paths = WorkspacePaths(workspace_root=tmp_path)
    worktree = paths.worktree_dir("WRK-001", "agt-1")
    worktree.mkdir(parents=True)
    store = _WorkStore({"agt-1": _Agent("agt-1", tmp_path / "src")})
    record = _record(agent_slug="agt-1")

    assert run_workspace(record, store, paths) == worktree


@pytest.mark.parametrize("state", [{}, {"workspace_path": ""}, {"agent_slug": ""}])
def test_run_workspace_reports_a_run_it_cannot_place(
    tmp_path: Path, state: dict[str, object]
) -> None:
    paths = WorkspacePaths(workspace_root=tmp_path)

    with pytest.raises(WorkspaceNotFound):
        run_workspace(_record(**state), _WorkStore({}), paths)
