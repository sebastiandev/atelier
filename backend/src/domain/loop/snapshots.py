"""Immutable JSON snapshots for reusable loop definitions."""

from __future__ import annotations

from typing import Any

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

_REPORT_SCHEMA = LoopReportSchema(
    schema_id="multi-stage-loop-report",
    fields=(LoopReportField("summary", "Summary", allow_explicit_none=False),),
)


def definition_snapshot(definition: LoopDefinition) -> dict[str, Any]:
    """Serialize one validated loop definition for a run.

    Preconditions: ``definition`` is the exact revision selected for launch.
    Postconditions: returns a JSON-safe value containing every executable stage.
    """
    return {
        "id": definition.definition_id,
        "name": definition.name,
        "description": definition.description,
        "scope": definition.scope.value,
        "revision": definition.revision,
        "stages": [_stage_snapshot(stage) for stage in definition.stages],
    }


def definition_from_snapshot(value: object) -> LoopDefinition:
    """Restore a loop definition from a persisted run snapshot.

    Preconditions: ``value`` came from :func:`definition_snapshot`.
    Postconditions: returns a detached domain value; no repository is consulted.
    """
    if not isinstance(value, dict):
        raise ValueError("loop definition snapshot must be a mapping")
    stages = value.get("stages")
    if not isinstance(stages, list):
        raise ValueError("loop definition snapshot needs stages")
    return LoopDefinition(
        definition_id=_string(value, "id"),
        name=_string(value, "name"),
        description=_optional_string(value.get("description")),
        trigger="artifact_or_objective",
        report_schema=_REPORT_SCHEMA,
        scope=LoopDefinitionScope(_string(value, "scope")),
        revision=_string(value, "revision"),
        stages=tuple(_stage_from_snapshot(stage) for stage in stages),
    )


def _stage_snapshot(stage: LoopStepDefinition) -> dict[str, Any]:
    return {
        "id": stage.step_id,
        "name": stage.name,
        "kind": stage.kind.value,
        "instructions": stage.instructions,
        "context": [
            {
                "kind": item.kind.value,
                "required": item.required,
                "paths": list(item.paths),
                "step": item.step,
                "ref": item.ref,
            }
            for item in stage.context
        ],
        "agent": (
            {
                "session": stage.agent.session.value,
                "permissions": (
                    stage.agent.permissions.value
                    if stage.agent.permissions is not None
                    else None
                ),
                "provider": stage.agent.provider,
                "model": stage.agent.model,
                "effort": stage.agent.effort,
            }
            if stage.agent is not None
            else None
        ),
        "report_contract": stage.report_contract,
        "retry": {
            "max_attempts": stage.retry.max_attempts,
            "timeout_minutes": stage.retry.timeout_minutes,
        },
        "transitions": {
            outcome.value: destination
            for outcome, destination in stage.transitions.items()
        },
        "check_adapter": stage.check_adapter,
        "check_command": list(stage.check_command),
    }


def _stage_from_snapshot(value: object) -> LoopStepDefinition:
    if not isinstance(value, dict):
        raise ValueError("loop stage snapshot must be a mapping")
    context = value.get("context", [])
    transitions = value.get("transitions", {})
    retry = value.get("retry", {})
    if not isinstance(context, list) or not isinstance(transitions, dict):
        raise ValueError("loop stage snapshot has invalid context or transitions")
    if not isinstance(retry, dict):
        raise ValueError("loop stage snapshot has invalid retry policy")
    return LoopStepDefinition(
        step_id=_string(value, "id"),
        name=_string(value, "name"),
        kind=LoopStepKind(_string(value, "kind")),
        instructions=_optional_string(value.get("instructions")),
        context=tuple(_context_from_snapshot(item) for item in context),
        agent=_agent_from_snapshot(value.get("agent")),
        report_contract=_optional_string(value.get("report_contract")) or "generic",
        retry=LoopRetryPolicy(
            max_attempts=_integer(retry.get("max_attempts"), 2),
            timeout_minutes=_integer(retry.get("timeout_minutes"), 20),
        ),
        transitions={
            LoopOutcome(str(outcome)): (
                destination if isinstance(destination, str) else None
            )
            for outcome, destination in transitions.items()
        },
        check_adapter=_optional_string(value.get("check_adapter")) or None,
        check_command=tuple(
            item
            for item in value.get("check_command", [])
            if isinstance(item, str)
        )
        if isinstance(value.get("check_command", []), list)
        else (),
    )


def _context_from_snapshot(value: object) -> LoopContextReference:
    if not isinstance(value, dict):
        raise ValueError("loop context snapshot must be a mapping")
    paths = value.get("paths", [])
    return LoopContextReference(
        kind=LoopContextKind(_string(value, "kind")),
        required=bool(value.get("required", False)),
        paths=tuple(item for item in paths if isinstance(item, str))
        if isinstance(paths, list)
        else (),
        step=_optional_string(value.get("step")) or None,
        ref=_optional_string(value.get("ref")) or None,
    )


def _agent_from_snapshot(value: object) -> LoopAgentPolicy | None:
    if value is None:
        return None
    if not isinstance(value, dict):
        raise ValueError("loop agent snapshot must be a mapping")
    raw_permissions = value.get("permissions")
    return LoopAgentPolicy(
        session=LoopSessionPolicy(_string(value, "session")),
        permissions=(
            LoopPermission(raw_permissions)
            if isinstance(raw_permissions, str)
            else None
        ),
        provider=_optional_string(value.get("provider")) or None,
        model=_optional_string(value.get("model")) or None,
        effort=_optional_string(value.get("effort")) or None,
    )


def _string(value: dict[str, Any], key: str) -> str:
    item = value.get(key)
    if not isinstance(item, str) or not item:
        raise ValueError(f"loop snapshot {key} must be a string")
    return item


def _optional_string(value: object) -> str:
    return value if isinstance(value, str) else ""


def _integer(value: object, default: int) -> int:
    return value if isinstance(value, int) and not isinstance(value, bool) else default


__all__ = ["definition_from_snapshot", "definition_snapshot"]
