"""Filesystem adapter for repository-owned YAML/Markdown loop definitions."""

from __future__ import annotations

import shutil
from dataclasses import replace
from pathlib import Path
from typing import Any

import yaml  # type: ignore[import-untyped]

from src.domain.loop.definitions import (
    LoopDefinitionConflict,
    LoopDefinitionNotFound,
    LoopSchemaOutdated,
    prepare_definition,
)
from src.domain.loop.dtos import (
    AgentStage,
    ApprovalStage,
    CheckStage,
    LoopAgentPolicy,
    LoopContextKind,
    LoopContextReference,
    LoopDefinition,
    LoopDefinitionScope,
    LoopHistoryLevel,
    LoopOutcome,
    LoopPermission,
    LoopPrConfig,
    LoopReportField,
    LoopReportSchema,
    LoopRetryPolicy,
    LoopReviewGate,
    LoopReviewGateMode,
    LoopSessionPolicy,
    LoopStepDefinition,
    LoopStepKind,
    PrStage,
    ReviewStage,
    StageDefinition,
    StageDefinitionRef,
    TaskStage,
)
from src.domain.loop.snapshots import (
    history_or_raise,
    inputs_from_raw,
    loop_pr_config_from_snapshot,
    loop_pr_config_snapshot,
    raw_inputs,
    report_contract_from_raw,
    reports_from_raw,
    require_declared_sections,
    stage_overrides_from_snapshot,
    stage_overrides_snapshot,
)
from src.domain.loop.stage_builtins import builtin_stage_definition
from src.domain.loop.stages import StageDefinitionNotFound, resolve_stage_link
from src.infrastructure.filesystem.atomic import atomic_write_text
from src.infrastructure.filesystem.stage_definitions import FsStageDefinitionRepository

_SCHEMA_VERSION = 1
_REPORT_SCHEMA = LoopReportSchema(
    schema_id="multi-stage-loop-report",
    fields=(LoopReportField("summary", "Summary", allow_explicit_none=False),),
)


class FsLoopDefinitionRepository:
    """Store custom loop definitions under ``<root>/.atelier/loops``."""

    def __init__(self, stage_library_root: str | None = None) -> None:
        self._stage_library_root = stage_library_root

    def list_definitions(
        self,
        root_path: str,
        *,
        scope: LoopDefinitionScope = LoopDefinitionScope.LIBRARY,
    ) -> list[LoopDefinition]:
        root = _loops_root(root_path)
        try:
            entries = sorted(root.iterdir(), key=lambda path: path.name)
        except FileNotFoundError:
            return []
        return [
            self._read_definition(entry, scope=scope)
            for entry in entries
            if entry.is_dir() and not entry.name.startswith(".")
        ]

    def get_definition(
        self,
        root_path: str,
        definition_id: str,
        *,
        scope: LoopDefinitionScope = LoopDefinitionScope.LIBRARY,
    ) -> LoopDefinition | None:
        directory = _definition_dir(root_path, definition_id)
        if not directory.is_dir():
            return None
        return self._read_definition(directory, scope=scope)

    def save_definition(
        self,
        root_path: str,
        definition: LoopDefinition,
        *,
        expected_revision: str | None,
        scope: LoopDefinitionScope = LoopDefinitionScope.LIBRARY,
    ) -> LoopDefinition:
        directory = _definition_dir(root_path, definition.definition_id)
        current = self.get_definition(
            root_path,
            definition.definition_id,
            scope=scope,
        )
        if current is not None and expected_revision != current.revision:
            raise LoopDefinitionConflict(f"loop definition changed: {definition.definition_id}")
        if current is None and expected_revision is not None:
            raise LoopDefinitionConflict(
                f"loop definition no longer exists: {definition.definition_id}"
            )

        prepared = prepare_definition(
            replace(
                definition,
                stages=tuple(
                    resolve_stage_link(
                        stage,
                        _linked_source(
                            root_path,
                            stage.stage_ref.definition_id,
                            self._stage_library_root,
                        ),
                    )
                    if stage.stage_ref is not None
                    else stage
                    for stage in definition.stages
                ),
            )
        )
        directory.mkdir(parents=True, exist_ok=True)
        data = _to_yaml_data(prepared)
        atomic_write_text(
            directory / "loop.yaml",
            yaml.safe_dump(data, sort_keys=False, allow_unicode=False),
        )
        return self._read_definition(directory, scope=scope)

    def delete_definition(self, root_path: str, definition_id: str) -> None:
        directory = _definition_dir(root_path, definition_id)
        if not directory.is_dir():
            raise LoopDefinitionNotFound(f"loop definition not found: {definition_id}")
        shutil.rmtree(directory)

    def definition_dir(self, root_path: str, definition_id: str) -> Path:
        """The on-disk directory for one loop, whether or not it exists.

        The single source of the ``<root>/loops/<id>`` layout, so callers
        that only need the path (reveal) don't rebuild it and drift.
        """
        return _definition_dir(root_path, definition_id)

    def _read_definition(
        self,
        directory: Path,
        *,
        scope: LoopDefinitionScope,
    ) -> LoopDefinition:
        try:
            raw = yaml.safe_load((directory / "loop.yaml").read_text(encoding="utf-8"))
            if not isinstance(raw, dict):
                raise ValueError("loop.yaml must contain a mapping")
            definition = _from_yaml_data(
                directory,
                raw,
                scope=scope,
                stage_library_root=self._stage_library_root,
            )
            return prepare_definition(definition)
        except (OSError, ValueError, TypeError, yaml.YAMLError) as exc:
            return _invalid_definition(directory.name, str(exc), scope=scope)


