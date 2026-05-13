"""FsShareProvisioner — real filesystem behaviour.

Operates against ``tmp_path`` so each test gets an isolated workspace
root. Covers the mount-conflict policy, idempotent re-mount, custom-
location external symlinks, and the stop-sharing vs delete-contents
distinction.
"""

from __future__ import annotations

import os
from pathlib import Path

import pytest

from src.domain.sharedfolders.ports import MountConflict
from src.infrastructure.filesystem.paths import WorkspacePaths
from src.infrastructure.filesystem.share_provisioner import FsShareProvisioner


def _provisioner(tmp_path: Path) -> tuple[FsShareProvisioner, WorkspacePaths]:
    paths = WorkspacePaths(workspace_root=tmp_path)
    return FsShareProvisioner(paths), paths


def _make_worktree(paths: WorkspacePaths, work: str, agent: str) -> Path:
    wt = paths.worktree_dir(work, agent)
    wt.mkdir(parents=True)
    return wt


# ----- canonical-side -----


def test_ensure_canonical_dir_creates_real_directory(tmp_path: Path) -> None:
    prov, paths = _provisioner(tmp_path)
    out = prov.ensure_canonical_dir("PRJ-001", "shr-1")
    assert out == paths.project_share_dir("PRJ-001", "shr-1")
    assert out.is_dir()
    assert not out.is_symlink()


def test_ensure_canonical_dir_idempotent(tmp_path: Path) -> None:
    prov, _ = _provisioner(tmp_path)
    prov.ensure_canonical_dir("PRJ-001", "shr-1")
    prov.ensure_canonical_dir("PRJ-001", "shr-1")  # no exception


def test_link_canonical_to_external_creates_symlink(tmp_path: Path) -> None:
    prov, paths = _provisioner(tmp_path)
    real = tmp_path / "user_folder"
    real.mkdir()
    canonical = prov.link_canonical_to_external("PRJ-001", "shr-1", real)
    assert canonical.is_symlink()
    assert Path(os.readlink(canonical)) == real


def test_link_canonical_replaces_empty_default_dir(tmp_path: Path) -> None:
    """If ensure_canonical_dir ran first (created an empty dir), the
    subsequent link call replaces the dir with a symlink. Common when
    "+ New + custom location" runs both calls in sequence."""
    prov, _ = _provisioner(tmp_path)
    prov.ensure_canonical_dir("PRJ-001", "shr-1")
    real = tmp_path / "user_folder"
    real.mkdir()
    canonical = prov.link_canonical_to_external("PRJ-001", "shr-1", real)
    assert canonical.is_symlink()


def test_link_canonical_refuses_non_empty_default_dir(tmp_path: Path) -> None:
    """If the canonical dir has content (shouldn't happen in the happy
    path, but could if something raced), refuse to symlink rather than
    losing data."""
    prov, paths = _provisioner(tmp_path)
    prov.ensure_canonical_dir("PRJ-001", "shr-1")
    (paths.project_share_dir("PRJ-001", "shr-1") / "stowaway").write_text("x")
    real = tmp_path / "user_folder"
    real.mkdir()
    with pytest.raises(FileExistsError):
        prov.link_canonical_to_external("PRJ-001", "shr-1", real)


def test_link_canonical_refuses_self_referential_real_path(
    tmp_path: Path,
) -> None:
    """Guard against the user (or a buggy caller) picking the share's
    own canonical path as the real_path — that would create a symlink
    pointing at itself."""
    prov, paths = _provisioner(tmp_path)
    canonical = paths.project_share_dir("PRJ-001", "shr-1")
    canonical.parent.mkdir(parents=True)
    canonical.mkdir()
    with pytest.raises(ValueError, match="self-referential"):
        prov.link_canonical_to_external("PRJ-001", "shr-1", canonical)


def test_link_canonical_refuses_real_path_inside_works_tree(
    tmp_path: Path,
) -> None:
    """Pointing a share at a path inside ``<workspace>/works/`` (i.e.
    inside any agent worktree) would loop when that worktree later
    mounts the share at the same name."""
    prov, paths = _provisioner(tmp_path)
    inside_worktree = paths.worktree_dir("WRK-001", "agt-1") / "bmad"
    inside_worktree.mkdir(parents=True)
    with pytest.raises(ValueError, match="inside the agent worktree tree"):
        prov.link_canonical_to_external("PRJ-001", "shr-1", inside_worktree)


