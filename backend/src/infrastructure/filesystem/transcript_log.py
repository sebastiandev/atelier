"""TranscriptLog adapter — NDJSON-backed event log per agent.

Thin composition of `WorkspacePaths` and the NDJSON primitives. The crash
semantics (fsync per line, partial-line repair on append) live in
`ndjson.py`.
"""

from collections.abc import Iterator
from typing import Any

from src.infrastructure.filesystem.ndjson import (
    append_event,
    last_seq,
    read_before,
    read_from_cursor,
    read_recent_by_type,
    read_tail,
)
from src.infrastructure.filesystem.paths import WorkspacePaths


class FsTranscriptLog:
    def __init__(self, paths: WorkspacePaths) -> None:
        self._paths = paths

    def append(self, work_slug: str, agent_slug: str, event: dict[str, Any]) -> None:
        append_event(self._paths.transcript(work_slug, agent_slug), event)

    def read_from_cursor(
        self, work_slug: str, agent_slug: str, cursor: int
    ) -> Iterator[dict[str, Any]]:
        return read_from_cursor(self._paths.transcript(work_slug, agent_slug), cursor)

    def read_before(
        self, work_slug: str, agent_slug: str, before_seq: int, limit: int
    ) -> Iterator[dict[str, Any]]:
        return read_before(
            self._paths.transcript(work_slug, agent_slug), before_seq, limit
        )

    def read_tail(
        self,
        work_slug: str,
        agent_slug: str,
        cursor: int,
        limit: int,
        before_seq: int | None = None,
    ) -> Iterator[dict[str, Any]]:
        return read_tail(
            self._paths.transcript(work_slug, agent_slug),
            cursor=cursor,
            limit=limit,
            before_seq=before_seq,
        )

    def read_recent_by_type(
        self,
        work_slug: str,
        agent_slug: str,
        event_types: set[str],
        cursor: int,
        limit: int,
        before_seq: int | None = None,
    ) -> Iterator[dict[str, Any]]:
        return read_recent_by_type(
            self._paths.transcript(work_slug, agent_slug),
            event_types,
            cursor=cursor,
            limit=limit,
            before_seq=before_seq,
        )

    def last_seq(self, work_slug: str, agent_slug: str) -> int:
        return last_seq(self._paths.transcript(work_slug, agent_slug))


__all__ = ["FsTranscriptLog"]
