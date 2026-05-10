"""GET /api/fs/list — directory listing for the folder-picker modal.

Atelier is local-trust so there's no allow-list: the user can browse
anywhere they have read access. The endpoint is read-only, never
recursive, and never returns file contents — it lists names + a "is
this a directory" flag so the FE can render a one-level browser.
"""

from __future__ import annotations

from pathlib import Path

from fastapi import APIRouter, HTTPException, Query
from pydantic import BaseModel

from src.infrastructure.filesystem.dir_listing import list_directory

router = APIRouter()


class FolderEntry(BaseModel):
    name: str
    is_dir: bool
    is_hidden: bool


class FolderListing(BaseModel):
    path: str
    parent: str | None
    entries: list[FolderEntry]


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
