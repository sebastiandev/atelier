"""FsShareProvisioner — filesystem ops for shared folders.

Implements ``ShareProvisioner`` against the local filesystem. Two
surfaces:

  1. Canonical-side (project dir): ``ensure_canonical_dir``,
     ``link_canonical_to_external``, ``remove_canonical``.
  2. Worktree-side (per agent): ``mount_in_worktree``,
     ``unmount_from_worktree``.

Conflict policy (mount_in_worktree): refuse if the target path
already exists as a regular dir/file. Idempotent when it's already a
symlink to the right place. Broken symlinks get replaced cleanly.

See ``_bmad-output/stories/STORY-032.md`` § "Design notes — symlink
semantics + conflict handling" for the rationale.
"""

from __future__ import annotations

import os
import shutil
from pathlib import Path

from src.domain.sharedfolders.ports import MountConflict
from src.infrastructure.filesystem.paths import WorkspacePaths


class FsShareProvisioner:
    def __init__(self, paths: WorkspacePaths) -> None:
        self._paths = paths

    # ----- canonical-side -----

    def ensure_canonical_dir(
        self, project_slug: str, share_slug: str
    ) -> Path:
        canonical = self._paths.project_share_dir(project_slug, share_slug)
        # If the canonical path is already a symlink (set up by a prior
        # link_canonical_to_external), leave it alone — re-mkdir would
        # raise FileExistsError on the symlink even with exist_ok=True
        # if the link target doesn't exist yet.
        if canonical.is_symlink():
            return canonical
        canonical.mkdir(parents=True, exist_ok=True)
        return canonical

    def share_canonical_path(
        self, project_slug: str, share_slug: str
    ) -> Path:
        return self._paths.project_share_dir(project_slug, share_slug)

    def link_canonical_to_external(
        self, project_slug: str, share_slug: str, real_path: Path
    ) -> Path:
        canonical = self._paths.project_share_dir(project_slug, share_slug)
        canonical.parent.mkdir(parents=True, exist_ok=True)
        # Cycle guards. Run before any state-mutating ops so a bad input
        # leaves the canonical untouched. ``real_path`` is required to
        # exist by both callers (create_new mkdir's it; create_from_
        # existing checks is_dir), so .resolve() chases through any
        # symlink chain to the physical target.
        real_resolved = real_path.resolve()
        canonical_target = canonical.parent.resolve() / canonical.name
        if real_resolved == canonical_target:
            raise ValueError(
                f"share source {real_path} resolves to its own canonical path "
                f"({canonical}); refusing to create a self-referential symlink"
            )
        works_dir = self._paths.works_dir()
        works_resolved = works_dir.resolve() if works_dir.exists() else works_dir
        if real_resolved == works_resolved or works_resolved in real_resolved.parents:
            raise ValueError(
                f"share source {real_path} is inside the agent worktree tree "
                f"({works_resolved}); a share that points into a worktree would "
                f"loop when that worktree later mounts the share"
            )
        # If a real dir is sitting at canonical (because we just created
        # it in ensure_canonical_dir and now the user picked a custom
        # location), only replace it when it's empty. Refuse otherwise
        # — losing user data silently would be the worst failure mode.
        if canonical.exists() and not canonical.is_symlink():
            if any(canonical.iterdir()):
                raise FileExistsError(
                    f"cannot symlink canonical path to {real_path}: "
                    f"{canonical} already exists with content"
                )
            canonical.rmdir()
        elif canonical.is_symlink():
            canonical.unlink()
        canonical.symlink_to(real_path, target_is_directory=True)
        return canonical

    def remove_canonical(
        self,
        project_slug: str,
        share_slug: str,
        *,
        delete_contents: bool,
    ) -> None:
        canonical = self._paths.project_share_dir(project_slug, share_slug)
        if canonical.is_symlink():
            # Custom-location share — never follow the symlink, just drop it.
            canonical.unlink()
            return
        if not canonical.exists():
            return
        if delete_contents:
            shutil.rmtree(canonical)
        else:
            # Stop-sharing on a default-location share: only drop the dir
            # if it's empty so we never surprise-delete user content.
            try:
                canonical.rmdir()
            except OSError:
                # Non-empty default-location dir: leave it. The share
                # registration is gone; data remains under
                # ~/Atelier/projects/<PRJ>/shared/<slug>/ for the user
                # to inspect or re-adopt.
                pass

    # ----- worktree-side -----

    def mount_in_worktree(
        self,
        work_slug: str,
        agent_slug: str,
        mount_path: str,
        target: Path,
    ) -> None:
        worktree = self._paths.worktree_dir(work_slug, agent_slug)
        link_path = worktree / mount_path
        # Parent dir must exist (mount paths can be nested, e.g.
        # ``docs/runbooks/``); idempotent.
        link_path.parent.mkdir(parents=True, exist_ok=True)
        if link_path.is_symlink():
            current_target = os.readlink(link_path)
            if Path(current_target) == target:
                return  # already mounted correctly
            link_path.unlink()
        elif link_path.exists():
            raise MountConflict(
                f"{link_path} already exists with content; refusing to mount"
            )
        # Cycle guards. Cheap to evaluate and cheap-to-recover-from
        # (caller logs + skips the share for this agent), and they fail
        # CLOSED rather than scribbling outside the workspace or
        # creating a self-referential link.
        workspace_root = self._paths.workspace_root.resolve()
        parent_resolved = link_path.parent.resolve()
        if parent_resolved != workspace_root and workspace_root not in parent_resolved.parents:
            raise MountConflict(
                f"mount parent {parent_resolved} resolves outside workspace "
                f"({workspace_root}); refusing to write symlink — an ancestor "
                f"of the worktree dir is redirecting through a symlink"
            )
        target_resolved = target.resolve() if target.exists() else target.absolute()
        if parent_resolved == target_resolved or target_resolved in parent_resolved.parents:
            raise MountConflict(
                f"mount parent {parent_resolved} is inside share target "
                f"{target_resolved}; refusing to create symlink loop"
            )
        link_path.symlink_to(target, target_is_directory=True)

    def unmount_from_worktree(
        self, work_slug: str, agent_slug: str, mount_path: str
    ) -> None:
        worktree = self._paths.worktree_dir(work_slug, agent_slug)
        link_path = worktree / mount_path
        if link_path.is_symlink():
            link_path.unlink()


__all__ = ["FsShareProvisioner"]
