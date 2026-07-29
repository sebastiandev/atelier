"""Manifest keys for the plan-artifacts location, old and new.

``manifest.json`` files written before the rename carry ``artifact_root``
and ``artifact_root_path``. Reads accept either spelling so an existing
work keeps opening; writes only emit the current one, so a manifest
converges on the new keys the next time anything touches it.
``scripts/migrate-plan-manifests.py`` converts them eagerly.
"""

from __future__ import annotations

from typing import Any

PLAN_ARTIFACTS_DIR_KEY = "plan_artifacts_dir"
PLAN_ARTIFACTS_PATH_KEY = "plan_artifacts_path"

LEGACY_PLAN_ARTIFACTS_DIR_KEY = "artifact_root"
LEGACY_PLAN_ARTIFACTS_PATH_KEY = "artifact_root_path"


def plan_artifacts_dir(manifest: dict[str, Any] | None) -> Any:
    """The manifest's plan-artifacts folder, relative to the work root."""
    return _either(manifest, PLAN_ARTIFACTS_DIR_KEY, LEGACY_PLAN_ARTIFACTS_DIR_KEY)


def plan_artifacts_path(manifest: dict[str, Any] | None) -> Any:
    """The manifest's absolute plan-artifacts path."""
    return _either(manifest, PLAN_ARTIFACTS_PATH_KEY, LEGACY_PLAN_ARTIFACTS_PATH_KEY)


def _either(manifest: dict[str, Any] | None, current: str, legacy: str) -> Any:
    if not manifest:
        return None
    value = manifest.get(current)
    return value if value else manifest.get(legacy)


__all__ = [
    "LEGACY_PLAN_ARTIFACTS_DIR_KEY",
    "LEGACY_PLAN_ARTIFACTS_PATH_KEY",
    "PLAN_ARTIFACTS_DIR_KEY",
    "PLAN_ARTIFACTS_PATH_KEY",
    "plan_artifacts_dir",
    "plan_artifacts_path",
]
