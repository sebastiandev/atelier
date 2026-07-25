"""Ports for source-backed Work planning."""

from __future__ import annotations

from typing import Any, Protocol

from src.domain.loop.ports import LoopWorkingRootRepository
from src.domain.planning.models import PlanningSession


class PlanningSessionRepository(LoopWorkingRootRepository, Protocol):
    """SQL-side storage for one Work's planning setup."""

    def upsert_session(self, session: PlanningSession) -> PlanningSession:
        """Create or replace the planning session for ``session.work_slug``."""
        ...

    def get_by_work_slug(self, work_slug: str) -> PlanningSession | None:
        """Return the persisted planning session for a Work, if any."""
        ...

    def get_by_chat_slug(self, chat_slug: str) -> PlanningSession | None:
        """Return the persisted planning session for a Planning chat, if any."""
        ...


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

    def artifact_root_path(self, work_slug: str) -> str:
        """Absolute path to framework-generated planning artifacts."""
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
        """Read a UTF-8 source file by artifact-root-relative path."""
        ...

    def write_text(self, work_slug: str, rel_path: str, content: str) -> None:
        """Atomically write a UTF-8 source file by artifact-root-relative path."""
        ...

    def list_markdown(self, work_slug: str, rel_dir: str) -> list[str]:
        """List immediate Markdown files below an artifact-root-relative directory."""
        ...

    def absolute_path(self, work_slug: str, rel_path: str) -> str:
        """Resolve an artifact-root-relative path to an absolute display path."""
        ...

    def read_text_at(self, root_path: str, rel_path: str) -> str | None:
        """Read a UTF-8 source file by explicit root-relative path."""
        ...


__all__ = ["PlanningFiles", "PlanningSessionRepository"]
