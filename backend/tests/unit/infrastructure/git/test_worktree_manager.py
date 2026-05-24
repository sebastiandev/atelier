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


def test_remove_deletes_per_agent_atelier_branch(
    manager: GitWorktreeManager, repo: Path
) -> None:
    """When the user opted into a branch on agent creation, ``remove``
    must drop it on teardown — otherwise a future agent that lands on
    the same slug after a wipe collides with the leftover branch."""
    manager.ensure("WRK-001", "agt-1", repo, branch_name="atelier/WRK-001/agt-1")

    branches_before = subprocess.run(
        ["git", "branch", "--list", "atelier/WRK-001/agt-1"],
        cwd=str(repo),
        check=True,
        capture_output=True,
        text=True,
    ).stdout
    assert "atelier/WRK-001/agt-1" in branches_before

    manager.remove("WRK-001", "agt-1")

    branches_after = subprocess.run(
        ["git", "branch", "--list", "atelier/WRK-001/agt-1"],
        cwd=str(repo),
        check=True,
        capture_output=True,
        text=True,
    ).stdout
    assert branches_after.strip() == ""


def test_ensure_self_heals_when_branch_lingers_after_dir_was_wiped(
    manager: GitWorktreeManager, repo: Path, tmp_path: Path
) -> None:
    """Reproduces the wipe.sh aftermath on the named-branch path: branch
    exists, git's worktree registry still references a missing path. A
    naive retry would fail with "branch is already checked out at
    <missing>" — the manager should prune + retry and succeed."""
    workdir = manager.ensure(
        "WRK-001", "agt-1", repo, branch_name="atelier/WRK-001/agt-1"
    )
    # Simulate the wipe: blow away the worktree dir without telling git.
    import shutil as _shutil

    _shutil.rmtree(workdir)

    # Sanity: git still thinks the worktree exists (it's "prunable").
    listing = subprocess.run(
        ["git", "worktree", "list"],
        cwd=str(repo),
        check=True,
        capture_output=True,
        text=True,
    ).stdout
    assert "prunable" in listing

    # Re-ensure should now self-heal: branch exists, prunable registry
    # entry exists, so the first add fails, the fallback fails too, and
    # the manager prunes + retries.
    revived = manager.ensure(
        "WRK-001", "agt-1", repo, branch_name="atelier/WRK-001/agt-1"
    )
    assert revived.exists()
    assert (revived / ".git").exists()


def test_ensure_default_is_detached_head(
    manager: GitWorktreeManager, repo: Path
) -> None:
    """No branch_name → detached HEAD; symbolic-ref returns non-zero."""
    workdir = manager.ensure("WRK-001", "agt-1", repo)
    head = subprocess.run(
        ["git", "symbolic-ref", "-q", "HEAD"],
        cwd=str(workdir),
        capture_output=True,
    )
    assert head.returncode != 0


def test_ensure_with_branch_name_creates_named_branch(
    manager: GitWorktreeManager, repo: Path
) -> None:
    workdir = manager.ensure("WRK-001", "agt-1", repo, branch_name="my-feature")
    head = subprocess.run(
        ["git", "symbolic-ref", "HEAD"],
        cwd=str(workdir),
        check=True,
        capture_output=True,
        text=True,
    ).stdout.strip()
    assert head == "refs/heads/my-feature"


def test_is_detached_reports_true_for_detached_default(
    manager: GitWorktreeManager, repo: Path
) -> None:
    workdir = manager.ensure("WRK-001", "agt-1", repo)
    assert manager.is_detached(workdir) is True


def test_is_detached_reports_false_when_branch_named(
    manager: GitWorktreeManager, repo: Path
) -> None:
    workdir = manager.ensure("WRK-001", "agt-1", repo, branch_name="feature-x")
    assert manager.is_detached(workdir) is False


def test_is_detached_returns_false_for_non_git_folder(
    manager: GitWorktreeManager, tmp_path: Path
) -> None:
    plain = tmp_path / "plain"
    plain.mkdir()
    assert manager.is_detached(plain) is False


