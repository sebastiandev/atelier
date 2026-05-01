"""Workspace filesystem adapter.

Path computation, atomic JSON/text writes, and NDJSON transcript I/O. The
canonical layout is documented in `architecture-atelier-2026-04-30.md`
§ Filesystem Layout. Slug validation lives in `paths.py` so a malicious or
buggy upstream can't construct a path outside the workspace via traversal.
"""

from src.infrastructure.filesystem.atomic import (
    atomic_write_bytes,
    atomic_write_json,
    atomic_write_text,
)
from src.infrastructure.filesystem.ndjson import append_event, read_from_cursor
from src.infrastructure.filesystem.paths import WorkspacePaths

__all__ = [
    "WorkspacePaths",
    "append_event",
    "atomic_write_bytes",
    "atomic_write_json",
    "atomic_write_text",
    "read_from_cursor",
]
