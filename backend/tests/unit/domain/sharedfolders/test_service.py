"""Unit tests for ``SharedFolderStoreService``.

Uses tiny in-memory stubs for the Repository + Provisioner ports so
the service's policy (slug allocation, locking, ordering of FS+DB,
validation) gets exercised without touching SQLite or the filesystem.
"""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path

import pytest

from src.domain.models import SharedFolder
from src.domain.sharedfolders import (
    CreateExistingShareRequest,
    CreateNewShareRequest,
    SharedFolderStoreService,
    UpdateShareRequest,
)
from src.domain.sharedfolders.service import (
    CustomLocationProtected,
    MountPathConflict,
    ShareNotFound,
)


class _StubRepo:
    """In-memory ShareRepository — mimics the SQL one's slug allocation
    (placeholder → flush-derived id → rewrite slug)."""

    def __init__(self) -> None:
        self.rows: list[SharedFolder] = []
        self._next_id = 1

    def add(self, share: SharedFolder) -> SharedFolder:
        share.id = self._next_id
        share.slug = f"shr-{self._next_id}"
        self._next_id += 1
        self.rows.append(share)
        return share

    def update(self, share: SharedFolder) -> SharedFolder:
        # name/real_path only; mount_path immutable
        for existing in self.rows:
            if existing.slug == share.slug:
                existing.name = share.name
                existing.real_path = share.real_path
        return share

    def get_by_slug(self, slug: str) -> SharedFolder | None:
        return next((r for r in self.rows if r.slug == slug), None)

    def get_by_mount_path(
        self, project_id: int, mount_path: str
    ) -> SharedFolder | None:
        return next(
            (
                r
                for r in self.rows
                if r.project_id == project_id and r.mount_path == mount_path
            ),
            None,
        )

    def list_for_project(self, project_id: int) -> list[SharedFolder]:
        return [r for r in self.rows if r.project_id == project_id]

    def delete(self, slug: str) -> None:
        self.rows = [r for r in self.rows if r.slug != slug]


class _StubProvisioner:
    """Records calls so tests can assert on ordering. The actual FS ops
    are exercised by the integration tests against ``FsShareProvisioner``."""

    def __init__(self) -> None:
        self.calls: list[tuple[str, ...]] = []

    def ensure_canonical_dir(self, project_slug: str, share_slug: str) -> Path:
        self.calls.append(("ensure", project_slug, share_slug))
        return Path(f"/tmp/{project_slug}/shared/{share_slug}")

    def share_canonical_path(
        self, project_slug: str, share_slug: str
    ) -> Path:
        return Path(f"/tmp/{project_slug}/shared/{share_slug}")

    def link_canonical_to_external(
        self, project_slug: str, share_slug: str, real_path: Path
    ) -> Path:
        self.calls.append(("link", project_slug, share_slug, str(real_path)))
        return Path(f"/tmp/{project_slug}/shared/{share_slug}")

    def remove_canonical(
        self,
        project_slug: str,
        share_slug: str,
        *,
        delete_contents: bool,
    ) -> None:
        self.calls.append(
            ("remove", project_slug, share_slug, str(delete_contents))
        )

    def mount_in_worktree(
        self,
        work_slug: str,
        agent_slug: str,
        mount_path: str,
        target: Path,
    ) -> None:
        self.calls.append(
            ("mount", work_slug, agent_slug, mount_path, str(target))
        )

    def unmount_from_worktree(
        self, work_slug: str, agent_slug: str, mount_path: str
    ) -> None:
        self.calls.append(("unmount", work_slug, agent_slug, mount_path))


def _make_service(
    project_ids: dict[str, int] | None = None,
) -> tuple[SharedFolderStoreService, _StubRepo, _StubProvisioner]:
    repo = _StubRepo()
    prov = _StubProvisioner()
    ids = project_ids or {"PRJ-001": 1}
    service = SharedFolderStoreService(
        repo, prov, lambda slug: ids.get(slug), clock=lambda: datetime(2026, 5, 11, tzinfo=UTC)
    )
    return service, repo, prov


