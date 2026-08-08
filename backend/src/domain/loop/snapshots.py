"""Immutable JSON snapshots for reusable loop definitions."""

from __future__ import annotations

from typing import Any

from src.domain.loop.dtos import (
    PREVIOUS_STAGE,
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
    LoopReportReference,
    LoopReportSchema,
    LoopRetryPolicy,
    LoopReviewGate,
    LoopReviewGateMode,
    LoopSessionPolicy,
    LoopStepDefinition,
    LoopStepKind,
    StageDefinitionRef,
    StageOverrides,
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
    value: dict[str, Any] = {
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
            # `previous_report` is not an input any more: it is read back as a
            # `reports` entry below, and writing it in both places would put
            # the same declaration in two shapes again.
            if item.kind != LoopContextKind.PREVIOUS_REPORT
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
                **(
                    {"fast": stage.agent.fast}
                    if stage.agent.fast is not None
                    else {}
                ),
                **(
                    {
                        "approved_command_prefixes": list(
                            stage.agent.approved_command_prefixes
                        )
                    }
                    if stage.agent.approved_command_prefixes is not None
                    else {}
                ),
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
    # Emitted only when declared, so a definition that never asked for either
    # serialises exactly as it did before they existed.
    if stage.reports:
        value["reports"] = [
            {"from": item.from_stage, "required": item.required} for item in stage.reports
        ]
    if stage.history != LoopHistoryLevel.NONE:
        value["history"] = stage.history.value
    if stage.note_required is not None:
        value["note_required"] = stage.note_required
    if stage.review_gate is not None:
        value["review_gate"] = {
            "mode": stage.review_gate.mode.value,
            "max_passes": stage.review_gate.max_passes,
            "locked": stage.review_gate.locked,
        }
    if stage.pr_config is not None:
        value["pr_config"] = loop_pr_config_snapshot(stage.pr_config)
    if stage.stage_ref is not None:
        value["stage_ref"] = {
            "definition_id": stage.stage_ref.definition_id,
            "revision": stage.stage_ref.revision,
        }
    if stage.overrides is not None:
        value["overrides"] = _overrides_snapshot(stage.overrides)
    return value


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
        context=_inputs_from_snapshot(value, context),
        reports=_reports_from_snapshot(value.get("reports"), context),
        history=_history_from_snapshot(value.get("history")),
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
        note_required=(
            value.get("note_required")
            if isinstance(value.get("note_required"), bool)
            else None
        ),
        review_gate=_review_gate_from_snapshot(value.get("review_gate")),
        pr_config=loop_pr_config_from_snapshot(value.get("pr_config")),
        stage_ref=_stage_ref_from_snapshot(value.get("stage_ref")),
        overrides=_overrides_from_snapshot(value.get("overrides")),
    )


def loop_stage_snapshot(stage: LoopStepDefinition) -> dict[str, Any]:
    """Serialize one executable stage for filesystem and wire adapters."""
    return _stage_snapshot(stage)


def loop_stage_from_snapshot(value: object) -> LoopStepDefinition:
    """Restore one executable stage from an additive JSON mapping."""
    return _stage_from_snapshot(value)


def stage_overrides_snapshot(overrides: StageOverrides) -> dict[str, Any]:
    """Serialize sparse loop-local stage overrides."""
    return _overrides_snapshot(overrides)


def stage_overrides_from_snapshot(value: object) -> StageOverrides | None:
    """Restore sparse loop-local stage overrides."""
    return _overrides_from_snapshot(value)


def loop_pr_config_snapshot(config: LoopPrConfig) -> dict[str, Any]:
    """Serialize Create-PR configuration without dropping runtime branch state."""
    return {
        "name_template": config.name_template,
        "description_mode": config.description_mode,
        "description_instructions": config.description_instructions,
        "manual_body": config.manual_body,
        "status": config.status,
        "base_branch": config.base_branch,
        "branch_name": config.branch_name,
    }


def loop_pr_config_from_snapshot(value: object) -> LoopPrConfig | None:
    """Parse Create-PR configuration, accepting the legacy runtime ``name`` key."""
    if value is None:
        return None
    if not isinstance(value, dict):
        raise ValueError("loop PR config snapshot must be a mapping")
    return LoopPrConfig(
        name_template=(
            _optional_string(value.get("name_template"))
            or _optional_string(value.get("name"))
            or "{goal} - {work-id}"
        ),
        description_mode=_optional_string(value.get("description_mode")) or "automatic",
        description_instructions=_optional_string(value.get("description_instructions")),
        manual_body=_optional_string(value.get("manual_body")),
        status=_optional_string(value.get("status")) or "draft",
        base_branch=_optional_string(value.get("base_branch")) or "master",
        branch_name=_optional_string(value.get("branch_name")) or None,
    )


def _inputs_from_snapshot(
    stage: dict[str, Any],
    context: list[Any],
) -> tuple[LoopContextReference, ...]:
    """Read a stage's declared inputs, converting the old shape when needed.

    Two conversions. ``previous_report`` is not an input any more, so it drops
    out here and comes back through :func:`_reports_from_snapshot`. And
    feedback used to be injected into every stage except a PR stage rather than
    declared, so a definition written before this existed is granted it on the
    same terms -- otherwise every loop a user already has would silently stop
    telling its stages what was asked of them.

    Preconditions: ``stage`` is the raw stage snapshot; ``context`` is its raw
    context list. Postconditions: the declared inputs, with at most one
    ``feedback`` entry.
    """
    inputs = tuple(
        item
        for item in (_context_from_snapshot(row) for row in context)
        if item.kind != LoopContextKind.PREVIOUS_REPORT
    )
    declares_feedback = any(item.kind == LoopContextKind.FEEDBACK for item in inputs)
    written_before_inputs_existed = "reports" not in stage and not declares_feedback
    if written_before_inputs_existed and _optional_string(stage.get("kind")) != LoopStepKind.PR:
        return (*inputs, LoopContextReference(LoopContextKind.FEEDBACK))
    return inputs


def _reports_from_snapshot(value: object, context: list[Any]) -> tuple[LoopReportReference, ...]:
    """Read a stage's declared reports, converting the old shape when absent.

    This is the seam. A definition written before reports existed declared at
    most one report as a ``previous_report`` context entry, whose optional
    ``step`` named the stage to read; without a step it meant whichever stage
    ran last. Both map onto one reference, so nothing downstream ever sees the
    old shape.

    Preconditions: ``context`` is the snapshot's raw context list.
    Postconditions: an explicit ``reports`` list wins; otherwise the converted
    entries, in declaration order. A stage that declared no report gets none --
    which is what it effectively had.
    """
    if isinstance(value, list):
        return tuple(
            LoopReportReference(
                from_stage=_optional_string(item.get("from")) or PREVIOUS_STAGE,
                required=bool(item.get("required", True)),
            )
            for item in value
            if isinstance(item, dict)
        )
    return tuple(
        LoopReportReference(
            from_stage=_optional_string(item.get("step")) or PREVIOUS_STAGE,
            required=bool(item.get("required", False)),
        )
        for item in context
        if isinstance(item, dict)
        and _optional_string(item.get("kind")) == LoopContextKind.PREVIOUS_REPORT.value
    )


def _history_from_snapshot(value: object) -> LoopHistoryLevel:
    """Read a stage's history level, defaulting to the old behaviour of none."""
    try:
        return LoopHistoryLevel(_optional_string(value))
    except ValueError:
        return LoopHistoryLevel.NONE


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
    raw_prefixes = value.get("approved_command_prefixes")
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
        fast=value.get("fast") if isinstance(value.get("fast"), bool) else None,
        approved_command_prefixes=(
            tuple(item for item in raw_prefixes if isinstance(item, str))
            if isinstance(raw_prefixes, list)
            else None
        ),
    )


