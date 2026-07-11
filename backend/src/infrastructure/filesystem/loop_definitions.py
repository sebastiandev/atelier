"""Filesystem adapter for repository-owned YAML/Markdown loop definitions."""

from __future__ import annotations

import shutil
from pathlib import Path
from typing import Any

import yaml  # type: ignore[import-untyped]

from src.domain.loop.definitions import (
    LoopDefinitionConflict,
    LoopDefinitionNotFound,
    prepare_definition,
)
from src.domain.loop.dtos import (
    LoopAgentPolicy,
    LoopContextKind,
    LoopContextReference,
    LoopDefinition,
    LoopDefinitionScope,
    LoopOutcome,
    LoopPermission,
    LoopReportField,
    LoopReportSchema,
    LoopRetryPolicy,
    LoopSessionPolicy,
    LoopStepDefinition,
    LoopStepKind,
)
from src.infrastructure.filesystem.atomic import atomic_write_text

_SCHEMA_VERSION = 1
_REPORT_SCHEMA = LoopReportSchema(
    schema_id="multi-stage-loop-report",
    fields=(LoopReportField("summary", "Summary", allow_explicit_none=False),),
)


class FsLoopDefinitionRepository:
    """Store custom loop definitions under ``<root>/.atelier/loops``."""

    def list_definitions(self, root_path: str) -> list[LoopDefinition]:
        root = _loops_root(root_path)
        try:
            entries = sorted(root.iterdir(), key=lambda path: path.name)
        except FileNotFoundError:
            return []
        return [
            self._read_definition(entry)
            for entry in entries
            if entry.is_dir() and not entry.name.startswith(".")
        ]

    def get_definition(
        self, root_path: str, definition_id: str
    ) -> LoopDefinition | None:
        directory = _definition_dir(root_path, definition_id)
        if not directory.is_dir():
            return None
        return self._read_definition(directory)

    def save_definition(
        self,
        root_path: str,
        definition: LoopDefinition,
        *,
        expected_revision: str | None,
    ) -> LoopDefinition:
        directory = _definition_dir(root_path, definition.definition_id)
        current = self.get_definition(root_path, definition.definition_id)
        if current is not None and expected_revision != current.revision:
            raise LoopDefinitionConflict(
                f"loop definition changed: {definition.definition_id}"
            )
        if current is None and expected_revision is not None:
            raise LoopDefinitionConflict(
                f"loop definition no longer exists: {definition.definition_id}"
            )

        prepared = prepare_definition(definition)
        directory.mkdir(parents=True, exist_ok=True)
        steps_dir = directory / "steps"
        for stage in prepared.stages:
            if stage.kind in {LoopStepKind.AGENT_TASK, LoopStepKind.AGENT_REVIEW}:
                atomic_write_text(
                    steps_dir / f"{stage.step_id}.md",
                    stage.instructions.rstrip() + "\n",
                )
        data = _to_yaml_data(prepared)
        atomic_write_text(
            directory / "loop.yaml",
            yaml.safe_dump(data, sort_keys=False, allow_unicode=False),
        )
        return self._read_definition(directory)

    def delete_definition(self, root_path: str, definition_id: str) -> None:
        directory = _definition_dir(root_path, definition_id)
        if not directory.is_dir():
            raise LoopDefinitionNotFound(f"loop definition not found: {definition_id}")
        shutil.rmtree(directory)

    def _read_definition(self, directory: Path) -> LoopDefinition:
        try:
            raw = yaml.safe_load((directory / "loop.yaml").read_text(encoding="utf-8"))
            if not isinstance(raw, dict):
                raise ValueError("loop.yaml must contain a mapping")
            definition = _from_yaml_data(directory, raw)
            return prepare_definition(definition)
        except (OSError, ValueError, TypeError, yaml.YAMLError) as exc:
            return _invalid_definition(directory.name, str(exc))


