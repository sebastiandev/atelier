"""Local filesystem helpers for the frontend.

Atelier is local-trust so there's no allow-list: the user can browse
anywhere they have read access. The endpoint is read-only, never
recursive, and never returns file contents — it lists names + a "is
this a directory" flag so the FE can render a one-level browser.
"""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path
from typing import Annotated
from uuid import uuid4

from fastapi import APIRouter, Depends, File, HTTPException, Query, Request, UploadFile
from pydantic import BaseModel

from src.infrastructure.filesystem.dir_listing import list_directory
from src.infrastructure.filesystem.paths import WorkspacePaths
from src.settings import Settings

router = APIRouter()

_ALLOWED_IMAGE_TYPES = {
    "image/gif": ".gif",
    "image/jpeg": ".jpg",
    "image/png": ".png",
    "image/tiff": ".tiff",
    "image/webp": ".webp",
    "image/x-tiff": ".tiff",
}
_ALLOWED_IMAGE_EXTENSIONS = {
    ".gif": "image/gif",
    ".jpeg": "image/jpeg",
    ".jpg": "image/jpeg",
    ".png": "image/png",
    ".tif": "image/tiff",
    ".tiff": "image/tiff",
    ".webp": "image/webp",
}
_MAX_UPLOAD_BYTES = 20 * 1024 * 1024


class FolderEntry(BaseModel):
    name: str
    is_dir: bool
    is_hidden: bool


class FolderListing(BaseModel):
    path: str
    parent: str | None
    entries: list[FolderEntry]


class ImageUploadResponse(BaseModel):
    path: str
    filename: str
    content_type: str
    size: int


def get_settings_dep(request: Request) -> Settings:
    return request.app.state.settings  # type: ignore[no-any-return]


@router.get("/fs/list", response_model=FolderListing)
def fs_list(
    path: str | None = Query(default=None),
    show_hidden: bool = Query(default=False),
) -> FolderListing:
    target = _resolve_target(path)
    try:
        listing = list_directory(target, show_hidden=show_hidden)
    except FileNotFoundError as exc:
        raise HTTPException(status_code=404, detail="path not found") from exc
    except NotADirectoryError as exc:
        raise HTTPException(status_code=400, detail="path is not a directory") from exc
    except PermissionError as exc:
        raise HTTPException(status_code=403, detail="permission denied") from exc
    return FolderListing(
        path=listing.path,
        parent=listing.parent,
        entries=[
            FolderEntry(name=e.name, is_dir=e.is_dir, is_hidden=e.is_hidden)
            for e in listing.entries
        ],
    )


@router.post("/fs/uploads/images", response_model=ImageUploadResponse)
async def upload_image(
    file: Annotated[UploadFile, File()],
    settings: Annotated[Settings, Depends(get_settings_dep)],
    work_slug: str | None = Query(default=None),
) -> ImageUploadResponse:
    content_type = (file.content_type or "").split(";", 1)[0].strip().lower()
    resolved = _resolve_image_upload_type(content_type, file.filename)
    if resolved is None:
        raise HTTPException(status_code=415, detail="only pasted image files are supported")
    ext, content_type = resolved

    content = await file.read(_MAX_UPLOAD_BYTES + 1)
    if len(content) > _MAX_UPLOAD_BYTES:
        raise HTTPException(status_code=413, detail="image is larger than 20 MB")
    if not content:
        raise HTTPException(status_code=400, detail="image file is empty")

    paths = WorkspacePaths(settings.workspace_root)
    try:
        base_dir = (
            paths.work_dir(work_slug) / "attachments" / "images"
            if work_slug
            else settings.workspace_root / "attachments" / "images"
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    base_dir.mkdir(parents=True, exist_ok=True)
    filename = _generated_image_filename(ext)
    target = base_dir / filename
    target.write_bytes(content)
    return ImageUploadResponse(
        path=str(target),
        filename=filename,
        content_type=content_type,
        size=len(content),
    )


def _resolve_target(raw: str | None) -> Path:
    """Turn the raw query-string path into an absolute ``Path``.

    Defaults to ``$HOME`` when omitted. Expands a leading ``~``. Rejects
    a non-absolute remainder with 400 — relative paths against the
    server's cwd would be confusing for the picker, which presents
    absolute paths to the user throughout.
    """
    if raw is None or raw == "":
        return Path.home()
    expanded = Path(raw).expanduser()
    if not expanded.is_absolute():
        raise HTTPException(
            status_code=400, detail="path must be absolute (or start with ~)"
        )
    return expanded


def _generated_image_filename(ext: str) -> str:
    stamp = datetime.now(UTC).strftime("%Y%m%d-%H%M%S")
    return f"paste-{stamp}-{uuid4().hex[:8]}{ext}"


def _resolve_image_upload_type(
    content_type: str, filename: str | None
) -> tuple[str, str] | None:
    ext = _ALLOWED_IMAGE_TYPES.get(content_type)
    if ext is not None:
        return ext, content_type
    suffix = Path(filename or "").suffix.lower()
    inferred_type = _ALLOWED_IMAGE_EXTENSIONS.get(suffix)
    if inferred_type is None:
        return None
    return suffix, inferred_type
