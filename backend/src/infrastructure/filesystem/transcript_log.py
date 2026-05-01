"""TranscriptLog adapter — NDJSON-backed event log per agent.

Thin composition of `WorkspacePaths` and the NDJSON primitives. The crash
semantics (fsync per line, partial-line repair on append) live in
`ndjson.py`.
"""

from collections.abc import Iterator
from typing import Any

from src.infrastructure.filesystem.ndjson import append_event, read_from_cursor
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


__all__ = ["FsTranscriptLog"]
