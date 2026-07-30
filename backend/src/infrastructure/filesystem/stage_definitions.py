"""Filesystem adapter for reusable standalone stage definitions."""

from __future__ import annotations

import shutil
from pathlib import Path
from typing import Any

import yaml  # type: ignore[import-untyped]

from src.domain.loop.definitions import LoopSchemaOutdated
from src.domain.loop.dtos import (
    LoopOutcome,
    LoopRetryPolicy,
    LoopStepDefinition,
    LoopStepKind,
    StageDefinition,
    StageDefinitionScope,
)
from src.domain.loop.snapshots import loop_stage_from_snapshot, loop_stage_snapshot
from src.domain.loop.stages import (
    StageDefinitionConflict,
    StageDefinitionNotFound,
    prepare_stage_definition,
)
from src.infrastructure.filesystem.atomic import atomic_write_text

_SCHEMA_VERSION = 1


class FsStageDefinitionRepository:
    """Store reusable stages under ``<root>/.atelier/stages``."""

    def list_definitions(
        self,
        root_path: str,
        *,
        scope: StageDefinitionScope = StageDefinitionScope.LIBRARY,
    ) -> list[StageDefinition]:
        try:
            entries = sorted(_stages_root(root_path).iterdir(), key=lambda path: path.name)
        except FileNotFoundError:
            return []
        return [
            self._read(entry, scope=scope)
            for entry in entries
            if entry.is_dir() and not entry.name.startswith(".")
        ]

    def get_definition(
        self,
        root_path: str,
        definition_id: str,
        *,
        scope: StageDefinitionScope = StageDefinitionScope.LIBRARY,
    ) -> StageDefinition | None:
        directory = _definition_dir(root_path, definition_id)
        return self._read(directory, scope=scope) if directory.is_dir() else None

    def save_definition(
        self,
        root_path: str,
        definition: StageDefinition,
        *,
        expected_revision: str | None,
    ) -> StageDefinition:
        current = self.get_definition(root_path, definition.definition_id)
        if current is not None and current.revision != expected_revision:
            raise StageDefinitionConflict(f"stage definition changed: {definition.definition_id}")
        if current is None and expected_revision is not None:
            raise StageDefinitionConflict(
                f"stage definition no longer exists: {definition.definition_id}"
            )
        prepared = prepare_stage_definition(definition)
        directory = _definition_dir(root_path, prepared.definition_id)
        directory.mkdir(parents=True, exist_ok=True)
        atomic_write_text(
            directory / "stage.yaml",
            yaml.safe_dump(_to_data(prepared), sort_keys=False, allow_unicode=False),
        )
        return self._read(directory, scope=StageDefinitionScope.LIBRARY)

    def delete_definition(self, root_path: str, definition_id: str) -> None:
        directory = _definition_dir(root_path, definition_id)
        if not directory.is_dir():
            raise StageDefinitionNotFound(f"stage definition not found: {definition_id}")
        shutil.rmtree(directory)

    def definition_dir(self, root_path: str, definition_id: str) -> Path:
        """The on-disk directory for one stage, whether or not it exists.

        Single source of the ``<root>/stages/<id>`` layout, so reveal and
        the like don't rebuild the path and drift from it.
        """
        return _definition_dir(root_path, definition_id)

    def _read(
        self,
        directory: Path,
        *,
        scope: StageDefinitionScope,
    ) -> StageDefinition:
        try:
            raw = yaml.safe_load((directory / "stage.yaml").read_text(encoding="utf-8"))
            if not isinstance(raw, dict):
                raise ValueError("stage.yaml must contain a mapping")
            return prepare_stage_definition(_from_data(directory, raw, scope=scope))
        except (OSError, TypeError, ValueError, yaml.YAMLError) as exc:
            return _invalid(directory.name, str(exc), scope)


def _from_data(
    directory: Path,
    raw: dict[str, Any],
    *,
    scope: StageDefinitionScope,
) -> StageDefinition:
    if raw.get("schema_version") != _SCHEMA_VERSION:
        raise ValueError(f"unsupported schema_version: {raw.get('schema_version')!r}")
    definition_id = _required(raw, "id")
    if definition_id != directory.name:
        raise ValueError("stage id must match its directory name")
    stage_data = raw.get("stage")
    if not isinstance(stage_data, dict):
        raise ValueError("stage must be a mapping")
    stage_data = dict(stage_data)
    instructions = stage_data.get("instructions")
    if (
        isinstance(instructions, str)
        and instructions.rstrip().endswith(".md")
        and "\n" not in instructions
    ):
        raise LoopSchemaOutdated(
            f"{definition_id}: instructions is a file path, not inline text; "
            "run scripts/migrate-loops.py"
        )
    outcomes = raw.get("outcomes")
    if not isinstance(outcomes, list):
        raise ValueError("outcomes must be a list")
    return StageDefinition(
        definition_id=definition_id,
        name=_required(raw, "name"),
        description=_optional(raw.get("description")),
        scope=scope,
        forked_from=_optional(raw.get("forked_from")) or None,
        stage=loop_stage_from_snapshot(stage_data),
        outcomes=tuple(LoopOutcome(str(item)) for item in outcomes),
    )


def _to_data(definition: StageDefinition) -> dict[str, Any]:
    stage = loop_stage_snapshot(definition.stage)
    return {
        "schema_version": _SCHEMA_VERSION,
        "id": definition.definition_id,
        "name": definition.name,
        "description": definition.description,
        **({"forked_from": definition.forked_from} if definition.forked_from else {}),
        "outcomes": [item.value for item in definition.outcomes],
        "stage": stage,
    }


def _invalid(
    definition_id: str,
    error: str,
    scope: StageDefinitionScope,
) -> StageDefinition:
    return StageDefinition(
        definition_id=definition_id,
        name=definition_id.replace("-", " ").title(),
        description="Repository stage could not be loaded.",
        scope=scope,
        stage=LoopStepDefinition(
            step_id=definition_id,
            name=definition_id,
            kind=LoopStepKind.AGENT_TASK,
            retry=LoopRetryPolicy(),
        ),
        outcomes=(),
        errors=(error,),
    )


def _stages_root(root_path: str) -> Path:
    root = Path(root_path).expanduser().resolve()
    if not root.is_dir():
        raise ValueError(f"stage library root is not a directory: {root}")
    return root / "stages"


def _definition_dir(root_path: str, definition_id: str) -> Path:
    if (
        not definition_id
        or definition_id in {".", ".."}
        or "/" in definition_id
        or "\\" in definition_id
    ):
        raise ValueError(f"invalid stage definition id: {definition_id!r}")
    return _stages_root(root_path) / definition_id


def _required(data: dict[str, Any], key: str) -> str:
    value = data.get(key)
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{key} must be a non-empty string")
    return value.strip()


def _optional(value: object) -> str:
    return value.strip() if isinstance(value, str) else ""


__all__ = ["FsStageDefinitionRepository"]
