"""Unit tests for ``doc_state`` — pure path classification + git probing.

The location classifier is fully deterministic (path comparison only).
The git probe shells out to ``git`` and is exercised against real
repos created in ``tmp_path`` so the tests cover the end-to-end shape
the route relies on.
"""

from __future__ import annotations

import subprocess
from pathlib import Path

from src.domain.agents.doc_state import classify_location, git_state


# ---------------------------------------------------------------------------
# classify_location
# ---------------------------------------------------------------------------


def test_classify_location_returns_worktree_for_path_inside_worktree(tmp_path: Path) -> None:
    worktree = tmp_path / "wt"
    worktree.mkdir()
    doc = worktree / "design.md"
    doc.write_text("x")
    assert classify_location(doc, worktree=worktree, share_roots=[]) == "worktree"


def test_classify_location_returns_shared_for_path_inside_share_root(tmp_path: Path) -> None:
    share = tmp_path / "share"
    share.mkdir()
    doc = share / "plan.md"
    doc.write_text("x")
    assert (
        classify_location(doc, worktree=None, share_roots=[share]) == "shared"
    )


def test_classify_location_prefers_shared_for_doc_accessed_via_worktree_symlink(
    tmp_path: Path,
) -> None:
    """Default flow: shares are mounted into the worktree at
    ``./shares/<mount>/`` via a symlink. The resolved real path lives
    in the share, so we want ``shared`` even though the supplied path
    descends from the worktree."""
    worktree = tmp_path / "wt"
    worktree.mkdir()
    share = tmp_path / "share"
    share.mkdir()
    (share / "plan.md").write_text("x")
    # Mimic the share_provisioner symlink under the worktree.
    mount = worktree / "shares" / "bmad"
    mount.parent.mkdir(parents=True)
    mount.symlink_to(share)

    doc_via_symlink = mount / "plan.md"
    assert (
        classify_location(doc_via_symlink, worktree=worktree, share_roots=[share])
        == "shared"
    )


def test_classify_location_returns_none_when_outside_all_roots(tmp_path: Path) -> None:
    worktree = tmp_path / "wt"
    worktree.mkdir()
    other = tmp_path / "elsewhere"
    other.mkdir()
    doc = other / "stray.md"
    doc.write_text("x")
    assert classify_location(doc, worktree=worktree, share_roots=[]) is None


def test_classify_location_handles_missing_path_gracefully(tmp_path: Path) -> None:
    """Stale rows (file deleted on disk) shouldn't crash the route —
    the classifier returns ``None`` and the FE just hides the chip."""
    worktree = tmp_path / "wt"
    worktree.mkdir()
    missing = worktree / "gone.md"
    # On most platforms .resolve() on a missing path returns the would-be
    # path rather than raising, so classify still returns "worktree" —
    # which is the right answer (the parent is the worktree, the file
    # just isn't there yet/anymore). The contract here is: don't crash.
    result = classify_location(missing, worktree=worktree, share_roots=[])
    assert result in ("worktree", None)


# ---------------------------------------------------------------------------
# git_state
# ---------------------------------------------------------------------------


def _init_repo(repo: Path) -> None:
    repo.mkdir(parents=True, exist_ok=True)
    subprocess.run(["git", "init", "-q"], cwd=repo, check=True)
    # Configure a local identity so commits don't error on machines
    # without a global git identity.
    subprocess.run(["git", "config", "user.email", "t@t"], cwd=repo, check=True)
    subprocess.run(["git", "config", "user.name", "t"], cwd=repo, check=True)
    subprocess.run(["git", "config", "commit.gpgsign", "false"], cwd=repo, check=True)


def test_git_state_returns_none_for_path_outside_any_repo(tmp_path: Path) -> None:
    doc = tmp_path / "loose.md"
    doc.write_text("hi")
    assert git_state(doc) is None


def test_git_state_returns_committed_when_file_matches_head(tmp_path: Path) -> None:
    _init_repo(tmp_path)
    doc = tmp_path / "design.md"
    doc.write_text("# v1")
    subprocess.run(["git", "add", "design.md"], cwd=tmp_path, check=True)
    subprocess.run(
        ["git", "commit", "-q", "-m", "init"], cwd=tmp_path, check=True
    )
    assert git_state(doc) == "committed"


def test_git_state_returns_uncommitted_when_file_modified(tmp_path: Path) -> None:
    _init_repo(tmp_path)
    doc = tmp_path / "design.md"
    doc.write_text("# v1")
    subprocess.run(["git", "add", "design.md"], cwd=tmp_path, check=True)
    subprocess.run(
        ["git", "commit", "-q", "-m", "init"], cwd=tmp_path, check=True
    )
    doc.write_text("# v2 changed")
    assert git_state(doc) == "uncommitted"


def test_git_state_returns_uncommitted_for_brand_new_untracked_file(
    tmp_path: Path,
) -> None:
    _init_repo(tmp_path)
    # Need at least one commit so HEAD exists; ``git status`` works
    # without it but the typical agent flow has prior history.
    seed = tmp_path / ".keep"
    seed.write_text("")
    subprocess.run(["git", "add", ".keep"], cwd=tmp_path, check=True)
    subprocess.run(["git", "commit", "-q", "-m", "init"], cwd=tmp_path, check=True)

    new_doc = tmp_path / "new.md"
    new_doc.write_text("# fresh")
    assert git_state(new_doc) == "uncommitted"


def test_git_state_handles_missing_file(tmp_path: Path) -> None:
    _init_repo(tmp_path)
    missing = tmp_path / "gone.md"
    assert git_state(missing) is None