def _from_yaml_data(directory: Path, raw: dict[str, Any]) -> LoopDefinition:
    version = raw.get("schema_version")
    if version != _SCHEMA_VERSION:
        raise ValueError(f"unsupported schema_version: {version!r}")
    definition_id = _required_str(raw, "id")
    if definition_id != directory.name:
        raise ValueError("loop id must match its directory name")
    raw_steps = raw.get("steps")
    if not isinstance(raw_steps, list):
        raise ValueError("steps must be a list")
    stages = tuple(_stage_from_data(directory, item) for item in raw_steps)
    return LoopDefinition(
        definition_id=definition_id,
        name=_required_str(raw, "name"),
        trigger="artifact_or_objective",
        report_schema=_REPORT_SCHEMA,
        retry_limit=max((stage.retry.max_attempts for stage in stages), default=1),
        description=_optional_str(raw.get("description")),
        scope=LoopDefinitionScope.REPOSITORY,
        forked_from=_optional_str(raw.get("forked_from")) or None,
        stages=stages,
    )


def _stage_from_data(directory: Path, value: object) -> LoopStepDefinition:
    if not isinstance(value, dict):
        raise ValueError("each stage must be a mapping")
    step_id = _required_str(value, "id")
    kind = LoopStepKind(_required_str(value, "kind"))
    instruction_ref = _optional_str(value.get("instructions"))
    instructions = ""
    if instruction_ref:
        path = _resolve_under(directory, instruction_ref)
        instructions = path.read_text(encoding="utf-8")

    context_raw = value.get("context", [])
    if not isinstance(context_raw, list):
        raise ValueError(f"stage {step_id!r} context must be a list")
    agent_raw = value.get("agent")
    retry_raw = value.get("retry", {})
    transitions_raw = value.get("transitions", {})
    check_raw = value.get("check", {})
    return LoopStepDefinition(
        step_id=step_id,
        name=_required_str(value, "name"),
        kind=kind,
        instructions=instructions,
        context=tuple(_context_from_data(item) for item in context_raw),
        agent=_agent_from_data(agent_raw, kind),
        report_contract=_optional_str(value.get("report_contract")) or "generic",
        retry=_retry_from_data(retry_raw),
        transitions=_transitions_from_data(transitions_raw),
        check_adapter=(
            _optional_str(check_raw.get("adapter"))
            if isinstance(check_raw, dict)
            else None
        )
        or None,
        check_command=(
            tuple(_str_list(check_raw.get("command", [])))
            if isinstance(check_raw, dict)
            else ()
        ),
    )


def _context_from_data(value: object) -> LoopContextReference:
    if not isinstance(value, dict):
        raise ValueError("context entry must be a mapping")
    return LoopContextReference(
        kind=LoopContextKind(_required_str(value, "kind")),
        required=bool(value.get("required", False)),
        paths=tuple(_str_list(value.get("paths", []))),
        step=_optional_str(value.get("step")) or None,
        ref=_optional_str(value.get("ref")) or None,
    )


def _agent_from_data(value: object, kind: LoopStepKind) -> LoopAgentPolicy | None:
    if kind not in {LoopStepKind.AGENT_TASK, LoopStepKind.AGENT_REVIEW}:
        return None
    if not isinstance(value, dict):
        raise ValueError("agent stage requires an agent mapping")
    return LoopAgentPolicy(
        session=LoopSessionPolicy(_optional_str(value.get("session")) or "fresh"),
        permissions=LoopPermission(
            _optional_str(value.get("permissions")) or "read"
        ),
        provider=_optional_str(value.get("provider")) or None,
        model=_optional_str(value.get("model")) or None,
        effort=_optional_str(value.get("effort")) or None,
    )


def _retry_from_data(value: object) -> LoopRetryPolicy:
    if not isinstance(value, dict):
        raise ValueError("retry must be a mapping")
    return LoopRetryPolicy(
        max_attempts=_positive_int(value.get("max_attempts"), 2),
        timeout_minutes=_positive_int(value.get("timeout_minutes"), 20),
    )


def _transitions_from_data(value: object) -> dict[LoopOutcome, str | None]:
    if not isinstance(value, dict):
        raise ValueError("transitions must be a mapping")
    return {
        outcome: _optional_str(value.get(outcome.value)) or None
        for outcome in LoopOutcome
        if outcome.value in value
    }


