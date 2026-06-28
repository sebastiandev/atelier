"""Integration tests for ``GET /api/fs/list`` (folder picker backend).

Builds a small directory tree under ``tmp_path`` and walks the route
through happy + edge cases. Tests that need ``$HOME`` resolution use
``monkeypatch`` against ``Path.home`` rather than the real env so the
host's home directory contents don't influence assertions.
"""

from __future__ import annotations

from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from src.settings import Settings


def _make_tree(parent: Path) -> Path:
    """Build a controlled tree under a fresh subdir so the workspace root
    that the ``app_client`` fixture creates inside ``tmp_path`` doesn't
    bleed into the listing."""
    root = parent / "tree"
    root.mkdir()
    (root / "alpha").mkdir()
    (root / "beta").mkdir()
    (root / ".hidden_dir").mkdir()
    (root / "z_file.txt").write_text("ok", encoding="utf-8")
    (root / "a_file.txt").write_text("ok", encoding="utf-8")
    (root / ".hidden_file").write_text("ok", encoding="utf-8")
    return root


def test_lists_directory_dirs_first(
    app_client: TestClient, tmp_path: Path
) -> None:
    root = _make_tree(tmp_path)
    res = app_client.get("/api/fs/list", params={"path": str(root)})
    assert res.status_code == 200
    body = res.json()
    assert body["path"] == str(root.resolve())
    # Hidden entries omitted by default; dirs sorted first, then files,
    # both case-folded alphabetical within their group.
    names = [e["name"] for e in body["entries"]]
    assert names == ["alpha", "beta", "a_file.txt", "z_file.txt"]
    assert body["entries"][0]["is_dir"] is True
    assert body["entries"][2]["is_dir"] is False


def test_show_hidden_includes_dotfiles(
    app_client: TestClient, tmp_path: Path
) -> None:
    root = _make_tree(tmp_path)
    res = app_client.get(
        "/api/fs/list", params={"path": str(root), "show_hidden": "true"}
    )
    body = res.json()
    names = [e["name"] for e in body["entries"]]
    assert names == [".hidden_dir", "alpha", "beta", ".hidden_file", "a_file.txt", "z_file.txt"]
    hidden = next(e for e in body["entries"] if e["name"] == ".hidden_dir")
    assert hidden["is_hidden"] is True


def test_parent_resolves_to_parent_directory(
    app_client: TestClient, tmp_path: Path
) -> None:
    sub = tmp_path / "sub"
    sub.mkdir()
    res = app_client.get("/api/fs/list", params={"path": str(sub)})
    body = res.json()
    assert body["parent"] == str(tmp_path.resolve())


def test_default_path_is_home(
    app_client: TestClient,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Omitting ``path`` lands the user in their home directory — the
    "starting point" for the folder picker."""
    fake_home = tmp_path / "home"
    fake_home.mkdir()
    (fake_home / "marker").mkdir()
    monkeypatch.setattr(Path, "home", classmethod(lambda cls: fake_home))
    res = app_client.get("/api/fs/list")
    body = res.json()
    assert body["path"] == str(fake_home.resolve())
    assert any(e["name"] == "marker" for e in body["entries"])


def test_tilde_expansion(
    app_client: TestClient,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    fake_home = tmp_path / "home"
    fake_home.mkdir()
    monkeypatch.setenv("HOME", str(fake_home))
    res = app_client.get("/api/fs/list", params={"path": "~"})
    assert res.status_code == 200
    assert res.json()["path"] == str(fake_home.resolve())


def test_404_when_path_missing(app_client: TestClient, tmp_path: Path) -> None:
    res = app_client.get(
        "/api/fs/list", params={"path": str(tmp_path / "nope")}
    )
    assert res.status_code == 404


def test_400_when_path_relative(app_client: TestClient) -> None:
    res = app_client.get("/api/fs/list", params={"path": "rel/path"})
    assert res.status_code == 400


def test_400_when_path_is_a_file(
    app_client: TestClient, tmp_path: Path
) -> None:
    file_path = tmp_path / "f.txt"
    file_path.write_text("ok", encoding="utf-8")
    res = app_client.get("/api/fs/list", params={"path": str(file_path)})
    assert res.status_code == 400


def test_upload_image_stores_under_workspace_root(
    app_client: TestClient, test_settings: Settings
) -> None:
    res = app_client.post(
        "/api/fs/uploads/images",
        files={"file": ("clip.png", b"\x89PNG\r\n", "image/png")},
    )

    assert res.status_code == 200
    body = res.json()
    path = Path(body["path"])
    assert body["content_type"] == "image/png"
    assert body["size"] == 6
    assert path.parent == test_settings.workspace_root / "attachments" / "images"
    assert path.suffix == ".png"
    assert path.read_bytes() == b"\x89PNG\r\n"


def test_upload_image_stores_under_work_when_work_slug_supplied(
    app_client: TestClient, test_settings: Settings
) -> None:
    res = app_client.post(
        "/api/fs/uploads/images",
        params={"work_slug": "WRK-001"},
        files={"file": ("clip.webp", b"RIFFwebp", "image/webp")},
    )

    assert res.status_code == 200
    path = Path(res.json()["path"])
    assert path.parent == (
        test_settings.workspace_root / "works" / "WRK-001" / "attachments" / "images"
    )
    assert path.suffix == ".webp"
    assert path.read_bytes() == b"RIFFwebp"


def test_upload_image_accepts_known_extension_when_mime_is_generic(
    app_client: TestClient, test_settings: Settings
) -> None:
    res = app_client.post(
        "/api/fs/uploads/images",
        files={"file": ("clip.jpg", b"\xff\xd8\xff", "application/octet-stream")},
    )

    assert res.status_code == 200
    body = res.json()
    path = Path(body["path"])
    assert body["content_type"] == "image/jpeg"
    assert path.parent == test_settings.workspace_root / "attachments" / "images"
    assert path.suffix == ".jpg"
    assert path.read_bytes() == b"\xff\xd8\xff"


def test_upload_image_accepts_tiff_clipboard_payload(
    app_client: TestClient, test_settings: Settings
) -> None:
    res = app_client.post(
        "/api/fs/uploads/images",
        files={"file": ("clipboard-image.tiff", b"MM\x00*", "image/tiff")},
    )

    assert res.status_code == 200
    body = res.json()
    path = Path(body["path"])
    assert body["content_type"] == "image/tiff"
    assert path.parent == test_settings.workspace_root / "attachments" / "images"
    assert path.suffix == ".tiff"
    assert path.read_bytes() == b"MM\x00*"


def test_upload_image_rejects_non_images(app_client: TestClient) -> None:
    res = app_client.post(
        "/api/fs/uploads/images",
        files={"file": ("note.txt", b"hello", "text/plain")},
    )

    assert res.status_code == 415