def _review_gate_from_snapshot(value: object) -> LoopReviewGate | None:
    if value is None:
        return None
    if not isinstance(value, dict):
        raise ValueError("loop review gate snapshot must be a mapping")
    return LoopReviewGate(
        mode=LoopReviewGateMode(_optional_string(value.get("mode")) or "automatic"),
        max_passes=max(1, _integer(value.get("max_passes"), 3)),
        locked=bool(value.get("locked", False)),
    )


def _stage_ref_from_snapshot(value: object) -> StageDefinitionRef | None:
    if value is None:
        return None
    if not isinstance(value, dict):
        raise ValueError("stage reference must be a mapping")
    return StageDefinitionRef(
        definition_id=_string(value, "definition_id"),
        revision=_string(value, "revision"),
    )


def _overrides_snapshot(overrides: StageOverrides) -> dict[str, Any]:
    value: dict[str, Any] = {}
    for key in ("name", "instructions", "report_contract", "check_adapter", "note_required"):
        item = getattr(overrides, key)
        if item is not None:
            value[key] = item
    if overrides.context is not None:
        value["context"] = [
            {
                "kind": item.kind.value,
                "required": item.required,
                "paths": list(item.paths),
                "step": item.step,
                "ref": item.ref,
            }
            for item in overrides.context
        ]
    if overrides.agent is not None:
        value["agent"] = _stage_snapshot(
            LoopStepDefinition(
                step_id="placeholder",
                name="Placeholder",
                kind=LoopStepKind.AGENT_TASK,
                agent=overrides.agent,
            )
        )["agent"]
    if overrides.retry is not None:
        value["retry"] = {
            "max_attempts": overrides.retry.max_attempts,
            "timeout_minutes": overrides.retry.timeout_minutes,
        }
    if overrides.check_command is not None:
        value["check_command"] = list(overrides.check_command)
    if overrides.review_gate is not None:
        value["review_gate"] = {
            "mode": overrides.review_gate.mode.value,
            "max_passes": overrides.review_gate.max_passes,
            "locked": overrides.review_gate.locked,
        }
    if overrides.pr_config is not None:
        value["pr_config"] = loop_pr_config_snapshot(overrides.pr_config)
    return value


