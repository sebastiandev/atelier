"""Tests for GitWorktreeManager against a real tmp git repo.

These exercise actual ``git worktree`` subprocess calls — they require
git on PATH. The fixture creates a one-commit repo per test so tests
stay isolated and side-effect free outside the tmp dir.
"""

from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

from src.infrastructure.filesystem.paths import WorkspacePaths
from src.infrastructure.git.worktree_manager import GitWorktreeManager


def _git(cwd: Path, *args: str) -> None:
    subprocess.run(["git", *args], cwd=str(cwd), check=True, capture_output=True)


@pytest.fixture
def repo(tmp_path: Path) -> Path:
    """A fresh git repo at ``tmp_path/source`` with one commit on the
    default branch (renamed to ``main`` to keep tests deterministic
    regardless of the host's git defaults)."""
    src = tmp_path / "source"
    src.mkdir()
    _git(src, "init", "-q", "-b", "main")
    _git(src, "config", "user.email", "test@example.com")
    _git(src, "config", "user.name", "Test")
    (src / "README.md").write_text("hello\n")
    _git(src, "add", "README.md")
    _git(src, "commit", "-q", "-m", "initial")
    return src


@pytest.fixture
def manager(tmp_path: Path) -> GitWorktreeManager:
    paths = WorkspacePaths(workspace_root=tmp_path / "atelier")
    return GitWorktreeManager(paths)


def test_ensure_creates_worktree_under_workspace_root(
    manager: GitWorktreeManager, repo: Path, tmp_path: Path
) -> None:
    workdir = manager.ensure("WRK-001", "agt-1", repo)

    expected = tmp_path / "atelier" / "works" / "WRK-001" / "worktrees" / "agt-1"
    assert workdir == expected
    assert workdir.exists()
    assert (workdir / ".git").is_file()  # worktree has a gitlink, not a dir
    assert (workdir / "README.md").read_text() == "hello\n"


def test_ensure_forked_inherits_uncommitted_state(
    manager: GitWorktreeManager, repo: Path
) -> None:
    """The forked agent starts at the source agent's HEAD (detached) AND
    inherits its modified + untracked-not-gitignored files."""
    source_workdir = manager.ensure("WRK-001", "agt-1", repo)
    # Source agent makes uncommitted changes + an untracked file.
    (source_workdir / "README.md").write_text("modified by agt-1\n")
    (source_workdir / "scratch.md").write_text("untracked draft\n")
    # And a gitignored file we should NOT see in the fork.
    (source_workdir / ".gitignore").write_text("ignored.bin\n")
    (source_workdir / "ignored.bin").write_bytes(b"DO NOT COPY")

    forked = manager.ensure_forked("WRK-001", "agt-2", "agt-1", repo)

    # New worktree exists under the workspace root and is detached
    # (no auto-branch created).
    assert forked.exists()
    assert (forked / ".git").is_file()
    head = subprocess.run(
        ["git", "symbolic-ref", "-q", "HEAD"],
        cwd=str(forked),
        capture_output=True,
        text=True,
    )
    # symbolic-ref returns non-zero when HEAD is detached — exactly what
    # we want for "no auto-branch".
    assert head.returncode != 0

    # Inherited the source's uncommitted modifications.
    assert (forked / "README.md").read_text() == "modified by agt-1\n"
    # Inherited the untracked-not-gitignored file too.
    assert (forked / "scratch.md").read_text() == "untracked draft\n"
    # Did NOT inherit gitignored bulk.
    assert not (forked / "ignored.bin").exists()


def test_ensure_forked_is_idempotent(
    manager: GitWorktreeManager, repo: Path
) -> None:
    manager.ensure("WRK-001", "agt-1", repo)
    a = manager.ensure_forked("WRK-001", "agt-2", "agt-1", repo)
    b = manager.ensure_forked("WRK-001", "agt-2", "agt-1", repo)
    assert a == b


