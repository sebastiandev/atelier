"""Filesystem adapter for chat metadata and transcripts."""

import shutil
from collections.abc import Iterator
from typing import Any

from src.infrastructure.filesystem.atomic import atomic_write_json, atomic_write_text
from src.infrastructure.filesystem.ndjson import (
    append_event,
    last_seq,
    read_before,
    read_from_cursor,
    read_recent_by_type,
    read_tail,
)
from src.infrastructure.filesystem.paths import WorkspacePaths


class FsChatFiles:
    def __init__(self, paths: WorkspacePaths) -> None:
        self._paths = paths

    def ensure_chat_dir(self, chat_slug: str) -> None:
        self._paths.chat_dir(chat_slug).mkdir(parents=True, exist_ok=True)

    def remove_chat_dir(self, chat_slug: str) -> None:
        # ``chat_dir`` validates the slug, so this can only remove data
        # underneath the Atelier chats root. Ignore missing dirs for
        # idempotent retry/delete behavior.
        shutil.rmtree(self._paths.chat_dir(chat_slug), ignore_errors=True)

    def write_chat_json(self, chat_slug: str, data: dict[str, object]) -> None:
        atomic_write_json(self._paths.chat_json(chat_slug), data)

    def read_chat_json(self, chat_slug: str) -> dict[str, object] | None:
        # Reserved for future chat reconcile. Current read paths use SQL
        # for metadata and transcript.ndjson for messages.
        try:
            import json

            raw = self._paths.chat_json(chat_slug).read_bytes()
            decoded = json.loads(raw)
        except (FileNotFoundError, json.JSONDecodeError):
            return None
        return decoded if isinstance(decoded, dict) else None

    def append_transcript_event(
        self, chat_slug: str, event: dict[str, object]
    ) -> None:
        append_event(self._paths.chat_transcript(chat_slug), event)

    def last_seq(self, chat_slug: str) -> int:
        return last_seq(self._paths.chat_transcript(chat_slug))

    def read_transcript(self, chat_slug: str) -> Iterator[dict[str, Any]]:
        return read_from_cursor(self._paths.chat_transcript(chat_slug), 0)

    def read_transcript_before(
        self, chat_slug: str, before_seq: int, limit: int
    ) -> Iterator[dict[str, Any]]:
        return read_before(self._paths.chat_transcript(chat_slug), before_seq, limit)

    def read_transcript_tail(
        self,
        chat_slug: str,
        *,
        cursor: int,
        limit: int,
        before_seq: int | None = None,
    ) -> Iterator[dict[str, Any]]:
        return read_tail(
            self._paths.chat_transcript(chat_slug),
            cursor=cursor,
            limit=limit,
            before_seq=before_seq,
        )

    def read_transcript_recent_by_type(
        self,
        chat_slug: str,
        event_types: set[str],
        *,
        cursor: int,
        limit: int,
        before_seq: int | None = None,
    ) -> Iterator[dict[str, Any]]:
        return read_recent_by_type(
            self._paths.chat_transcript(chat_slug),
            event_types,
            cursor=cursor,
            limit=limit,
            before_seq=before_seq,
        )

    def write_chat_compaction_doc(
        self, chat_slug: str, filename: str, content: str
    ) -> str:
        _validate_filename(filename)
        target = self._paths.chat_compactions_dir(chat_slug) / filename
        atomic_write_text(target, content)
        return str(target)

    def read_chat_compaction_doc(
        self, chat_slug: str, filename: str
    ) -> tuple[str, str] | None:
        _validate_filename(filename)
        target = self._paths.chat_compactions_dir(chat_slug) / filename
        try:
            return str(target), target.read_text()
        except FileNotFoundError:
            return None


def _validate_filename(name: str) -> None:
    if not name:
        raise ValueError("filename cannot be empty")
    if name.startswith("."):
        raise ValueError(f"filename cannot start with '.': {name!r}")
    if "/" in name or "\\" in name or "\x00" in name:
        raise ValueError(f"filename contains path separator or null: {name!r}")


__all__ = ["FsChatFiles"]
