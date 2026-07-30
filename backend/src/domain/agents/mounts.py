"""Helpers for mounting shared folders into agent worktrees."""

from __future__ import annotations

import logging
from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path

from src.domain.sharedfolders.dtos import ShareSummary
from src.domain.sharedfolders.ports import (
    MountConflict,
    SharedFolderStore,
    ShareProvisioner,
)
from src.domain.worktrees import WorktreeManager

_log = logging.getLogger(__name__)


@dataclass(frozen=True)
class MountedProjectShares:
    """Mounted project shares split by audience."""

    summaries: tuple[ShareSummary, ...] = ()
    writable_roots: tuple[Path, ...] = ()


def mount_project_shares(
    *,
    sharestore: SharedFolderStore,
    provisioner: ShareProvisioner,
    project_slug: str | None,
    work_slug: str,
    agent_slug: str,
) -> MountedProjectShares:
    """Mount each project share into the agent's worktree as a symlink.

    Preconditions: ``work_slug`` and ``agent_slug`` identify a provisioned
    worktree.
    Postconditions: returns prompt summaries and sandbox writable roots for
    shares that mounted successfully; conflicts are logged and skipped.
    """
    if project_slug is None:
        return MountedProjectShares()
    summaries: list[ShareSummary] = []
    writable_roots: list[Path] = []
    for share in sharestore.list_for_project(project_slug):
        if share.slug is None:
            continue
        target = provisioner.share_canonical_path(project_slug, share.slug)
        try:
            provisioner.mount_in_worktree(
                work_slug, agent_slug, share.mount_path, target
            )
        except MountConflict as exc:
            _log.warning(
                "share %s not mounted for %s/%s: %s",
                share.slug,
                work_slug,
                agent_slug,
                exc,
            )
            continue
        summaries.append(ShareSummary(name=share.name, mount_path=share.mount_path))
        writable_roots.append(target.resolve(strict=False))
    return MountedProjectShares(
        summaries=tuple(summaries),
        writable_roots=tuple(dict.fromkeys(writable_roots)),
    )


def agent_writable_roots(
    mounted_shares: MountedProjectShares,
    worktree_manager: WorktreeManager,
    workdir: Path,
) -> tuple[Path, ...]:
    """Return sandbox roots for one agent runtime.

    Preconditions: ``workdir`` is the agent worktree path.
    Postconditions: returned paths include mounted share targets and the
    provider-specific writable roots for the worktree.
    """
    return tuple(
        dict.fromkeys(
            (
                *mounted_shares.writable_roots,
                *worktree_manager.sandbox_writable_roots(workdir),
            )
        )
    )


def mount_work_chat_contexts(
    *,
    workdir: Path,
    folders: Sequence[object],
) -> MountedProjectShares:
    """Mount work chat context folders into an agent worktree.

    Preconditions: ``folders`` may contain context folder DTOs with name,
    mount path, and absolute target path.
    Postconditions: successfully mounted folders are returned as prompt
    summaries and writable roots.
    """
    summaries: list[ShareSummary] = []
    writable_roots: list[Path] = []
    for folder in folders:
        name = getattr(folder, "name", None)
        mount_path = getattr(folder, "mount_path", None)
        target = getattr(folder, "absolute_path", None)
        if not isinstance(name, str) or not isinstance(mount_path, str):
            continue
        if not isinstance(target, Path):
            continue
        link_path = workdir / mount_path
        try:
            link_path.parent.mkdir(parents=True, exist_ok=True)
            if link_path.is_symlink():
                if link_path.resolve(strict=False) == target.resolve(strict=False):
                    pass
                else:
                    link_path.unlink()
                    link_path.symlink_to(target, target_is_directory=True)
            elif link_path.exists():
                _log.warning(
                    "chat context %s not mounted at %s: path already exists",
                    name,
                    link_path,
                )
                continue
            else:
                link_path.symlink_to(target, target_is_directory=True)
        except OSError as exc:
            _log.warning(
                "chat context %s not mounted at %s: %s",
                name,
                link_path,
                exc,
            )
            continue
        summaries.append(ShareSummary(name=name, mount_path=mount_path))
        writable_roots.append(target.resolve(strict=False))
    return MountedProjectShares(
        summaries=tuple(summaries),
        writable_roots=tuple(dict.fromkeys(writable_roots)),
    )


def merge_mounted_shares(
    *groups: MountedProjectShares,
) -> MountedProjectShares:
    """Merge mounted share groups while preserving order.

    Preconditions: groups contain prompt summaries and writable roots.
    Postconditions: duplicate writable roots are removed.
    """
    summaries: list[ShareSummary] = []
    writable_roots: list[Path] = []
    for group in groups:
        summaries.extend(group.summaries)
        writable_roots.extend(group.writable_roots)
    return MountedProjectShares(
        summaries=tuple(summaries),
        writable_roots=tuple(dict.fromkeys(writable_roots)),
    )


__all__ = [
    "MountedProjectShares",
    "agent_writable_roots",
    "merge_mounted_shares",
    "mount_project_shares",
    "mount_work_chat_contexts",
]