# --- devtime artifact symlinks ----------------------------------------------


def test_ensure_symlinks_top_level_venv_into_worktree(
    manager: GitWorktreeManager, repo: Path
) -> None:
    """A ``.venv`` at the source root is gitignored, so it doesn't get
    cloned into the worktree by ``git worktree add``. We mirror it as a
    symlink so the agent's shell sees the same env the user has."""
    venv = repo / ".venv"
    venv.mkdir()
    (venv / "marker").write_text("present\n")

    workdir = manager.ensure("WRK-001", "agt-1", repo)

    link = workdir / ".venv"
    assert link.is_symlink()
    assert link.resolve() == venv.resolve()
    assert (link / "marker").read_text() == "present\n"


def test_ensure_symlinks_nested_venv_for_monorepos(
    manager: GitWorktreeManager, repo: Path
) -> None:
    """Atelier-style layouts put venv/node_modules one level down
    (``backend/.venv``, ``frontend/node_modules``). The target subdir
    must already exist in the worktree (from a tracked file) for the
    mirror to fire — we don't fabricate subdirs."""
    backend = repo / "backend"
    backend.mkdir()
    (backend / "app.py").write_text("pass\n")
    _git(repo, "add", "backend/app.py")
    _git(repo, "commit", "-q", "-m", "add backend")

    venv = backend / ".venv"
    venv.mkdir()

    frontend = repo / "frontend"
    frontend.mkdir()
    (frontend / "index.html").write_text("<html></html>\n")
    _git(repo, "add", "frontend/index.html")
    _git(repo, "commit", "-q", "-m", "add frontend")

    node_modules = frontend / "node_modules"
    node_modules.mkdir()

    workdir = manager.ensure("WRK-001", "agt-1", repo)

    backend_link = workdir / "backend" / ".venv"
    frontend_link = workdir / "frontend" / "node_modules"
    assert backend_link.is_symlink()
    assert backend_link.resolve() == venv.resolve()
    assert frontend_link.is_symlink()
    assert frontend_link.resolve() == node_modules.resolve()


def test_ensure_skips_symlink_when_source_has_no_devtime_artifacts(
    manager: GitWorktreeManager, repo: Path
) -> None:
    """No .venv / venv / node_modules at the source → no symlinks
    created. Non-Python / non-Node repos should be untouched."""
    workdir = manager.ensure("WRK-001", "agt-1", repo)

    assert not (workdir / ".venv").exists()
    assert not (workdir / "venv").exists()
    assert not (workdir / "node_modules").exists()


def test_ensure_skips_when_devtime_artifact_is_a_file_not_a_dir(
    manager: GitWorktreeManager, repo: Path
) -> None:
    """A regular file named ``.venv`` (unusual but possible) must not
    be symlinked — we only mirror directories."""
    (repo / ".venv").write_text("not a venv\n")

    workdir = manager.ensure("WRK-001", "agt-1", repo)

    assert not (workdir / ".venv").exists()
    assert not (workdir / ".venv").is_symlink()


def test_ensure_with_branch_name_also_symlinks_devtime_artifacts(
    manager: GitWorktreeManager, repo: Path
) -> None:
    """Named-branch worktrees follow the same convenience path."""
    venv = repo / ".venv"
    venv.mkdir()

    workdir = manager.ensure(
        "WRK-001", "agt-1", repo, branch_name="feature-x"
    )

    link = workdir / ".venv"
    assert link.is_symlink()
    assert link.resolve() == venv.resolve()


def test_ensure_symlinks_top_level_env_local_into_worktree(
    manager: GitWorktreeManager, repo: Path
) -> None:
    """``.env.local`` at the source root is gitignored; without a
    symlink the agent's app can't see secrets. Vite's compile-time
    ``define`` substitution is especially nasty here — empty values get
    baked in silently if the file is missing at server start."""
    env_file = repo / ".env.local"
    env_file.write_text("OPENAI_API_KEY=sk-test\nSUPABASE_URL=https://x\n")

    workdir = manager.ensure("WRK-001", "agt-1", repo)

    link = workdir / ".env.local"
    assert link.is_symlink()
    assert link.resolve() == env_file.resolve()
    assert "OPENAI_API_KEY=sk-test" in link.read_text()