def _from_yaml_data(
    directory: Path,
    raw: dict[str, Any],
    *,
    scope: LoopDefinitionScope,
    stage_library_root: str | None = None,
) -> LoopDefinition:
    version = raw.get("schema_version")
    if version != _SCHEMA_VERSION:
        raise ValueError(f"unsupported schema_version: {version!r}")
    if "steps" in raw and "stages" not in raw:
        raise LoopSchemaOutdated(
            f"{directory.name}: pre-refactor loop file (uses `steps:`); "
            "run scripts/migrate-loops.py"
        )
    definition_id = _required_str(raw, "id")
    if definition_id != directory.name:
        raise ValueError("loop id must match its directory name")
    raw_stages = raw.get("stages")
    if not isinstance(raw_stages, list):
        raise ValueError("stages must be a list")
    stages = tuple(
        _stage_from_data(directory, item, stage_library_root=stage_library_root)
        for item in raw_stages
    )
    return LoopDefinition(
        definition_id=definition_id,
        name=_required_str(raw, "name"),
        trigger="artifact_or_objective",
        report_schema=_REPORT_SCHEMA,
        retry_limit=max((stage.retry.max_attempts for stage in stages), default=1),
        description=_optional_str(raw.get("description")),
        scope=scope,
        forked_from=_optional_str(raw.get("forked_from")) or None,
        stages=stages,
    )


