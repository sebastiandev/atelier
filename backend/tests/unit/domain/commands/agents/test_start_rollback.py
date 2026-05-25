"""Rollback contract for ``agents/start.execute``.

When worktree provisioning fails after the agent row has already been
inserted, ``start`` must:
    * tear down the per-agent worktree (idempotent — handles missing)
    * delete the agent's workspace dir + DB row
    * re-raise ``WorktreeProvisionFailed`` so the route can surface
      stderr to the user

Without rollback the user is left with a zombie agent that can't be
re-attached (the WS connect path keeps hitting the same provisioning
failure).
"""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import pytest

from src.domain.commands.agents import start
from src.domain.models import SharedFolder
from src.domain.workstore import CreateWorkRequest, WorkStoreService
from src.domain.worktrees import WorktreeProvisionFailed
from src.settings import Settings
from tests.unit.domain.workstore._stubs import (
    StubFiles,
    StubRepository,
    StubTranscriptLog,
)


class _StubSupervisor:
    def __init__(self) -> None:
        self.registered: list[str] = []
        self.inputs: list[tuple[str, str]] = []

    async def register_agent(
        self, work_slug: str, agent_slug: str, adapter: Any, context: Any
    ) -> None:
        self.registered.append(agent_slug)

    async def send_input(self, agent_slug: str, message: str) -> None:
        self.inputs.append((agent_slug, message))

    async def stop_agent(self, agent_slug: str) -> None:
        # Used by the rollback path's worktree.remove fallback in some
        # configurations; harmless for the start-rollback case.
        pass


class _ExplodingWorktreeManager:
    """Worktree manager whose ``ensure`` always blows up — simulates
    the "branch already exists, checked out at missing path" failure
    mode that motivates the rollback."""

    def __init__(self) -> None:
        self.removed: list[tuple[str, str]] = []

    def ensure(
        self,
        work_slug: str,
        agent_slug: str,
        source: Path,
        base_ref: str = "HEAD",
        branch_name: str | None = None,
    ) -> Path:
        raise WorktreeProvisionFailed(
            f"git worktree add failed for {work_slug}/{agent_slug}: "
            "fatal: 'atelier/X/Y' is already checked out at '/missing/path'",
            stderr="fatal: 'atelier/X/Y' is already checked out at '/missing/path'",
        )

    def ensure_forked(
        self,
        work_slug: str,
        new_agent_slug: str,
        source_agent_slug: str,
        source: Path,
    ) -> Path:  # pragma: no cover — fork path not exercised here
        raise NotImplementedError

    def is_detached(self, workdir: Path) -> bool:  # pragma: no cover
        return False

    def remove(self, work_slug: str, agent_slug: str) -> None:
        self.removed.append((work_slug, agent_slug))

    def sweep_orphans(
        self, work_slug: str, live_agent_slugs: set[str]
    ) -> None:  # pragma: no cover
        pass


class _StubConnectionStore:
    def fetch_context_body(self, context: Any) -> str:
        return ""


class _StubSharestore:
    def list_for_project(self, project_slug: str) -> list[Any]:
        return []


class _StubProvisioner:
    def share_canonical_path(
        self, project_slug: str, share_slug: str
    ) -> Path:  # pragma: no cover — no shares in these tests
        raise NotImplementedError

    def mount_in_worktree(
        self,
        work_slug: str,
        agent_slug: str,
        mount_path: str,
        target: Path,
    ) -> None:  # pragma: no cover
        pass


def _make_workstore() -> tuple[WorkStoreService, StubFiles, StubRepository]:
    repo = StubRepository()
    files = StubFiles()
    log = StubTranscriptLog()
    return (
        WorkStoreService(
            repo, files, log, clock=lambda: datetime(2026, 5, 11, 12, 0, tzinfo=UTC)
        ),
        files,
        repo,
    )


def _seed_work(workstore: WorkStoreService) -> str:
    record = workstore.create_work(
        CreateWorkRequest(name="W", description="d", contexts=[])
    )
    assert record.work.slug is not None
    return record.work.slug


def test_start_rolls_back_agent_when_worktree_provisioning_fails(
    tmp_path: Path,
) -> None:
    workstore, files, _repo = _make_workstore()
    supervisor = _StubSupervisor()
    worktrees = _ExplodingWorktreeManager()
    work_slug = _seed_work(workstore)
    settings = Settings(workspace_root=tmp_path / "ws")

    req = start.StartAgentRequest(
        work_slug=work_slug,
        name="Dev",
        persona="developer",
        role="dev",
        provider="amp",
        model="rush",
        folder=tmp_path,
        options={},
        contexts=(),
    )

    with pytest.raises(WorktreeProvisionFailed) as exc:
        asyncio.run(
            start.execute(
                workstore,
                supervisor,
                worktrees,
                _StubConnectionStore(),
                _StubSharestore(),
                _StubProvisioner(),
                settings,
                req,
            )
        )

    # The exception preserves stderr so the route can surface it.
    assert "checked out" in exc.value.stderr
    # Rollback ran: worktree.remove was invoked and the agent row + dir
    # are gone. The user can immediately retry without zombie state.
    assert worktrees.removed == [(work_slug, "agt-1")]
    assert workstore.list_agents_for_work(work_slug) == []
    assert (work_slug, "agt-1") not in files.agent_jsons
    # Supervisor was never asked to register an agent that couldn't get
    # a workdir — keeps the supervisor's view of the world consistent.
    assert supervisor.registered == []


def test_mount_project_shares_returns_resolved_writable_roots(
    tmp_path: Path,
) -> None:
    external = tmp_path / "external-share"
    external.mkdir()
    canonical = tmp_path / "canonical-share"
    canonical.symlink_to(external, target_is_directory=True)

    share = SharedFolder(
        slug="shr-1",
        project_id=1,
        name="BMAD",
        mount_path="_bmad-output",
        created_at=datetime(2026, 5, 11, 12, 0, tzinfo=UTC),
    )

    class Sharestore:
        def list_for_project(self, project_slug: str) -> list[SharedFolder]:
            assert project_slug == "PRJ-001"
            return [share]

    class Provisioner:
        def __init__(self) -> None:
            self.mounts: list[tuple[str, str, str, Path]] = []

        def share_canonical_path(
            self, project_slug: str, share_slug: str
        ) -> Path:
            assert (project_slug, share_slug) == ("PRJ-001", "shr-1")
            return canonical

        def mount_in_worktree(
            self,
            work_slug: str,
            agent_slug: str,
            mount_path: str,
            target: Path,
        ) -> None:
            self.mounts.append((work_slug, agent_slug, mount_path, target))

    provisioner = Provisioner()

    mounted = start._mount_project_shares(
        sharestore=Sharestore(),
        provisioner=provisioner,
        project_slug="PRJ-001",
        work_slug="WRK-001",
        agent_slug="agt-1",
    )

    assert [(s.name, s.mount_path) for s in mounted.summaries] == [
        ("BMAD", "_bmad-output")
    ]
    assert mounted.writable_roots == (external,)
    assert provisioner.mounts == [
        ("WRK-001", "agt-1", "_bmad-output", canonical)
    ]
