"""Ports for source-backed Work planning."""

from __future__ import annotations

from typing import Any, Protocol


class PlanningFiles(Protocol):
    """Filesystem side of source-backed planning."""

    def bind_working_root(self, work_slug: str, root_path: str) -> str:
        """Persist the working root used for this work's planning docs."""
        ...

    def working_root(self, work_slug: str) -> str | None:
        """Return the bound working root, or None when planning is absent."""
        ...

    def planning_path(self, work_slug: str) -> str:
        """Absolute path to the work's planning folder."""
        ...

    def ensure_plan_dir(self, work_slug: str) -> None:
        """Create the planning folder if it does not exist."""
        ...

    def read_manifest(self, work_slug: str) -> dict[str, Any] | None:
        """Read manifest.json, returning None when absent or invalid."""
        ...

    def write_manifest(self, work_slug: str, data: dict[str, Any]) -> None:
        """Atomically write manifest.json."""
        ...

    def read_text(self, work_slug: str, rel_path: str) -> str | None:
        """Read a UTF-8 source file by plan-relative path."""
        ...

    def write_text(self, work_slug: str, rel_path: str, content: str) -> None:
        """Atomically write a UTF-8 source file by plan-relative path."""
        ...

    def list_markdown(self, work_slug: str, rel_dir: str) -> list[str]:
        """List immediate Markdown files below a plan-relative directory."""
        ...

    def absolute_path(self, work_slug: str, rel_path: str) -> str:
        """Resolve a plan-relative path to an absolute display path."""
        ...


__all__ = ["PlanningFiles"]