def test_remove_canonical_drops_symlink_without_following(
    tmp_path: Path,
) -> None:
    """Custom-location share: remove the symlink only; the user's real
    folder MUST NOT be touched."""
    prov, _ = _provisioner(tmp_path)
    real = tmp_path / "user_folder"
    real.mkdir()
    (real / "important.txt").write_text("don't delete me")
    prov.link_canonical_to_external("PRJ-001", "shr-1", real)
    prov.remove_canonical("PRJ-001", "shr-1", delete_contents=True)
    # symlink gone, real folder intact
    assert real.exists()
    assert (real / "important.txt").read_text() == "don't delete me"


def test_remove_canonical_keeps_default_dir_when_not_empty(
    tmp_path: Path,
) -> None:
    """Stop-sharing on a default-location share with content: leave
    the dir on disk for the user to inspect / re-adopt."""
    prov, paths = _provisioner(tmp_path)
    prov.ensure_canonical_dir("PRJ-001", "shr-1")
    canonical = paths.project_share_dir("PRJ-001", "shr-1")
    (canonical / "story.md").write_text("hello")
    prov.remove_canonical("PRJ-001", "shr-1", delete_contents=False)
    assert canonical.exists()
    assert (canonical / "story.md").exists()


def test_remove_canonical_with_delete_contents_wipes_dir(
    tmp_path: Path,
) -> None:
    prov, paths = _provisioner(tmp_path)
    prov.ensure_canonical_dir("PRJ-001", "shr-1")
    (paths.project_share_dir("PRJ-001", "shr-1") / "x").write_text("x")
    prov.remove_canonical("PRJ-001", "shr-1", delete_contents=True)
    assert not paths.project_share_dir("PRJ-001", "shr-1").exists()


# ----- worktree-side -----


def test_mount_creates_symlink_into_canonical(tmp_path: Path) -> None:
    prov, paths = _provisioner(tmp_path)
    canonical = prov.ensure_canonical_dir("PRJ-001", "shr-1")
    _make_worktree(paths, "WRK-001", "agt-1")
    prov.mount_in_worktree(
        "WRK-001", "agt-1", "_bmad-output", canonical
    )
    mounted = paths.worktree_dir("WRK-001", "agt-1") / "_bmad-output"
    assert mounted.is_symlink()
    assert Path(os.readlink(mounted)) == canonical


def test_mount_creates_nested_parent_directories(tmp_path: Path) -> None:
    """Mount paths like ``docs/runbooks/`` need their parents created
    first since the worktree's tracked content won't include them."""
    prov, paths = _provisioner(tmp_path)
    canonical = prov.ensure_canonical_dir("PRJ-001", "shr-1")
    _make_worktree(paths, "WRK-001", "agt-1")
    prov.mount_in_worktree(
        "WRK-001", "agt-1", "docs/runbooks", canonical
    )
    assert (paths.worktree_dir("WRK-001", "agt-1") / "docs" / "runbooks").is_symlink()


def test_mount_is_idempotent_when_symlink_already_correct(
    tmp_path: Path,
) -> None:
    prov, paths = _provisioner(tmp_path)
    canonical = prov.ensure_canonical_dir("PRJ-001", "shr-1")
    _make_worktree(paths, "WRK-001", "agt-1")
    prov.mount_in_worktree("WRK-001", "agt-1", "_bmad", canonical)
    prov.mount_in_worktree("WRK-001", "agt-1", "_bmad", canonical)
    # No exception, still a symlink to the same target.
    mounted = paths.worktree_dir("WRK-001", "agt-1") / "_bmad"
    assert Path(os.readlink(mounted)) == canonical


def test_mount_refuses_when_path_exists_as_real_directory(
    tmp_path: Path,
) -> None:
    """The conflict-handling rule from STORY-032 design notes: if the
    worktree path already has real content, surface MountConflict so
    the agent's transcript shows a warning and the user can resolve."""
    prov, paths = _provisioner(tmp_path)
    canonical = prov.ensure_canonical_dir("PRJ-001", "shr-1")
    worktree = _make_worktree(paths, "WRK-001", "agt-1")
    pre_existing = worktree / "_bmad"
    pre_existing.mkdir()
    (pre_existing / "stuff.md").write_text("preexisting")

    with pytest.raises(MountConflict):
        prov.mount_in_worktree("WRK-001", "agt-1", "_bmad", canonical)
    # Preexisting content was NOT touched.
    assert (pre_existing / "stuff.md").read_text() == "preexisting"