def test_ensure_symlinks_all_env_file_variants(
    manager: GitWorktreeManager, repo: Path
) -> None:
    """The full ``.env*`` set Vite / pydantic-settings / Next.js look
    for, not just ``.env.local`` — repos commonly mix several."""
    for name in (".env", ".env.development", ".env.production"):
        (repo / name).write_text(f"# {name}\n")

    workdir = manager.ensure("WRK-001", "agt-1", repo)

    for name in (".env", ".env.development", ".env.production"):
        link = workdir / name
        assert link.is_symlink(), f"missing symlink for {name}"
        assert link.resolve() == (repo / name).resolve()


def test_ensure_symlinks_nested_env_file_for_monorepos(
    manager: GitWorktreeManager, repo: Path
) -> None:
    """``backend/.env.local`` + ``frontend/.env.local`` cover the
    common monorepo split (per-component env files). Same scope as the
    nested venv / node_modules case — only fires when the subdir
    already exists in the worktree (has tracked files)."""
    backend = repo / "backend"
    backend.mkdir()
    (backend / "main.py").write_text("pass\n")
    _git(repo, "add", "backend/main.py")
    _git(repo, "commit", "-q", "-m", "add backend")

    backend_env = backend / ".env.local"
    backend_env.write_text("BACKEND=1\n")

    frontend = repo / "frontend"
    frontend.mkdir()
    (frontend / "index.html").write_text("<html></html>\n")
    _git(repo, "add", "frontend/index.html")
    _git(repo, "commit", "-q", "-m", "add frontend")

    frontend_env = frontend / ".env.local"
    frontend_env.write_text("VITE_API_URL=http://x\n")

    workdir = manager.ensure("WRK-001", "agt-1", repo)

    backend_link = workdir / "backend" / ".env.local"
    assert backend_link.is_symlink()
    assert backend_link.resolve() == backend_env.resolve()

    frontend_link = workdir / "frontend" / ".env.local"
    assert frontend_link.is_symlink()
    assert frontend_link.resolve() == frontend_env.resolve()


def test_ensure_skips_env_file_when_source_is_missing(
    manager: GitWorktreeManager, repo: Path
) -> None:
    """No ``.env*`` at source → no symlink in the worktree. Quiet
    no-op, no error."""
    workdir = manager.ensure("WRK-001", "agt-1", repo)

    for name in (".env", ".env.local", ".env.development"):
        assert not (workdir / name).exists()
        assert not (workdir / name).is_symlink()


def test_ensure_skips_env_file_when_source_path_is_a_directory(
    manager: GitWorktreeManager, repo: Path
) -> None:
    """A directory named ``.env.local`` (unusual but possible) must
    not be mirrored — the file-variant of the symlink helper only
    mirrors regular files, symmetric with how ``_maybe_symlink``
    refuses to mirror anything that isn't a directory."""
    (repo / ".env.local").mkdir()

    workdir = manager.ensure("WRK-001", "agt-1", repo)

    assert not (workdir / ".env.local").exists()
    assert not (workdir / ".env.local").is_symlink()


def test_ensure_forked_symlinks_devtime_artifacts(
    manager: GitWorktreeManager, repo: Path
) -> None:
    """The fork path runs through ``ensure_forked`` (handoff into a new
    agent). It also overlays uncommitted state — the venv symlink should
    coexist with that overlay."""
    venv = repo / ".venv"
    venv.mkdir()

    manager.ensure("WRK-001", "agt-1", repo)
    forked = manager.ensure_forked("WRK-001", "agt-2", "agt-1", repo)

    link = forked / ".venv"
    assert link.is_symlink()
    assert link.resolve() == venv.resolve()
