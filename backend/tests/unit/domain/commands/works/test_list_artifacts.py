"""Unit tests for the ``list_artifacts`` query command.

Verifies the per-type dispatch + doc-status derivation. The route-level
test in ``test_works_routes`` covers the end-to-end FastAPI path; this
suite exercises the command in isolation against a stub workstore +
two injected resolvers so we can shape arbitrary location/git scenarios
without touching real git or filesystem paths.
"""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path
from unittest.mock import patch

from src.domain.artifacts import DocArtifact, JiraArtifact, PrArtifact
from src.domain.commands.works.list_artifacts import (
    ArtifactView,
    execute,
)
from src.domain.models import Agent, Work
from src.domain.workstore.dtos import WorkRecord

UTC_NOW = datetime(2026, 5, 13, tzinfo=UTC)


class _StubWorkStore:
    """Minimal stub: returns canned artifacts/agents/work for one slug."""

    def __init__(
        self,
        *,
        artifacts: list,
        agents: list[Agent],
        work: WorkRecord | None,
    ) -> None:
        self._artifacts = artifacts
        self._agents = agents
        self._work = work

    def list_artifacts_for_work(self, _work_slug: str) -> list:
        return list(self._artifacts)

    def list_agents_for_work(self, _work_slug: str) -> list[Agent]:
        return list(self._agents)

    def get_work(self, _work_slug: str) -> WorkRecord | None:
        return self._work


def _work_record(project_slug: str | None = "PRJ-001") -> WorkRecord:
    return WorkRecord(
        work=Work(
            id=1,
            slug="WRK-001",
            name="W",
            description="",
            status="active",
            created_at=UTC_NOW,
            project_slug=project_slug,
        ),
        contexts=[],
    )


def _agent(agent_id: int = 1, slug: str = "agt-1") -> Agent:
    return Agent(
        id=agent_id,
        slug=slug,
        work_id=1,
        name="A",
        persona="developer",
        role="r",
        provider="amp",
        model="smart",
        folder=Path("/tmp/folder"),
        status="idle",
        started_at=UTC_NOW,
    )


def test_pr_artifact_passes_through_status() -> None:
    """PR status is author-set — the query mustn't rewrite it."""
    store = _StubWorkStore(
        artifacts=[
            PrArtifact(
                id=1,
                slug="art-1",
                work_id=1,
                agent_id=1,
                title="Fix",
                status="open",
                url="https://x/1",
                created_at=UTC_NOW,
            )
        ],
        agents=[_agent()],
        work=_work_record(),
    )
    views = execute(
        workstore=store,  # type: ignore[arg-type]
        work_slug="WRK-001",
        resolve_worktree=lambda _w, _a: None,
        resolve_share_roots=lambda _p: [],
    )
    assert len(views) == 1
    assert views[0] == ArtifactView(
        slug="art-1",
        type="pr",
        title="Fix",
        status="open",
        created_at=UTC_NOW,
        agent_slug="agt-1",
        url="https://x/1",
        repo=None,
        doc_path=None,
        location_kind=None,
    )


def test_doc_artifact_in_shared_folder_derives_draft_status(
    tmp_path: Path,
) -> None:
    """Shared-folder docs always render as ``draft`` regardless of git
    state — the doc lives outside the worktree concept."""
    share_root = tmp_path / "share"
    share_root.mkdir()
    doc = share_root / "story.md"
    doc.write_text("hi")

    store = _StubWorkStore(
        artifacts=[
            DocArtifact(
                id=1,
                slug="art-1",
                work_id=1,
                agent_id=1,
                title="Story",
                status="draft",
                doc_path=str(doc),
                created_at=UTC_NOW,
            )
        ],
        agents=[_agent()],
        work=_work_record(),
    )
    views = execute(
        workstore=store,  # type: ignore[arg-type]
        work_slug="WRK-001",
        resolve_worktree=lambda _w, _a: tmp_path / "never_a_worktree",
        resolve_share_roots=lambda _p: [share_root],
    )
    assert views[0].status == "draft"
    assert views[0].location_kind == "shared"


