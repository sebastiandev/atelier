"""WorkspaceFiles adapter — atomic JSON/text writes against the workspace tree.

Composes `WorkspacePaths` (validation + path computation) with the atomic
write helpers. `read_*_json` returns ``None`` on a missing or corrupt
file so reconcile can skip it without raising.
"""

import json
import shutil
from typing import Any

from src.infrastructure.filesystem.atomic import atomic_write_json, atomic_write_text
from src.infrastructure.filesystem.paths import WorkspacePaths


class FsWorkspaceFiles:
    def __init__(self, paths: WorkspacePaths) -> None:
        self._paths = paths

    def ensure_work_dir(self, work_slug: str) -> None:
        self._paths.work_dir(work_slug).mkdir(parents=True, exist_ok=True)

    def ensure_agent_dir(self, work_slug: str, agent_slug: str) -> None:
        self._paths.agent_dir(work_slug, agent_slug).mkdir(parents=True, exist_ok=True)

    def remove_agent_dir(self, work_slug: str, agent_slug: str) -> None:
        # ``ignore_errors`` covers the "already gone" case + any race with
        # a transcript reader holding the file open on Windows. The dir is
        # always under the workspace root (slug validation in WorkspacePaths).
        shutil.rmtree(self._paths.agent_dir(work_slug, agent_slug), ignore_errors=True)

    def write_work_json(self, work_slug: str, data: dict[str, Any]) -> None:
        atomic_write_json(self._paths.work_json(work_slug), data)

    def read_work_json(self, work_slug: str) -> dict[str, Any] | None:
        return _read_json(self._paths.work_json(work_slug))

    def write_brief(self, work_slug: str, content: str) -> None:
        atomic_write_text(self._paths.brief(work_slug), content)

    def write_agent_json(self, work_slug: str, agent_slug: str, data: dict[str, Any]) -> None:
        atomic_write_json(self._paths.agent_dir(work_slug, agent_slug) / "agent.json", data)

    def read_agent_json(self, work_slug: str, agent_slug: str) -> dict[str, Any] | None:
        return _read_json(self._paths.agent_dir(work_slug, agent_slug) / "agent.json")

    def write_handoff_doc(self, work_slug: str, filename: str, content: str) -> str:
        _validate_filename(filename)
        target = self._paths.work_dir(work_slug) / "handoffs" / filename
        atomic_write_text(target, content)
        return str(target)

    def write_agent_compaction_doc(
        self, work_slug: str, agent_slug: str, filename: str, content: str
    ) -> str:
        _validate_filename(filename)
        target = self._paths.agent_compactions_dir(work_slug, agent_slug) / filename
        atomic_write_text(target, content)
        return str(target)

    def read_agent_compaction_doc(
        self, work_slug: str, agent_slug: str, filename: str
    ) -> tuple[str, str] | None:
        _validate_filename(filename)
        target = self._paths.agent_compactions_dir(work_slug, agent_slug) / filename
        try:
            return str(target), target.read_text()
        except FileNotFoundError:
            return None

    def write_agent_context_file(
        self, work_slug: str, agent_slug: str, filename: str, content: str
    ) -> str:
        _validate_filename(filename)
        target = self._paths.agent_context_dir(work_slug, agent_slug) / filename
        atomic_write_text(target, content)
        return str(target)

    def write_agent_context_index(
        self, work_slug: str, agent_slug: str, content: str
    ) -> str:
        target = self._paths.agent_context_index(work_slug, agent_slug)
        atomic_write_text(target, content)
        return str(target)

    def list_work_slugs(self) -> list[str]:
        return _list_valid_subdirs(self._paths.works_dir())

    def list_agent_slugs(self, work_slug: str) -> list[str]:
        return _list_valid_subdirs(self._paths.work_dir(work_slug) / "agents")


def _read_json(path: Any) -> dict[str, Any] | None:
    try:
        raw = path.read_bytes()
    except FileNotFoundError:
        return None
    try:
        decoded = json.loads(raw)
    except json.JSONDecodeError:
        return None
    return decoded if isinstance(decoded, dict) else None


def _list_valid_subdirs(parent: Any) -> list[str]:
    """Return immediate subdirectory names that look like slugs.

    Filters hidden dirs (`.foo`) and anything that wouldn't survive slug
    validation downstream. Missing parent → empty list.
    """
    try:
        entries = list(parent.iterdir())
    except FileNotFoundError:
        return []
    out: list[str] = []
    for entry in entries:
        if not entry.is_dir():
            continue
        name = entry.name
        if name.startswith(".") or "/" in name or "\\" in name:
            continue
        out.append(name)
    return sorted(out)


def _validate_filename(name: str) -> None:
    if not name:
        raise ValueError("filename cannot be empty")
    if name.startswith("."):
        raise ValueError(f"filename cannot start with '.': {name!r}")
    if "/" in name or "\\" in name or "\x00" in name:
        raise ValueError(f"filename contains path separator or null: {name!r}")


__all__ = ["FsWorkspaceFiles"]