def test_mount_replaces_symlink_pointing_at_wrong_target(
    tmp_path: Path,
) -> None:
    """If the worktree already has a symlink at the mount path but it
    points somewhere stale (different canonical), replace it. Common
    after the user deletes + recreates a share with the same mount."""
    prov, paths = _provisioner(tmp_path)
    canonical = prov.ensure_canonical_dir("PRJ-001", "shr-1")
    worktree = _make_worktree(paths, "WRK-001", "agt-1")
    stale = tmp_path / "stale"
    stale.mkdir()
    (worktree / "_bmad").symlink_to(stale, target_is_directory=True)

    prov.mount_in_worktree("WRK-001", "agt-1", "_bmad", canonical)
    assert Path(os.readlink(worktree / "_bmad")) == canonical


def test_mount_refuses_when_worktree_resolves_outside_workspace(
    tmp_path: Path,
) -> None:
    """Defense in depth: if some ancestor of the worktree dir was made
    a symlink that redirects outside the workspace (the historical bug
    that turned the user's main repo's ``bmad/`` into a self-link to a
    worktree), we must refuse to write rather than scribble outside
    Atelier."""
    # Need a workspace_root that has a sibling we can call "external" —
    # tmp_path on its own would put external INSIDE the workspace.
    workspace_root = tmp_path / "workspace"
    workspace_root.mkdir()
    external = tmp_path / "outside_workspace_repo"
    external.mkdir()
    paths = WorkspacePaths(workspace_root=workspace_root)
    prov = FsShareProvisioner(paths)

    canonical = prov.ensure_canonical_dir("PRJ-001", "shr-1")
    # Plant the worktree dir as a symlink to `external`. The
    # provisioner's mount routine calls .resolve() on link_path.parent
    # — when the worktree is a symlink to elsewhere, that resolves
    # outside workspace_root.
    worktree_parent = paths.worktree_dir("WRK-001", "agt-1").parent
    worktree_parent.mkdir(parents=True)
    (worktree_parent / "agt-1").symlink_to(external, target_is_directory=True)

    with pytest.raises(MountConflict, match="outside workspace"):
        prov.mount_in_worktree("WRK-001", "agt-1", "bmad", canonical)
    # Critically: nothing was written inside `external` — the original
    # folder is left alone.
    assert list(external.iterdir()) == []


def test_mount_refuses_when_target_contains_mount_parent(
    tmp_path: Path,
) -> None:
    """If the share's resolved target is an ancestor of the worktree
    parent (or equal to it), the symlink would loop instantly. Refuse."""
    prov, paths = _provisioner(tmp_path)
    worktree = _make_worktree(paths, "WRK-001", "agt-1")
    # Point a custom-location share at the worktree's parent. The
    # canonical resolves into the works tree → mount would create
    # ``<worktree>/sub`` symlink → resolves up to the worktree itself.
    custom = worktree.parent  # ``<workspace>/works/WRK-001/worktrees/``
    canonical = prov.share_canonical_path("PRJ-001", "shr-1")
    canonical.parent.mkdir(parents=True)
    canonical.symlink_to(custom, target_is_directory=True)

    with pytest.raises(MountConflict, match="symlink loop"):
        prov.mount_in_worktree("WRK-001", "agt-1", "sub", canonical)


def test_unmount_removes_symlink_only(tmp_path: Path) -> None:
    prov, paths = _provisioner(tmp_path)
    canonical = prov.ensure_canonical_dir("PRJ-001", "shr-1")
    _make_worktree(paths, "WRK-001", "agt-1")
    prov.mount_in_worktree("WRK-001", "agt-1", "_bmad", canonical)
    prov.unmount_from_worktree("WRK-001", "agt-1", "_bmad")
    assert not (paths.worktree_dir("WRK-001", "agt-1") / "_bmad").exists()


def test_unmount_leaves_regular_directory_alone(tmp_path: Path) -> None:
    """Defensive: if the path is somehow a real directory (e.g. stale
    pre-share content), unmount must not delete it. Only symlinks get
    removed."""
    prov, paths = _provisioner(tmp_path)
    worktree = _make_worktree(paths, "WRK-001", "agt-1")
    regular = worktree / "_bmad"
    regular.mkdir()
    (regular / "x.md").write_text("y")
    prov.unmount_from_worktree("WRK-001", "agt-1", "_bmad")
    assert regular.exists()
    assert (regular / "x.md").read_text() == "y"