def _overrides_from_snapshot(value: object) -> StageOverrides | None:
    if value is None:
        return None
    if not isinstance(value, dict):
        raise ValueError("stage overrides must be a mapping")
    raw_context = value.get("context")
    raw_retry = value.get("retry")
    raw_command = value.get("check_command")
    return StageOverrides(
        name=value.get("name") if isinstance(value.get("name"), str) else None,
        instructions=(
            value.get("instructions") if isinstance(value.get("instructions"), str) else None
        ),
        context=(
            tuple(_context_from_snapshot(item) for item in raw_context)
            if isinstance(raw_context, list)
            else None
        ),
        agent=_agent_from_snapshot(value.get("agent")),
        report_contract=(
            value.get("report_contract")
            if isinstance(value.get("report_contract"), str)
            else None
        ),
        retry=(
            LoopRetryPolicy(
                max_attempts=_integer(raw_retry.get("max_attempts"), 2),
                timeout_minutes=_integer(raw_retry.get("timeout_minutes"), 20),
            )
            if isinstance(raw_retry, dict)
            else None
        ),
        check_adapter=(
            value.get("check_adapter")
            if isinstance(value.get("check_adapter"), str)
            else None
        ),
        check_command=(
            tuple(item for item in raw_command if isinstance(item, str))
            if isinstance(raw_command, list)
            else None
        ),
        note_required=(
            value.get("note_required")
            if isinstance(value.get("note_required"), bool)
            else None
        ),
        review_gate=_review_gate_from_snapshot(value.get("review_gate")),
        pr_config=loop_pr_config_from_snapshot(value.get("pr_config")),
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


__all__ = [
    "definition_from_snapshot",
    "definition_snapshot",
    "loop_pr_config_from_snapshot",
    "loop_pr_config_snapshot",
    "loop_stage_from_snapshot",
    "loop_stage_snapshot",
    "stage_overrides_from_snapshot",
    "stage_overrides_snapshot",
]
