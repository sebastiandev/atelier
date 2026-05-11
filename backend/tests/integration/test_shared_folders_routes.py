"""Integration tests for ``/api/projects/{slug}/shares``.

Covers full CRUD against the live FastAPI app + SQLite + filesystem.
Validates that the wire format matches the FE's expectations and that
mount-path validation, conflict detection, and the stop-sharing vs
delete-contents distinction all flow through correctly.
"""

from __future__ import annotations

from pathlib import Path

import pytest
from fastapi.testclient import TestClient


def _make_project(client: TestClient, name: str = "Praxy") -> str:
    res = client.post(
        "/api/projects",
        json={
            "name": name,
            "description": "",
            "glyph": "PX",
            "color": 180,
            "pinned": False,
        },
    )
    assert res.status_code == 201
    return res.json()["slug"]


def test_list_shares_empty_for_new_project(app_client: TestClient) -> None:
    slug = _make_project(app_client)
    res = app_client.get(f"/api/projects/{slug}/shares")
    assert res.status_code == 200
    assert res.json() == []


def test_create_new_share_default_location(
    app_client: TestClient, tmp_path: Path
) -> None:
    slug = _make_project(app_client)
    res = app_client.post(
        f"/api/projects/{slug}/shares",
        json={"mode": "new", "name": "BMAD", "mount_path": "_bmad-output"},
    )
    assert res.status_code == 201, res.text
    body = res.json()
    assert body["name"] == "BMAD"
    assert body["mount_path"] == "_bmad-output"
    assert body["is_custom_location"] is False
    assert body["real_path"] is None
    # Canonical dir exists on disk now.
    assert Path(body["canonical_path"]).is_dir()


def test_create_new_share_custom_location_creates_external_symlink(
    app_client: TestClient, tmp_path: Path
) -> None:
    slug = _make_project(app_client)
    custom = tmp_path / "elsewhere"
    res = app_client.post(
        f"/api/projects/{slug}/shares",
        json={
            "mode": "new",
            "name": "Notes",
            "mount_path": "notes",
            "location": str(custom),
        },
    )
    assert res.status_code == 201, res.text
    body = res.json()
    assert body["is_custom_location"] is True
    assert body["real_path"] == str(custom)
    canonical = Path(body["canonical_path"])
    assert canonical.is_symlink()
    assert custom.is_dir()  # we mkdir on the user's chosen location too


def test_create_existing_share_links_to_user_folder(
    app_client: TestClient, tmp_path: Path
) -> None:
    """The motivating BMAD case: user has an existing folder; we point
    Atelier at it without moving or copying anything."""
    slug = _make_project(app_client)
    user_folder = tmp_path / "_bmad-output"
    user_folder.mkdir()
    (user_folder / "STORY-007.md").write_text("planning content")
    res = app_client.post(
        f"/api/projects/{slug}/shares",
        json={
            "mode": "existing",
            "name": "BMAD",
            "mount_path": "_bmad-output",
            "location": str(user_folder),
        },
    )
    assert res.status_code == 201, res.text
    canonical = Path(res.json()["canonical_path"])
    assert canonical.is_symlink()
    # Original content untouched + reachable via the canonical symlink.
    assert (canonical / "STORY-007.md").read_text() == "planning content"
    assert (user_folder / "STORY-007.md").read_text() == "planning content"


def test_create_share_rejects_relative_location(
    app_client: TestClient,
) -> None:
    slug = _make_project(app_client)
    res = app_client.post(
        f"/api/projects/{slug}/shares",
        json={
            "mode": "new",
            "name": "x",
            "mount_path": "x",
            "location": "relative/path",
        },
    )
    assert res.status_code == 400
    assert "absolute" in res.json()["detail"]


def test_create_share_rejects_invalid_mount_path(
    app_client: TestClient,
) -> None:
    slug = _make_project(app_client)
    res = app_client.post(
        f"/api/projects/{slug}/shares",
        json={"mode": "new", "name": "x", "mount_path": "../oops"},
    )
    assert res.status_code == 400


def test_create_share_409_on_mount_collision(app_client: TestClient) -> None:
    slug = _make_project(app_client)
    app_client.post(
        f"/api/projects/{slug}/shares",
        json={"mode": "new", "name": "a", "mount_path": "dup"},
    )
    res = app_client.post(
        f"/api/projects/{slug}/shares",
        json={"mode": "new", "name": "b", "mount_path": "dup"},
    )
    assert res.status_code == 409


def test_rename_share_updates_label(app_client: TestClient) -> None:
    slug = _make_project(app_client)
    create = app_client.post(
        f"/api/projects/{slug}/shares",
        json={"mode": "new", "name": "old", "mount_path": "x"},
    ).json()
    share_slug = create["slug"]
    res = app_client.patch(
        f"/api/projects/{slug}/shares/{share_slug}",
        json={"name": "new"},
    )
    assert res.status_code == 200
    assert res.json()["name"] == "new"
    # mount_path immutable post-creation
    assert res.json()["mount_path"] == "x"


def test_rename_404_for_unknown_share(app_client: TestClient) -> None:
    slug = _make_project(app_client)
    res = app_client.patch(
        f"/api/projects/{slug}/shares/shr-999",
        json={"name": "x"},
    )
    assert res.status_code == 404