def test_ensure_forked_for_non_git_source_falls_back_to_copy(
    manager: GitWorktreeManager, tmp_path: Path
) -> None:
    """Non-git source folders don't have worktrees — we copy instead."""
    plain = tmp_path / "plain"
    plain.mkdir()
    (plain / "notes.md").write_text("just files\n")

    forked = manager.ensure_forked("WRK-001", "agt-2", "agt-1", plain)
    assert forked.exists()
    assert (forked / "notes.md").read_text() == "just files\n"


def test_ensure_two_agents_get_two_worktrees(
    manager: GitWorktreeManager, repo: Path
) -> None:
    a = manager.ensure("WRK-001", "agt-1", repo)
    b = manager.ensure("WRK-001", "agt-2", repo)

    assert a != b
    assert a.exists() and b.exists()
    # Both checkouts pin to the source — verify by reading a file each.
    assert (a / "README.md").exists()
    assert (b / "README.md").exists()


def test_ensure_is_idempotent(manager: GitWorktreeManager, repo: Path) -> None:
    first = manager.ensure("WRK-001", "agt-1", repo)
    second = manager.ensure("WRK-001", "agt-1", repo)

    assert first == second
    assert first.exists()


def test_ensure_passes_through_for_non_git_folder(
    manager: GitWorktreeManager, tmp_path: Path
) -> None:
    plain = tmp_path / "plain"
    plain.mkdir()
    (plain / "notes.txt").write_text("just a folder")

    workdir = manager.ensure("WRK-001", "agt-1", plain)

    # The agent gets the folder itself; no worktree dir was created.
    assert workdir == plain
    atelier_root = tmp_path / "atelier"
    assert not atelier_root.exists()


def test_remove_tears_down_worktree(
    manager: GitWorktreeManager, repo: Path
) -> None:
    workdir = manager.ensure("WRK-001", "agt-1", repo)
    assert workdir.exists()

    manager.remove("WRK-001", "agt-1")

    assert not workdir.exists()
    # Source repo's worktree registry is cleared so a re-ensure works.
    again = manager.ensure("WRK-001", "agt-1", repo)
    assert again.exists()


def test_remove_missing_is_noop(manager: GitWorktreeManager) -> None:
    # Should not raise — remove on a non-existent worktree is fine.
    manager.remove("WRK-001", "ghost")


def test_remove_force_falls_back_to_rmtree_when_dirty(
    manager: GitWorktreeManager, repo: Path
) -> None:
    """A dirty worktree refuses ``git worktree remove``; the manager
    should escalate to --force, then to rmtree if even that fails. We
    exercise the --force path here by leaving uncommitted edits."""
    workdir = manager.ensure("WRK-001", "agt-1", repo)
    (workdir / "README.md").write_text("dirty edit\n")

    manager.remove("WRK-001", "agt-1")

    assert not workdir.exists()


def test_sweep_orphans_removes_worktrees_not_in_live_set(
    manager: GitWorktreeManager, repo: Path
) -> None:
    manager.ensure("WRK-001", "agt-1", repo)
    manager.ensure("WRK-001", "agt-2", repo)
    manager.ensure("WRK-001", "agt-3", repo)

    manager.sweep_orphans("WRK-001", live_agent_slugs={"agt-2"})

    base = repo
    assert (manager._paths.workspace_root / "works" / "WRK-001" / "worktrees" / "agt-2").exists()
    assert not (manager._paths.workspace_root / "works" / "WRK-001" / "worktrees" / "agt-1").exists()
    assert not (manager._paths.workspace_root / "works" / "WRK-001" / "worktrees" / "agt-3").exists()
    # The source repo's git worktree registry should be pruned along
    # with the dirs — re-ensuring agt-1 should succeed.
    revived = manager.ensure("WRK-001", "agt-1", base)
    assert revived.exists()


def test_sweep_orphans_on_empty_work_is_noop(manager: GitWorktreeManager) -> None:
    # Work has no worktrees dir yet — nothing to do.
    manager.sweep_orphans("WRK-404", live_agent_slugs=set())