# ---------- create_new ----------


def test_create_new_default_location_skips_external_link() -> None:
    service, repo, prov = _make_service()
    record = service.create_new(
        CreateNewShareRequest(
            project_slug="PRJ-001",
            name="BMAD outputs",
            mount_path="_bmad-output",
        )
    )
    assert record.share.slug == "shr-1"
    assert record.share.name == "BMAD outputs"
    assert record.share.mount_path == "_bmad-output"
    assert record.share.real_path is None
    # Provisioner: ensure_canonical_dir only, no external link.
    assert prov.calls == [("ensure", "PRJ-001", "shr-1")]
    assert repo.rows[0].project_id == 1


def test_create_new_custom_location_links_to_external(tmp_path: Path) -> None:
    service, repo, prov = _make_service()
    real = tmp_path / "elsewhere"
    record = service.create_new(
        CreateNewShareRequest(
            project_slug="PRJ-001",
            name="Notes",
            mount_path="notes",
            real_path=real,
        )
    )
    assert record.share.real_path == real
    # ensure_canonical_dir runs first (creates the parent), then link
    # replaces it with a symlink to the external path.
    assert prov.calls[0][0] == "ensure"
    assert prov.calls[1][0] == "link"
    assert real.exists()  # service mkdir's the external dir for "+ New custom"


def test_create_new_rejects_invalid_mount_path() -> None:
    service, _, _ = _make_service()
    from src.domain.sharedfolders import InvalidMountPath

    with pytest.raises(InvalidMountPath):
        service.create_new(
            CreateNewShareRequest(
                project_slug="PRJ-001",
                name="Bad",
                mount_path="../oops",
            )
        )


def test_create_new_rejects_missing_project() -> None:
    service, _, _ = _make_service()
    with pytest.raises(ValueError, match="project not found"):
        service.create_new(
            CreateNewShareRequest(
                project_slug="PRJ-999",
                name="x",
                mount_path="x",
            )
        )


def test_create_new_rejects_mount_collision() -> None:
    service, _, _ = _make_service()
    service.create_new(
        CreateNewShareRequest(
            project_slug="PRJ-001", name="a", mount_path="dup"
        )
    )
    with pytest.raises(MountPathConflict):
        service.create_new(
            CreateNewShareRequest(
                project_slug="PRJ-001", name="b", mount_path="dup"
            )
        )


def test_create_new_name_defaults_to_mount_path_when_blank() -> None:
    service, _, _ = _make_service()
    record = service.create_new(
        CreateNewShareRequest(
            project_slug="PRJ-001", name="   ", mount_path="_bmad-output"
        )
    )
    assert record.share.name == "_bmad-output"


# ---------- create_from_existing ----------


def test_create_from_existing_links_canonical_to_user_folder(
    tmp_path: Path,
) -> None:
    service, _, prov = _make_service()
    existing = tmp_path / "user_folder"
    existing.mkdir()
    record = service.create_from_existing(
        CreateExistingShareRequest(
            project_slug="PRJ-001",
            name="BMAD",
            mount_path="_bmad-output",
            existing_path=existing,
        )
    )
    assert record.share.real_path == existing
    # No ensure_canonical_dir — we go straight to symlink-canonical-to-existing.
    assert prov.calls[0] == ("link", "PRJ-001", "shr-1", str(existing))


def test_create_from_existing_rejects_non_directory(tmp_path: Path) -> None:
    service, _, _ = _make_service()
    file_path = tmp_path / "f.txt"
    file_path.write_text("ok", encoding="utf-8")
    with pytest.raises(ValueError, match="not a directory"):
        service.create_from_existing(
            CreateExistingShareRequest(
                project_slug="PRJ-001",
                name="x",
                mount_path="x",
                existing_path=file_path,
            )
        )