def test_stop_sharing_leaves_real_folder_intact(
    app_client: TestClient, tmp_path: Path
) -> None:
    """The "Stop sharing" action removes the Atelier-side symlink only.
    For a custom-location share, the user's real folder MUST survive."""
    slug = _make_project(app_client)
    user_folder = tmp_path / "_bmad"
    user_folder.mkdir()
    (user_folder / "STORY.md").write_text("keep me")
    create = app_client.post(
        f"/api/projects/{slug}/shares",
        json={
            "mode": "existing",
            "name": "x",
            "mount_path": "_bmad",
            "location": str(user_folder),
        },
    ).json()
    share_slug = create["slug"]
    canonical = Path(create["canonical_path"])

    res = app_client.delete(f"/api/projects/{slug}/shares/{share_slug}")
    assert res.status_code == 204
    # Symlink gone, real folder + contents intact.
    assert not canonical.exists()
    assert (user_folder / "STORY.md").read_text() == "keep me"
    # Share no longer listed.
    assert app_client.get(f"/api/projects/{slug}/shares").json() == []


def test_delete_contents_refused_for_custom_location(
    app_client: TestClient, tmp_path: Path
) -> None:
    slug = _make_project(app_client)
    user_folder = tmp_path / "user"
    user_folder.mkdir()
    create = app_client.post(
        f"/api/projects/{slug}/shares",
        json={
            "mode": "existing",
            "name": "x",
            "mount_path": "x",
            "location": str(user_folder),
        },
    ).json()
    share_slug = create["slug"]
    res = app_client.delete(
        f"/api/projects/{slug}/shares/{share_slug}?delete_data=true"
    )
    assert res.status_code == 400
    assert "custom-location" in res.json()["detail"]


def test_delete_contents_removes_default_location_data(
    app_client: TestClient,
) -> None:
    slug = _make_project(app_client)
    create = app_client.post(
        f"/api/projects/{slug}/shares",
        json={"mode": "new", "name": "x", "mount_path": "x"},
    ).json()
    canonical = Path(create["canonical_path"])
    (canonical / "scratch.md").write_text("ephemeral")
    res = app_client.delete(
        f"/api/projects/{slug}/shares/{create['slug']}?delete_data=true"
    )
    assert res.status_code == 204
    assert not canonical.exists()


def test_create_existing_rejects_non_directory_path(
    app_client: TestClient, tmp_path: Path
) -> None:
    slug = _make_project(app_client)
    file_path = tmp_path / "f.txt"
    file_path.write_text("ok")
    res = app_client.post(
        f"/api/projects/{slug}/shares",
        json={
            "mode": "existing",
            "name": "x",
            "mount_path": "x",
            "location": str(file_path),
        },
    )
    assert res.status_code == 400


# ---------------------------------------------------------------------------
# End-to-end: shares mount into agent worktrees on agent creation
# ---------------------------------------------------------------------------


def test_agent_start_mounts_project_shares_into_worktree(
    app_client: TestClient, tmp_path: Path, tmp_workdir: str
) -> None:
    """The motivating BMAD case end-to-end. Create a project + share +
    work-in-project + agent; verify the agent's worktree has the
    share's mount path as a symlink chasing through to the user's
    real folder."""
    project_slug = _make_project(app_client, name="Praxy")

    # User's real folder somewhere outside Atelier — populated with a
    # planning artifact before any agent is created.
    real_bmad = tmp_path / "user-bmad"
    real_bmad.mkdir()
    (real_bmad / "STORY-007.md").write_text("planning content")

    # Add the share as "existing" — Atelier symlinks to the user's folder.
    create_share = app_client.post(
        f"/api/projects/{project_slug}/shares",
        json={
            "mode": "existing",
            "name": "BMAD",
            "mount_path": "_bmad-output",
            "location": str(real_bmad),
        },
    )
    assert create_share.status_code == 201, create_share.text

    # Work inside that project.
    work_res = app_client.post(
        "/api/works",
        json={
            "name": "Implement STORY-007",
            "description": "",
            "contexts": [],
            "project_slug": project_slug,
        },
    )
    assert work_res.status_code == 201, work_res.text
    work_slug = work_res.json()["slug"]

    # Agent under that work.
    agent_res = app_client.post(
        f"/api/works/{work_slug}/agents",
        json={
            "name": "Dev",
            "persona": "developer",
            "role": "implementer",
            "provider": "amp",
            "model": "smart",
            "folder": tmp_workdir,
        },
    )
    assert agent_res.status_code == 201, agent_res.text
    agent_slug = agent_res.json()["slug"]

    # The worktree's <mount_path> should now be a symlink whose target
    # eventually resolves to the user's real folder (canonical → real).
    workspace_root = tmp_path / "Atelier"  # matches conftest.test_settings
    worktree = workspace_root / "works" / work_slug / "worktrees" / agent_slug
    mounted = worktree / "_bmad-output"
    assert mounted.is_symlink()
    # Follow the chain: mounted → canonical → real_bmad. The leaf file
    # should be readable via the worktree's mount path.
    assert (mounted / "STORY-007.md").read_text() == "planning content"


def test_agent_start_omits_shares_for_loose_work(
    app_client: TestClient, tmp_path: Path, tmp_workdir: str
) -> None:
    """Loose works (no project_slug) get no shared folders mounted —
    project-scoped shares simply don't apply."""
    work_res = app_client.post(
        "/api/works",
        json={"name": "Loose work", "description": "", "contexts": []},
    )
    work_slug = work_res.json()["slug"]
    agent_res = app_client.post(
        f"/api/works/{work_slug}/agents",
        json={
            "name": "Dev",
            "persona": "developer",
            "role": "implementer",
            "provider": "amp",
            "model": "smart",
            "folder": tmp_workdir,
        },
    )
    assert agent_res.status_code == 201
    agent_slug = agent_res.json()["slug"]
    workspace_root = tmp_path / "Atelier"
    worktree = workspace_root / "works" / work_slug / "worktrees" / agent_slug
    # No shares mounted — the worktree's children are only what the
    # worktree manager set up.
    assert not (worktree / "_bmad-output").exists()
