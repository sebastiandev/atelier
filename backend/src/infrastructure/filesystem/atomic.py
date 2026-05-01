"""Atomic file writes via write-to-tmp + rename.

`atomic_write_*` functions guarantee that readers either see the previous
file contents or the full new contents — never a partial write. On a crash
between the temp write and the rename, the target file is left untouched.
A stale `<path>.tmp` may be left behind; the next write overwrites it.
"""

import json
import os
from datetime import datetime
from pathlib import Path
from typing import Any


def atomic_write_bytes(path: Path, data: bytes) -> None:
    """Atomically replace `path` with `data`.

    Writes to `<path>.tmp`, fsyncs the file, renames into place, then
    fsyncs the parent directory so the new dirent itself is durable.
    """
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.name + ".tmp")
    fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o644)
    try:
        os.write(fd, data)
        os.fsync(fd)
    finally:
        os.close(fd)
    os.replace(tmp, path)
    _fsync_dir(path.parent)


def atomic_write_text(path: Path, content: str, *, encoding: str = "utf-8") -> None:
    atomic_write_bytes(path, content.encode(encoding))


def atomic_write_json(path: Path, obj: Any) -> None:
    """Serialise `obj` to indented JSON and atomically write.

    Indented for human readability — `work.json` and `agent.json` get
    eyeballed by users. `Path` and tz-aware `datetime` are handled; other
    non-JSON types raise loudly rather than coerce silently.
    """
    data = json.dumps(obj, ensure_ascii=False, indent=2, default=json_default)
    atomic_write_bytes(path, data.encode("utf-8"))


def json_default(obj: Any) -> str:
    if isinstance(obj, Path):
        return str(obj)
    if isinstance(obj, datetime):
        return obj.isoformat()
    raise TypeError(f"object of type {type(obj).__name__} is not JSON serializable")


def _fsync_dir(path: Path) -> None:
    """fsync the directory inode; best-effort on platforms that don't allow it."""
    try:
        fd = os.open(path, os.O_RDONLY)
    except OSError:
        return
    try:
        os.fsync(fd)
    except OSError:
        pass
    finally:
        os.close(fd)


__all__ = ["atomic_write_bytes", "atomic_write_json", "atomic_write_text", "json_default"]
