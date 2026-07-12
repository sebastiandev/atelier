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
            event = _decode_event_line(raw[:-1])
            if event is None:
                continue
            seq = event["seq"]
            if seq <= cursor:
                continue
            yield event


def read_before(path: Path, before_seq: int, limit: int) -> Iterator[dict[str, Any]]:
    """Yield up to ``limit`` events with ``seq < before_seq`` in file order."""
    if limit <= 0:
        return
    yield from _tail_events(path, limit=limit, min_seq=None, max_seq=before_seq - 1)


def read_tail(
    path: Path,
    *,
    cursor: int,
    limit: int,
    before_seq: int | None = None,
) -> Iterator[dict[str, Any]]:
    """Yield up to ``limit`` newest events with ``cursor < seq < before_seq``.

    ``before_seq`` is exclusive. When omitted, the tail window is bounded
    only by ``cursor``.
    """
    if limit <= 0:
        return
    max_seq = before_seq - 1 if before_seq is not None else None
    yield from _tail_events(path, limit=limit, min_seq=cursor + 1, max_seq=max_seq)


def read_recent_by_type(
    path: Path,
    event_types: set[str],
    *,
    cursor: int,
    limit: int,
    before_seq: int | None = None,
) -> Iterator[dict[str, Any]]:
    """Yield newest matching event types with ``cursor < seq < before_seq``.

    This uses the same reverse chunk scan as ``read_tail`` so callers can
    recover sticky metadata from large transcripts without a full replay.
    """
    if limit <= 0 or not event_types:
        return
    max_seq = before_seq - 1 if before_seq is not None else None
    yield from _tail_events(
        path,
        limit=limit,
        min_seq=cursor + 1,
        max_seq=max_seq,
        event_types=event_types,
    )


def _decode_event_line(line: bytes) -> dict[str, Any] | None:
    stripped = line.strip()
    if not stripped:
        return None
    try:
        event = json.loads(stripped)
    except json.JSONDecodeError:
        return None
    if not isinstance(event, dict):
        return None
    seq = event.get("seq")
    if not isinstance(seq, int) or isinstance(seq, bool):
        return None
    return event


def _tail_events(
    path: Path,
    *,
    limit: int,
    min_seq: int | None,
    max_seq: int | None,
    event_types: set[str] | None = None,
) -> list[dict[str, Any]]:
    try:
        size = path.stat().st_size
    except FileNotFoundError:
        return []
    if size == 0 or limit <= 0:
        return []

    chunk = 64 * 1024
    selected: list[dict[str, Any]] = []
    pending_prefix = b""
    first_chunk = True

    with path.open("rb") as f:
        pos = size
        while pos > 0 and len(selected) < limit:
            read_size = min(chunk, pos)
            pos -= read_size
            f.seek(pos)
            buf = f.read(read_size) + pending_prefix
            parts = buf.split(b"\n")

            if pos > 0:
                pending_prefix = parts[0]
                complete = parts[1:]
            else:
                pending_prefix = b""
                complete = parts

            if first_chunk and complete:
                # read_from_cursor drops a non-newline-terminated tail. Do
                # the same here so a crash fragment cannot show up in a
                # history chunk. If the file ends cleanly, split() leaves
                # an empty sentinel after the final newline; drop that too.
                complete = complete[:-1]
                first_chunk = False

            for raw_line in reversed(complete):
                event = _decode_event_line(raw_line)
                if event is None:
                    continue
                seq = event["seq"]
                if max_seq is not None and seq > max_seq:
                    continue
                if min_seq is not None and seq < min_seq:
                    return list(reversed(selected))
                if event_types is not None and event.get("type") not in event_types:
                    continue
                selected.append(event)
                if len(selected) >= limit:
                    break

    return list(reversed(selected))


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


__all__ = [
    "append_event",
    "last_seq",
    "read_before",
    "read_from_cursor",
    "read_recent_by_type",
    "read_tail",
]