def test_doc_artifact_in_worktree_derives_from_git_state(
    tmp_path: Path,
) -> None:
    """Worktree docs collapse git_state into status: ``committed`` if
    the helper reports committed, otherwise ``pending``."""
    worktree = tmp_path / "wt"
    worktree.mkdir()
    doc = worktree / "design.md"
    doc.write_text("plan")

    store = _StubWorkStore(
        artifacts=[
            DocArtifact(
                id=1,
                slug="art-1",
                work_id=1,
                agent_id=1,
                title="Design",
                status="draft",
                doc_path=str(doc),
                created_at=UTC_NOW,
            )
        ],
        agents=[_agent()],
        work=_work_record(),
    )

    def _run(stub_git_state: str | None) -> ArtifactView:
        with patch(
            "src.domain.commands.works.list_artifacts.git_state",
            return_value=stub_git_state,
        ):
            return execute(
                workstore=store,  # type: ignore[arg-type]
                work_slug="WRK-001",
                resolve_worktree=lambda _w, _a: worktree,
                resolve_share_roots=lambda _p: [],
            )[0]

    assert _run("committed").status == "committed"
    assert _run("committed").location_kind == "worktree"
    assert _run("uncommitted").status == "pending"
    assert _run(None).status == "pending"


def test_doc_artifact_with_missing_agent_falls_back_to_draft(
    tmp_path: Path,
) -> None:
    """When the agent owner has been deleted (FK SET NULL), worktree
    resolution returns None — doc classifies under no known root, so
    we fall back to ``draft`` rather than crashing."""
    doc = tmp_path / "orphan.md"
    doc.write_text("x")
    store = _StubWorkStore(
        artifacts=[
            DocArtifact(
                id=1,
                slug="art-1",
                work_id=1,
                agent_id=None,
                title="Orphan",
                status="draft",
                doc_path=str(doc),
                created_at=UTC_NOW,
            )
        ],
        agents=[],
        work=_work_record(),
    )
    views = execute(
        workstore=store,  # type: ignore[arg-type]
        work_slug="WRK-001",
        resolve_worktree=lambda _w, _a: None,
        resolve_share_roots=lambda _p: [],
    )
    assert views[0].status == "draft"
    assert views[0].location_kind is None
    assert views[0].agent_slug is None


def test_jira_artifact_passes_status_through() -> None:
    store = _StubWorkStore(
        artifacts=[
            JiraArtifact(
                id=1,
                slug="art-1",
                work_id=1,
                agent_id=1,
                title="JIRA-1",
                status="in_review",
                url="https://j/X-1",
                created_at=UTC_NOW,
            )
        ],
        agents=[_agent()],
        work=_work_record(),
    )
    views = execute(
        workstore=store,  # type: ignore[arg-type]
        work_slug="WRK-001",
        resolve_worktree=lambda _w, _a: None,
        resolve_share_roots=lambda _p: [],
    )
    assert views[0].type == "jira"
    assert views[0].status == "in_review"
    assert views[0].url == "https://j/X-1"


def test_loose_work_has_no_share_roots() -> None:
    """Work without a project_slug — list still runs, share_roots is
    empty, no doc artifacts get a location_kind."""
    doc_path = "/tmp/nonexistent.md"
    store = _StubWorkStore(
        artifacts=[
            DocArtifact(
                id=1,
                slug="art-1",
                work_id=1,
                agent_id=1,
                title="X",
                status="draft",
                doc_path=doc_path,
                created_at=UTC_NOW,
            )
        ],
        agents=[_agent()],
        work=_work_record(project_slug=None),
    )
    resolve_shares_called: list[str] = []
    views = execute(
        workstore=store,  # type: ignore[arg-type]
        work_slug="WRK-001",
        resolve_worktree=lambda _w, _a: None,
        resolve_share_roots=lambda p: resolve_shares_called.append(p) or [],
    )
    assert views[0].location_kind is None
    assert resolve_shares_called == []