# ---------- list / get / rename ----------


def test_list_for_project_returns_only_matching_project() -> None:
    service, _, _ = _make_service({"PRJ-001": 1, "PRJ-002": 2})
    service.create_new(
        CreateNewShareRequest(
            project_slug="PRJ-001", name="a", mount_path="a"
        )
    )
    service.create_new(
        CreateNewShareRequest(
            project_slug="PRJ-002", name="b", mount_path="b"
        )
    )
    assert len(service.list_for_project("PRJ-001")) == 1
    assert len(service.list_for_project("PRJ-002")) == 1
    assert service.list_for_project("PRJ-999") == []


def test_rename_updates_label_only() -> None:
    service, repo, _ = _make_service()
    service.create_new(
        CreateNewShareRequest(
            project_slug="PRJ-001", name="old", mount_path="_bmad"
        )
    )
    record = service.rename(
        UpdateShareRequest(
            project_slug="PRJ-001", share_slug="shr-1", name="new label"
        )
    )
    assert record.share.name == "new label"
    # mount_path unchanged
    assert repo.rows[0].mount_path == "_bmad"


def test_rename_rejects_blank_name() -> None:
    service, _, _ = _make_service()
    service.create_new(
        CreateNewShareRequest(
            project_slug="PRJ-001", name="x", mount_path="x"
        )
    )
    with pytest.raises(ValueError, match="non-empty"):
        service.rename(
            UpdateShareRequest(
                project_slug="PRJ-001", share_slug="shr-1", name="  "
            )
        )


def test_rename_rejects_unknown_share() -> None:
    service, _, _ = _make_service()
    with pytest.raises(ShareNotFound):
        service.rename(
            UpdateShareRequest(
                project_slug="PRJ-001", share_slug="shr-99", name="x"
            )
        )


def test_get_rejects_cross_project_lookup() -> None:
    """A share in project A is not visible via project B's lookup path,
    even if the share_slug is correct. Guards against URL tampering."""
    service, _, _ = _make_service({"PRJ-001": 1, "PRJ-002": 2})
    service.create_new(
        CreateNewShareRequest(
            project_slug="PRJ-001", name="a", mount_path="a"
        )
    )
    assert service.get("PRJ-001", "shr-1") is not None
    assert service.get("PRJ-002", "shr-1") is None


# ---------- stop_sharing / delete_contents ----------


def test_stop_sharing_removes_row_and_calls_remove_no_delete() -> None:
    service, repo, prov = _make_service()
    service.create_new(
        CreateNewShareRequest(
            project_slug="PRJ-001", name="x", mount_path="x"
        )
    )
    service.stop_sharing("PRJ-001", "shr-1")
    assert repo.rows == []
    assert ("remove", "PRJ-001", "shr-1", "False") in prov.calls


def test_delete_contents_calls_remove_with_delete_true() -> None:
    service, repo, prov = _make_service()
    service.create_new(
        CreateNewShareRequest(
            project_slug="PRJ-001", name="x", mount_path="x"
        )
    )
    service.delete_contents("PRJ-001", "shr-1")
    assert repo.rows == []
    assert ("remove", "PRJ-001", "shr-1", "True") in prov.calls


def test_delete_contents_refused_for_custom_location(tmp_path: Path) -> None:
    """We never delete data we don't own — custom-location shares live
    in the user's chosen path (iCloud, Dropbox, source repo). Stop
    sharing is fine; delete contents is not."""
    service, _, _ = _make_service()
    real = tmp_path / "user_folder"
    real.mkdir()
    service.create_from_existing(
        CreateExistingShareRequest(
            project_slug="PRJ-001",
            name="x",
            mount_path="x",
            existing_path=real,
        )
    )
    with pytest.raises(CustomLocationProtected):
        service.delete_contents("PRJ-001", "shr-1")
