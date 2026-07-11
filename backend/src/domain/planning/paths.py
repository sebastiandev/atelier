"""Canonical path helpers for source-backed planning."""

from __future__ import annotations

from pathlib import PurePosixPath

ATELIER_PLANNING_ROOT = ".atelier/planning"


def atelier_planning_rel_path(work_slug: str) -> str:
    """Return the repo-relative folder for Atelier-owned planning state.

    Preconditions: ``work_slug`` is the canonical Work slug.
    Postconditions: returns a POSIX relative path; no filesystem state changes.
    """
    segment = _safe_segment(work_slug)
    return f"{ATELIER_PLANNING_ROOT}/{segment}"


def _safe_segment(value: str) -> str:
    segment = value.strip()
    rel = PurePosixPath(segment)
    if (
        not segment
        or rel.is_absolute()
        or len(rel.parts) != 1
        or rel.parts[0] in {"", ".", ".."}
        or "\x00" in segment
    ):
        raise ValueError(f"invalid planning path segment: {value!r}")
    return segment


__all__ = ["ATELIER_PLANNING_ROOT", "atelier_planning_rel_path"]
