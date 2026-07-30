"""Filesystem adapter for source-backed planning documents."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from src.domain.planning import manifest_keys
from src.infrastructure.filesystem.atomic import atomic_write_json, atomic_write_text
from src.infrastructure.filesystem.paths import WorkspacePaths


class FsPlanningFiles:
    """Read/write planning state and framework source files."""

    def __init__(self, paths: WorkspacePaths) -> None:
        self._paths = paths

    def bind_working_root(self, work_slug: str, root_path: str) -> str:
        root = Path(root_path).expanduser().resolve()
        if not root.exists():
            raise ValueError(f"planning root does not exist: {root}")
        if not root.is_dir():
            raise ValueError(f"planning root is not a directory: {root}")
        # No pointer to write: the manifest records root_path and lives at a
        # known Atelier path, so the working root is read back from it.
        return str(root)

    def working_root(self, work_slug: str) -> str | None:
        manifest = self.read_manifest(work_slug)
        root = manifest.get("root_path") if manifest else None
        return root if isinstance(root, str) and root else None

    def planning_path(self, work_slug: str) -> str:
        return str(self._planning_dir(work_slug))

    def plan_artifacts_path(self, work_slug: str) -> str:
        return str(self._plan_artifacts_path(work_slug))

    def ensure_plan_dir(self, work_slug: str) -> None:
        self._planning_dir(work_slug).mkdir(parents=True, exist_ok=True)

    def read_manifest(self, work_slug: str) -> dict[str, Any] | None:
        return _read_json(self._planning_dir(work_slug) / "manifest.json")

    def write_manifest(self, work_slug: str, data: dict[str, Any]) -> None:
        atomic_write_json(self._planning_dir(work_slug) / "manifest.json", data)

    def read_text(self, work_slug: str, rel_path: str) -> str | None:
        try:
            return self._resolve(work_slug, rel_path).read_text(encoding="utf-8")
        except FileNotFoundError:
            return None

    def read_text_at(self, root_path: str, rel_path: str) -> str | None:
        try:
            return _resolve_under_root(root_path, rel_path).read_text(encoding="utf-8")
        except FileNotFoundError:
            return None

    def write_text(self, work_slug: str, rel_path: str, content: str) -> None:
        atomic_write_text(self._resolve(work_slug, rel_path), content)

    def list_markdown(self, work_slug: str, rel_dir: str) -> list[str]:
        directory = self._resolve(work_slug, rel_dir)
        try:
            entries = list(directory.iterdir())
        except FileNotFoundError:
            return []
        rels: list[str] = []
        for entry in entries:
            if entry.is_file() and entry.suffix == ".md" and not entry.name.startswith("."):
                rels.append(f"{rel_dir}/{entry.name}")
        return sorted(rels)

    def absolute_path(self, work_slug: str, rel_path: str) -> str:
        return str(self._resolve(work_slug, rel_path))

    def _resolve(self, work_slug: str, rel_path: str) -> Path:
        return _resolve_under_root(str(self._plan_artifacts_path(work_slug)), rel_path)

    def _planning_dir(self, work_slug: str) -> Path:
        return self._paths.planning_dir(work_slug)

    def _plan_artifacts_path(self, work_slug: str) -> Path:
        root = self.working_root(work_slug)
        if root is None:
            raise ValueError(f"planning root is not bound: {work_slug}")
        manifest = self.read_manifest(work_slug)
        dir_value = _str_or_none(manifest_keys.plan_artifacts_dir(manifest))
        path_value = _str_or_none(manifest_keys.plan_artifacts_path(manifest))
        if path_value:
            path = Path(path_value).expanduser()
            return path if path.is_absolute() else Path(root).expanduser().resolve() / path
        if dir_value:
            return _resolve_under_root(root, dir_value)
        # A materialized plan always records one of the keys above; fall back
        # to the work root rather than the old Atelier-state location.
        return Path(root).expanduser().resolve()


def _resolve_under_root(root_path: str, rel_path: str) -> Path:
    root = Path(root_path).expanduser().resolve()
    rel = Path(rel_path)
    if rel.is_absolute() or any(part in {"", ".", ".."} for part in rel.parts):
        raise ValueError(f"invalid planning path: {rel_path!r}")
    if "\x00" in rel_path:
        raise ValueError(f"invalid planning path: {rel_path!r}")
    return root / rel


def _str_or_none(value: object) -> str | None:
    return value if isinstance(value, str) and value else None


def _read_json(path: Path) -> dict[str, Any] | None:
    try:
        data = json.loads(path.read_bytes())
    except (FileNotFoundError, json.JSONDecodeError):
        return None
    return data if isinstance(data, dict) else None


__all__ = ["FsPlanningFiles"]
