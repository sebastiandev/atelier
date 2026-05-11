"""Mount-path validation for shared folders.

Mount paths are relative paths inside agent worktrees where shares
appear as symlinks. They must be safe to use as a relative path
segment — no traversal, no absolute paths, no funny characters that
break shell tools the agent will invoke.
"""

from __future__ import annotations

from pathlib import PurePosixPath


class InvalidMountPath(ValueError):
    """Mount path failed validation."""


def validate_mount_path(raw: str) -> str:
    """Normalise + validate a mount path. Returns the canonical form
    (no trailing slash, no leading ``./``).

    Rejects: empty, absolute (leading ``/``), parent-traversal (``..``
    anywhere), backslashes (Windows escape that breaks symlink
    targets), null bytes, and any segment exceeding 64 chars
    (matches the slug validator's bound).
    """
    if not isinstance(raw, str) or not raw.strip():
        raise InvalidMountPath("mount path must be a non-empty string")
    if raw.startswith("/"):
        raise InvalidMountPath("mount path must be relative (no leading '/')")
    if "\\" in raw:
        raise InvalidMountPath("mount path must use '/' separators, not '\\'")
    if "\x00" in raw:
        raise InvalidMountPath("mount path contains null byte")
    # PurePosixPath normalises ``./a/b/`` → ``a/b`` for us, but does NOT
    # collapse ``..`` (which would be unsafe to silently allow anyway).
    parts = PurePosixPath(raw).parts
    if not parts:
        raise InvalidMountPath("mount path must have at least one segment")
    for part in parts:
        if part in ("", "."):
            continue
        if part == "..":
            raise InvalidMountPath(
                "mount path must not contain '..' (no parent traversal)"
            )
        if len(part) > 64:
            raise InvalidMountPath(
                f"mount path segment too long: {part!r} (max 64 chars)"
            )
    # Canonical form: posix-style, no trailing slash, no leading './'.
    cleaned = "/".join(p for p in parts if p not in ("", "."))
    if not cleaned:
        raise InvalidMountPath("mount path resolved to empty")
    return cleaned


__all__ = ["InvalidMountPath", "validate_mount_path"]
