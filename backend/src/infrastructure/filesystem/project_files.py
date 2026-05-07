"""FsProjectFiles adapter — atomic project.json writes under <workspace>/projects/.

Mirrors ``FsWorkspaceFiles`` for the project tree. Project dirs are
created on first write; deletion clears the dir entirely (project owns
no children today, so a single rmtree is safe).
"""

import json
import shutil
from pathlib import Path
from typing import Any

from src.infrastructure.filesystem.atomic import atomic_write_json
from src.infrastructure.filesystem.paths import WorkspacePaths


class FsProjectFiles:
    def __init__(self, paths: WorkspacePaths) -> None:
        self._paths = paths

    def ensure_project_dir(self, project_slug: str) -> None:
        self._paths.project_dir(project_slug).mkdir(parents=True, exist_ok=True)

    def write_project_json(self, project_slug: str, data: dict[str, Any]) -> None:
        atomic_write_json(self._paths.project_json(project_slug), data)

    def read_project_json(self, project_slug: str) -> dict[str, Any] | None:
        return _read_json(self._paths.project_json(project_slug))

    def list_project_slugs(self) -> list[str]:
        return _list_valid_subdirs(self._paths.projects_dir())

    def delete_project_dir(self, project_slug: str) -> None:
        target = self._paths.project_dir(project_slug)
        if target.exists():
            shutil.rmtree(target)


def _read_json(path: Path) -> dict[str, Any] | None:
    try:
        raw = path.read_bytes()
    except FileNotFoundError:
        return None
    try:
        decoded = json.loads(raw)
    except json.JSONDecodeError:
        return None
    return decoded if isinstance(decoded, dict) else None


def _list_valid_subdirs(parent: Path) -> list[str]:
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


__all__ = ["FsProjectFiles"]
