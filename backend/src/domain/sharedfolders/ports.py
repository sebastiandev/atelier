"""Ports for the SharedFolderStore boundary.

Same split as ProjectStore: a public ``SharedFolderStore`` the
application layer depends on, decomposed into ``ShareRepository``
(SQL) + ``ShareProvisioner`` (filesystem). Domain stays framework-free.
"""

from __future__ import annotations

from pathlib import Path
from typing import Protocol

from src.domain.models import SharedFolder
from src.domain.sharedfolders.dtos import (
    CreateExistingShareRequest,
    CreateNewShareRequest,
    ShareRecord,
    UpdateShareRequest,
)


class MountConflict(RuntimeError):
    """The provisioner refused a mount because the worktree path
    already exists with content. Surfaced to the agent transcript as a
    warning; the user resolves by removing the conflicting path."""


class SharedFolderStore(Protocol):
    """Public boundary for project-scoped shared folders."""

    def create_new(self, req: CreateNewShareRequest) -> ShareRecord: ...
    def create_from_existing(
        self, req: CreateExistingShareRequest
    ) -> ShareRecord: ...
    def list_for_project(self, project_slug: str) -> list[SharedFolder]: ...
    def get(self, project_slug: str, share_slug: str) -> ShareRecord | None: ...
    def rename(self, req: UpdateShareRequest) -> ShareRecord: ...
    def stop_sharing(self, project_slug: str, share_slug: str) -> None:
        """Remove the registration + the Atelier-side symlink. Real
        folder is left untouched on disk."""

    def delete_contents(self, project_slug: str, share_slug: str) -> None:
        """Additionally remove the canonical path's contents. Refused
        for custom-location shares — we never delete data we don't
        own."""


class ShareRepository(Protocol):
    """SQL-side row operations."""

    def add(self, share: SharedFolder) -> SharedFolder: ...
    def update(self, share: SharedFolder) -> SharedFolder: ...
    def get_by_slug(self, share_slug: str) -> SharedFolder | None: ...
    def get_by_mount_path(
        self, project_id: int, mount_path: str
    ) -> SharedFolder | None: ...
    def list_for_project(self, project_id: int) -> list[SharedFolder]: ...
    def delete(self, share_slug: str) -> None: ...


class ShareProvisioner(Protocol):
    """Filesystem-side operations for shares.

    Decomposed into three concerns:
      1. Canonical directory under the workspace (created on share
         creation; can be a real dir or a symlink to a custom
         ``real_path``).
      2. Worktree-side mount symlinks (per agent, per share).
      3. Cleanup on stop-sharing and delete-contents.
    """

    # --- canonical-side ---

    def ensure_canonical_dir(
        self, project_slug: str, share_slug: str
    ) -> Path:
        """Create ``<workspace>/projects/<PRJ>/shared/<share>/`` as a
        real directory. Idempotent. Returns the absolute path."""

    def link_canonical_to_external(
        self, project_slug: str, share_slug: str, real_path: Path
    ) -> Path:
        """Make the canonical path a symlink to ``real_path``. Used by
        the "+ Add existing" flow and the "+ New" with custom-location
        flow. ``real_path`` must already exist as a directory.
        Returns the canonical path."""

    def share_canonical_path(
        self, project_slug: str, share_slug: str
    ) -> Path:
        """Return the canonical absolute path for a share without
        creating or modifying anything. Useful for callers that need
        the path as a symlink target."""

    def remove_canonical(
        self, project_slug: str, share_slug: str, *, delete_contents: bool
    ) -> None:
        """Drop the canonical path. If ``delete_contents`` and it's a
        real directory, ``rmtree`` it; if it's a symlink, just
        ``unlink`` it (never delete a custom-location share's data).
        Safe if absent."""

    # --- worktree-side ---

    def mount_in_worktree(
        self, work_slug: str, agent_slug: str, mount_path: str, target: Path
    ) -> None:
        """Create a symlink at ``<worktree>/<mount_path>/`` pointing to
        ``target``. Creates parent dirs as needed. Raises
        ``MountConflict`` if the target path already exists with
        content (regular dir/file). Idempotent when the existing path
        is already a symlink to ``target``."""

    def unmount_from_worktree(
        self, work_slug: str, agent_slug: str, mount_path: str
    ) -> None:
        """Remove the symlink at ``<worktree>/<mount_path>/`` if it
        exists and is a symlink. Leaves regular dirs/files alone."""


__all__ = [
    "MountConflict",
    "ShareProvisioner",
    "ShareRepository",
    "SharedFolderStore",
]