def _stage_from_data(
    directory: Path,
    value: object,
    *,
    stage_library_root: str | None = None,
) -> LoopStepDefinition:
    if not isinstance(value, dict):
        raise ValueError("each stage must be a mapping")
    step_id = _required_str(value, "id")
    use_ref = _optional_str(value.get("use"))
    if use_ref:
        source_id, _, pinned_rev = use_ref.partition("@")
        source_id = source_id.strip()
        source = _linked_source(str(directory.parents[1]), source_id, stage_library_root)
        raw_transitions = value.get("transitions", {})
        instance = LoopStepDefinition(
            step_id=step_id,
            name=source.name,
            kind=source.stage.kind,
            transitions=_transitions_from_data(raw_transitions),
            stage_ref=StageDefinitionRef(
                source_id,
                pinned_rev.strip() or source.revision,
            ),
            overrides=stage_overrides_from_snapshot(value.get("overrides")),
        )
        return resolve_stage_link(instance, source)
    kind = LoopStepKind(_required_str(value, "kind"))
    instructions = _optional_str(value.get("instructions")) or ""
    if instructions.rstrip().endswith(".md") and "\n" not in instructions:
        raise LoopSchemaOutdated(
            f"{step_id}: instructions is a file path, not inline text; "
            "run scripts/migrate-loops.py"
        )

    context_raw = raw_inputs(value)
    if not isinstance(context_raw, list):
        raise ValueError(f"stage {step_id!r} inputs must be a list")
    agent_raw = value.get("agent")
    retry_raw = value.get("retry", {})
    transitions_raw = value.get("transitions", {})
    check_raw = value.get("check", {})
    # A `loop.yaml` is editable, so it gets the strict reading: a missing
    # section is refused rather than silently defaulted.
    require_declared_sections(value)
    shared: dict[str, Any] = {
        "step_id": step_id,
        "name": _required_str(value, "name"),
        "kind": kind,
        # Through the shared seam, so a loop stored on disk before inputs were
        # declared reads exactly as a pinned run snapshot of the same age does.
        "inputs": inputs_from_raw(value, context_raw),
        "reports": reports_from_raw(value.get("reports"), context_raw),
        "history": history_or_raise(value.get("history")),
        "report_contract": report_contract_from_raw(value.get("report_contract"), kind),
        "retry": _retry_from_data(retry_raw),
        "transitions": _transitions_from_data(transitions_raw),
    }
    if kind is LoopStepKind.USER_APPROVAL:
        return ApprovalStage(**shared)
    if kind is LoopStepKind.DETERMINISTIC_CHECK:
        return CheckStage(
            **shared,
            check_adapter=(
                _optional_str(check_raw.get("adapter")) if isinstance(check_raw, dict) else ""
            )
            or "command",
            check_command=(
                tuple(_str_list(check_raw.get("command", [])))
                if isinstance(check_raw, dict)
                else ()
            ),
        )
    agent_fields: dict[str, Any] = {
        "instructions": instructions,
        "agent": _agent_from_data(agent_raw, kind) or LoopAgentPolicy(),
        "note_required": (
            value.get("note_required") if isinstance(value.get("note_required"), bool) else None
        ),
    }
    if kind is LoopStepKind.PR:
        return PrStage(
            **shared,
            **agent_fields,
            pr_config=loop_pr_config_from_snapshot(value.get("pr_config")) or LoopPrConfig(),
        )
    if kind is LoopStepKind.AGENT_REVIEW:
        return ReviewStage(
            **shared, **agent_fields, review_gate=_review_gate_from_data(value.get("review_gate"))
        )
    return TaskStage(**shared, **agent_fields)


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
    if kind not in {
        LoopStepKind.AGENT_TASK,
        LoopStepKind.AGENT_REVIEW,
        LoopStepKind.PR,
    }:
        return None
    if not isinstance(value, dict):
        raise ValueError("agent stage requires an agent mapping")
    raw_permissions = value.get("permissions", "read")
    raw_prefixes = value.get("approved_command_prefixes")
    return LoopAgentPolicy(
        session=LoopSessionPolicy(_optional_str(value.get("session")) or "fresh"),
        permissions=(
            None
            if raw_permissions == "inherit"
            else LoopPermission(_optional_str(raw_permissions) or "read")
        ),
        provider=_optional_str(value.get("provider")) or None,
        model=_optional_str(value.get("model")) or None,
        effort=_optional_str(value.get("effort")) or None,
        fast=value.get("fast") if isinstance(value.get("fast"), bool) else None,
        approved_command_prefixes=(
            tuple(_str_list(raw_prefixes)) if raw_prefixes is not None else None
        ),
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


def _review_gate_from_data(value: object) -> LoopReviewGate | None:
    if value is None:
        return None
    if not isinstance(value, dict):
        raise ValueError("review_gate must be a mapping")
    return LoopReviewGate(
        mode=LoopReviewGateMode(_optional_str(value.get("mode")) or "automatic"),
        max_passes=_positive_int(value.get("max_passes"), 3),
        locked=bool(value.get("locked", False)),
    )


def _to_yaml_data(definition: LoopDefinition) -> dict[str, Any]:
    data: dict[str, Any] = {
        "schema_version": _SCHEMA_VERSION,
        "id": definition.definition_id,
        "name": definition.name,
        "description": definition.description,
    }
    if definition.forked_from:
        data["forked_from"] = definition.forked_from
    data["stages"] = [_stage_to_data(stage) for stage in definition.stages]
    return data


def _stage_to_data(stage: LoopStepDefinition) -> dict[str, Any]:
    data: dict[str, Any] = {
        "id": stage.step_id,
        "name": stage.name,
        "kind": stage.kind.value,
    }
    if stage.stage_ref is not None:
        referenced: dict[str, Any] = {
            "id": stage.step_id,
            "use": f"{stage.stage_ref.definition_id}@{stage.stage_ref.revision}",
        }
        if stage.overrides is not None:
            referenced["overrides"] = stage_overrides_snapshot(stage.overrides)
        transitions = {
            outcome.value: destination
            for outcome, destination in stage.transitions.items()
            if destination is not None
        }
        if transitions:
            referenced["transitions"] = transitions
        return referenced
    if isinstance(stage, AgentStage):
        if stage.instructions.strip():
            data["instructions"] = stage.instructions.rstrip() + "\n"
        if stage.note_required is not None:
            data["note_required"] = stage.note_required
    if isinstance(stage, ReviewStage) and stage.review_gate is not None:
        data["review_gate"] = {
            "mode": stage.review_gate.mode.value,
            "max_passes": stage.review_gate.max_passes,
            "locked": stage.review_gate.locked,
        }
    if isinstance(stage, PrStage):
        data["pr_config"] = loop_pr_config_snapshot(stage.pr_config)
    if stage.inputs:
        data["inputs"] = [
            _context_to_data(item)
            for item in stage.inputs
            if item.kind != LoopContextKind.PREVIOUS_REPORT
        ]
    # Always written, empty or not: its presence marks the stage as stating its
    # own inputs, which is what stops the reader granting it the old injected
    # ones back. Dropping it here silently erased the reports of every loop a
    # user had forked or written themselves.
    data["reports"] = [
        {"from": item.from_stage, "required": item.required} for item in stage.reports
    ]
    if stage.history != LoopHistoryLevel.NONE:
        data["history"] = stage.history.value
    if isinstance(stage, AgentStage):
        data["agent"] = {
            "session": stage.agent.session.value,
            "permissions": (
                stage.agent.permissions.value if stage.agent.permissions is not None else "inherit"
            ),
            **({"provider": stage.agent.provider} if stage.agent.provider else {}),
            **({"model": stage.agent.model} if stage.agent.model else {}),
            **({"effort": stage.agent.effort} if stage.agent.effort else {}),
            **({"fast": stage.agent.fast} if stage.agent.fast is not None else {}),
            **(
                {"approved_command_prefixes": list(stage.agent.approved_command_prefixes)}
                if stage.agent.approved_command_prefixes is not None
                else {}
            ),
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
    if isinstance(stage, CheckStage):
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


def _linked_source(root_path: str, source_id: str, fallback_root: str | None) -> StageDefinition:
    repository = FsStageDefinitionRepository()
    source = builtin_stage_definition(source_id) or repository.get_definition(root_path, source_id)
    if source is None and fallback_root is not None:
        source = repository.get_definition(fallback_root, source_id)
    if source is None:
        raise StageDefinitionNotFound(f"stage definition not found: {source_id}")
    return source


def _invalid_definition(
    definition_id: str,
    error: str,
    *,
    scope: LoopDefinitionScope,
) -> LoopDefinition:
    return LoopDefinition(
        definition_id=definition_id,
        name=definition_id.replace("-", " ").title(),
        trigger="artifact_or_objective",
        report_schema=_REPORT_SCHEMA,
        description="Repository loop could not be loaded.",
        scope=scope,
        errors=(error,),
    )


def _loops_root(root_path: str) -> Path:
    root = Path(root_path).expanduser().resolve()
    if not root.is_dir():
        raise ValueError(f"loop working root is not a directory: {root}")
    return root / "loops"


def _definition_dir(root_path: str, definition_id: str) -> Path:
    if (
        not definition_id
        or definition_id in {".", ".."}
        or "/" in definition_id
        or "\\" in definition_id
    ):
        raise ValueError(f"invalid loop definition id: {definition_id!r}")
    return _loops_root(root_path) / definition_id


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