def _to_yaml_data(definition: LoopDefinition) -> dict[str, Any]:
    data: dict[str, Any] = {
        "schema_version": _SCHEMA_VERSION,
        "id": definition.definition_id,
        "name": definition.name,
        "description": definition.description,
    }
    if definition.forked_from:
        data["forked_from"] = definition.forked_from
    data["steps"] = [_stage_to_data(stage) for stage in definition.stages]
    return data


def _stage_to_data(stage: LoopStepDefinition) -> dict[str, Any]:
    data: dict[str, Any] = {
        "id": stage.step_id,
        "name": stage.name,
        "kind": stage.kind.value,
    }
    if stage.kind in {LoopStepKind.AGENT_TASK, LoopStepKind.AGENT_REVIEW}:
        data["instructions"] = f"steps/{stage.step_id}.md"
    if stage.context:
        data["context"] = [_context_to_data(item) for item in stage.context]
    if stage.agent is not None:
        data["agent"] = {
            "session": stage.agent.session.value,
            "permissions": stage.agent.permissions.value,
            **({"provider": stage.agent.provider} if stage.agent.provider else {}),
            **({"model": stage.agent.model} if stage.agent.model else {}),
            **({"effort": stage.agent.effort} if stage.agent.effort else {}),
        }
    data["report_contract"] = stage.report_contract
    data["retry"] = {
        "max_attempts": stage.retry.max_attempts,
        "timeout_minutes": stage.retry.timeout_minutes,
    }
    if stage.transitions:
        data["transitions"] = {
            outcome.value: destination
            for outcome, destination in stage.transitions.items()
            if destination is not None
        }
    if stage.kind == LoopStepKind.DETERMINISTIC_CHECK:
        data["check"] = {
            "adapter": stage.check_adapter,
            "command": list(stage.check_command),
        }
    return data


def _context_to_data(context: LoopContextReference) -> dict[str, Any]:
    return {
        "kind": context.kind.value,
        "required": context.required,
        **({"paths": list(context.paths)} if context.paths else {}),
        **({"step": context.step} if context.step else {}),
        **({"ref": context.ref} if context.ref else {}),
    }


def _invalid_definition(definition_id: str, error: str) -> LoopDefinition:
    return LoopDefinition(
        definition_id=definition_id,
        name=definition_id.replace("-", " ").title(),
        trigger="artifact_or_objective",
        report_schema=_REPORT_SCHEMA,
        description="Repository loop could not be loaded.",
        scope=LoopDefinitionScope.REPOSITORY,
        errors=(error,),
    )


def _loops_root(root_path: str) -> Path:
    root = Path(root_path).expanduser().resolve()
    if not root.is_dir():
        raise ValueError(f"loop working root is not a directory: {root}")
    return root / ".atelier" / "loops"


def _definition_dir(root_path: str, definition_id: str) -> Path:
    if not definition_id or "/" in definition_id or "\\" in definition_id:
        raise ValueError(f"invalid loop definition id: {definition_id!r}")
    return _loops_root(root_path) / definition_id


def _resolve_under(root: Path, relative: str) -> Path:
    candidate = Path(relative)
    if candidate.is_absolute() or ".." in candidate.parts:
        raise ValueError(f"unsafe loop instruction path: {relative!r}")
    resolved = (root / candidate).resolve()
    if resolved != root.resolve() and root.resolve() not in resolved.parents:
        raise ValueError(f"unsafe loop instruction path: {relative!r}")
    return resolved


def _required_str(data: dict[str, Any], key: str) -> str:
    value = data.get(key)
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{key} must be a non-empty string")
    return value.strip()


def _optional_str(value: object) -> str:
    return value.strip() if isinstance(value, str) else ""


def _str_list(value: object) -> list[str]:
    if not isinstance(value, list) or not all(isinstance(item, str) for item in value):
        raise ValueError("expected a list of strings")
    return [item for item in value if item]


def _positive_int(value: object, default: int) -> int:
    return value if isinstance(value, int) and not isinstance(value, bool) else default


__all__ = ["FsLoopDefinitionRepository"]
