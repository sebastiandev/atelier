"""Read-only directory listing for the folder-picker UI.

The new-agent dialog's folder field grew a modal browser that lists
immediate children of a directory (one level — never recursive). This
module is the small helper behind ``GET /api/fs/list``: pure I/O, no
domain concepts, easy to unit-test.

Atelier is single-user / single-host so there's no path allow-list —
the user can browse anywhere they have OS-level read permission. The
endpoint is read-only and never reads file contents.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True, kw_only=True)
class DirEntry:
    name: str
    is_dir: bool
    is_hidden: bool


@dataclass(frozen=True, kw_only=True)
class DirListing:
    path: str
    parent: str | None
    entries: tuple[DirEntry, ...]


def list_directory(path: Path, *, show_hidden: bool = False) -> DirListing:
    """List ``path``'s immediate children, dirs-first then files,
    alphabetically within each group.

    Caller is responsible for handing in an absolute path that exists +
    resolves to a directory. The HTTP layer surfaces ``ValueError`` /
    ``FileNotFoundError`` / ``NotADirectoryError`` as 400 / 404; this
    function doesn't translate them.
    """
    resolved = path.resolve(strict=True)
    if not resolved.is_dir():
        raise NotADirectoryError(str(resolved))

    children: list[DirEntry] = []
    # ``iterdir`` can raise PermissionError on a directory the OS won't
    # let us read; let it bubble — callers turn it into a 4xx.
    for child in resolved.iterdir():
        is_hidden = child.name.startswith(".")
        if is_hidden and not show_hidden:
            continue
        # ``is_dir`` follows symlinks, which is what we want — a symlink
        # to a directory is browseable. Broken symlinks return False for
        # both is_dir and is_file; we treat them as files (the common
        # case is a stale symlink the user wants to see in order to
        # clean up, but they shouldn't be able to drill into one).
        children.append(
            DirEntry(
                name=child.name,
                is_dir=_safe_is_dir(child),
                is_hidden=is_hidden,
            )
        )

    children.sort(key=lambda e: (not e.is_dir, e.name.casefold()))

    parent = resolved.parent
    parent_str = str(parent) if parent != resolved else None
    return DirListing(
        path=str(resolved),
        parent=parent_str,
        entries=tuple(children),
    )


def _safe_is_dir(p: Path) -> bool:
    try:
        return p.is_dir()
    except OSError:
        # Broken symlink, denied stat, etc. — render as a non-dir so the
        # picker shows it but doesn't try to drill in.
        return False


__all__ = ["DirEntry", "DirListing", "list_directory"]
