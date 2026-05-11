"""SharedFolderStoreService — composes repository + provisioner.

Ordering: persist DB first, then filesystem. A crash between the two
leaves a row pointing at a non-existent canonical path; the next
``ensure_canonical_dir`` call (on next access) re-creates it. We don't
go through the reconcile-on-startup pattern that workstore uses because
shares have no per-project on-disk JSON to reconcile against — the row
IS the source of truth.
"""

from __future__ import annotations

from collections.abc import Callable
from datetime import UTC, datetime
from pathlib import Path
from threading import RLock

from src.domain.models import SharedFolder
from src.domain.sharedfolders.dtos import (
    CreateExistingShareRequest,
    CreateNewShareRequest,
    ShareRecord,
    UpdateShareRequest,
)
from src.domain.sharedfolders.ports import (
    ShareProvisioner,
    ShareRepository,
)
from src.domain.sharedfolders.validation import validate_mount_path

Clock = Callable[[], datetime]


def _utc_now() -> datetime:
    return datetime.now(UTC)


class ShareNotFound(ValueError):
    """The (project_slug, share_slug) pair doesn't resolve to a share."""


class MountPathConflict(ValueError):
    """Another share in the same project already uses this mount path."""


class CustomLocationProtected(ValueError):
    """delete_contents was called on a custom-location share. Refused
    because we never delete data we don't own."""


class SharedFolderStoreService:
    """Project-scoped resolver. Constructor takes a callable that maps
    project slug → project id so the service stays decoupled from the
    project repository's import path."""

    def __init__(
        self,
        repo: ShareRepository,
        provisioner: ShareProvisioner,
        project_id_resolver: Callable[[str], int | None],
        *,
        lock: RLock | None = None,
        clock: Clock = _utc_now,
    ) -> None:
        self._repo = repo
        self._provisioner = provisioner
        self._resolve_project_id = project_id_resolver
        self._lock = lock if lock is not None else RLock()
        self._clock = clock

    # ----- creation -----

    def create_new(self, req: CreateNewShareRequest) -> ShareRecord:
        mount_path = validate_mount_path(req.mount_path)
        with self._lock:
            project_id = self._require_project_id(req.project_slug)
            self._reject_mount_collision(project_id, mount_path)
            share = self._repo.add(
                SharedFolder(
                    project_id=project_id,
                    name=req.name.strip() or mount_path,
                    mount_path=mount_path,
                    real_path=req.real_path,
                    created_at=self._clock(),
                )
            )
            assert share.slug is not None
            # Provision canonical dir; if a custom real_path is given,
            # replace it with a symlink to that path.
            self._provisioner.ensure_canonical_dir(req.project_slug, share.slug)
            if req.real_path is not None:
                req.real_path.mkdir(parents=True, exist_ok=True)
                self._provisioner.link_canonical_to_external(
                    req.project_slug, share.slug, req.real_path
                )
        return ShareRecord(share=share)

    def create_from_existing(
        self, req: CreateExistingShareRequest
    ) -> ShareRecord:
        mount_path = validate_mount_path(req.mount_path)
        if not req.existing_path.is_dir():
            raise ValueError(
                f"existing path is not a directory: {req.existing_path}"
            )
        with self._lock:
            project_id = self._require_project_id(req.project_slug)
            self._reject_mount_collision(project_id, mount_path)
            share = self._repo.add(
                SharedFolder(
                    project_id=project_id,
                    name=req.name.strip() or mount_path,
                    mount_path=mount_path,
                    real_path=req.existing_path,
                    created_at=self._clock(),
                )
            )
            assert share.slug is not None
            self._provisioner.link_canonical_to_external(
                req.project_slug, share.slug, req.existing_path
            )
        return ShareRecord(share=share)

    # ----- read -----

    def list_for_project(self, project_slug: str) -> list[SharedFolder]:
        with self._lock:
            project_id = self._resolve_project_id(project_slug)
            if project_id is None:
                return []
            return self._repo.list_for_project(project_id)

    def get(self, project_slug: str, share_slug: str) -> ShareRecord | None:
        with self._lock:
            share = self._repo.get_by_slug(share_slug)
            if share is None:
                return None
            project_id = self._resolve_project_id(project_slug)
            if project_id is None or share.project_id != project_id:
                return None
        return ShareRecord(share=share)

    # ----- mutate -----

    def rename(self, req: UpdateShareRequest) -> ShareRecord:
        new_name = req.name.strip()
        if not new_name:
            raise ValueError("name must be non-empty")
        with self._lock:
            share = self._require_share(req.project_slug, req.share_slug)
            share.name = new_name
            self._repo.update(share)
        return ShareRecord(share=share)

    def stop_sharing(self, project_slug: str, share_slug: str) -> None:
        with self._lock:
            share = self._require_share(project_slug, share_slug)
            assert share.slug is not None
            # Delete the canonical path's symlink (or empty dir) without
            # touching its target; we only delete the row + the Atelier-
            # side mounting infrastructure.
            self._provisioner.remove_canonical(
                project_slug, share.slug, delete_contents=False
            )
            self._repo.delete(share.slug)

    def delete_contents(self, project_slug: str, share_slug: str) -> None:
        with self._lock:
            share = self._require_share(project_slug, share_slug)
            assert share.slug is not None
            if share.real_path is not None:
                raise CustomLocationProtected(
                    "delete_contents is disabled for custom-location shares; "
                    "the data lives outside Atelier and must be removed by the user"
                )
            self._provisioner.remove_canonical(
                project_slug, share.slug, delete_contents=True
            )
            self._repo.delete(share.slug)

    # ----- helpers -----

    def _require_project_id(self, project_slug: str) -> int:
        project_id = self._resolve_project_id(project_slug)
        if project_id is None:
            raise ValueError(f"project not found: {project_slug}")
        return project_id

    def _require_share(
        self, project_slug: str, share_slug: str
    ) -> SharedFolder:
        share = self._repo.get_by_slug(share_slug)
        project_id = self._resolve_project_id(project_slug)
        if (
            share is None
            or project_id is None
            or share.project_id != project_id
        ):
            raise ShareNotFound(
                f"share not found: {project_slug}/{share_slug}"
            )
        return share

    def _reject_mount_collision(self, project_id: int, mount_path: str) -> None:
        existing = self._repo.get_by_mount_path(project_id, mount_path)
        if existing is not None:
            raise MountPathConflict(
                f"mount path already in use by another share: {mount_path}"
            )


__all__ = [
    "CustomLocationProtected",
    "MountPathConflict",
    "ShareNotFound",
    "SharedFolderStoreService",
]
