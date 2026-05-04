"""Append-only NDJSON event log with crash-safe append + cursor read.

Each event is one JSON object on its own line, terminated by `\\n`. Writes
fsync after every line so a successful `append_event` survives an
immediate process or kernel crash.

Crash recovery happens on the **append** side: before writing, we check
whether the file ends with a newline and, if not, truncate the partial
trailing line. Otherwise the next append's bytes would be glued onto the
broken line, fabricating a new corrupt line.

Reads independently skip any line that fails JSON parsing or that lacks
an integer `seq` field, so a transcript with garbage tolerated by the
appender still produces a valid event stream.

NOTE: each `append_event` reopens the file. That's fine for STORY-004's
ad-hoc usage; the supervisor (STORY-009) will hold a long-lived fd and
amortise the open + repair check.
"""

import json
import os
from collections.abc import Iterator, Mapping
from pathlib import Path
from typing import Any

from src.infrastructure.filesystem.atomic import json_default


def append_event(path: Path, event: Mapping[str, Any]) -> None:
    """Serialise `event` as a JSON line, append + fsync."""
    path.parent.mkdir(parents=True, exist_ok=True)
    _repair_partial_trailing_line(path)
    line = json.dumps(event, ensure_ascii=False, separators=(",", ":"), default=json_default)
    data = (line + "\n").encode("utf-8")
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_APPEND, 0o644)
    try:
        os.write(fd, data)
        os.fsync(fd)
    finally:
        os.close(fd)


def read_from_cursor(path: Path, cursor: int) -> Iterator[dict[str, Any]]:
    """Yield events with integer `seq > cursor`, in file order.

    Missing file → empty iterator. Malformed lines (parse failure, not a
    JSON object, missing/non-int `seq`) are skipped silently. The last
    line of the file is dropped if it lacks a trailing newline — i.e. it
    was written partially.
    """
    try:
        f = path.open("rb")
    except FileNotFoundError:
        return
    with f:
        for raw in f:
            if not raw.endswith(b"\n"):
                continue
            line = raw[:-1].strip()
            if not line:
                continue
            try:
                event = json.loads(line)
            except json.JSONDecodeError:
                continue
            if not isinstance(event, dict):
                continue
            seq = event.get("seq")
            if not isinstance(seq, int) or isinstance(seq, bool) or seq <= cursor:
                continue
            yield event


def _repair_partial_trailing_line(path: Path) -> None:
    """If `path` ends mid-line, truncate everything after the last newline.

    No-op if the file is missing, empty, or already ends with `\\n`.
    """
    try:
        size = path.stat().st_size
    except FileNotFoundError:
        return
    if size == 0:
        return
    with path.open("r+b") as f:
        f.seek(size - 1)
        if f.read(1) == b"\n":
            return
        chunk = 4096
        pos = size
        while pos > 0:
            read_size = min(chunk, pos)
            pos -= read_size
            f.seek(pos)
            buf = f.read(read_size)
            nl = buf.rfind(b"\n")
            if nl != -1:
                f.truncate(pos + nl + 1)
                f.flush()
                os.fsync(f.fileno())
                return
        f.truncate(0)
        f.flush()
        os.fsync(f.fileno())


def last_seq(path: Path) -> int:
    """Return the highest ``seq`` in the file, or 0 if missing/empty.

    Tail-reads in fixed-size chunks from the end of the file so the cost
    is independent of transcript length. Skips malformed trailing lines
    the same way ``read_from_cursor`` does.
    """
    try:
        size = path.stat().st_size
    except FileNotFoundError:
        return 0
    if size == 0:
        return 0
    chunk = 4096
    with path.open("rb") as f:
        pos = size
        buf = b""
        while pos > 0:
            read_size = min(chunk, pos)
            pos -= read_size
            f.seek(pos)
            buf = f.read(read_size) + buf
            for line in reversed(buf.splitlines()):
                stripped = line.strip()
                if not stripped:
                    continue
                try:
                    event = json.loads(stripped)
                except json.JSONDecodeError:
                    continue
                if not isinstance(event, dict):
                    continue
                seq = event.get("seq")
                if isinstance(seq, int) and not isinstance(seq, bool):
                    return seq
    return 0


__all__ = ["append_event", "last_seq", "read_from_cursor"]
