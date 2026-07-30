"""Validation, revisioning, and link resolution for reusable stages."""

from __future__ import annotations

import hashlib
import json
import re
from dataclasses import asdict, replace

from src.domain.loop.definitions import validate_agent_policy, validate_pr_config
from src.domain.loop.dtos import (
    LoopStepDefinition,
    LoopStepKind,
    StageDefinition,
    StageDefinitionRef,
    StageDefinitionScope,
    StageOverrides,
)

_ID = re.compile(r"^[a-z0-9]+(?:-[a-z0-9]+)*$")
_AGENT_KINDS = {
    LoopStepKind.AGENT_TASK,
    LoopStepKind.AGENT_REVIEW,
    LoopStepKind.PR,
}


class StageDefinitionNotFound(ValueError):
    """The requested reusable stage does not exist."""


class StageDefinitionConflict(ValueError):
    """The reusable stage changed since the caller loaded it."""


class StageDefinitionInvalid(ValueError):
    """The reusable stage does not satisfy its structural contract."""


class StageDefinitionReadOnly(ValueError):
    """A built-in stage cannot be changed or deleted in place."""


def prepare_stage_definition(definition: StageDefinition) -> StageDefinition:
    """Validate and revision-hash one standalone stage.

    Preconditions: ``definition`` is parsed and contains no loop wiring.
    Postconditions: returns an immutable value with deterministic errors/revision.
    """
    prepared = replace(
        definition,
        errors=validate_stage_definition(definition),
        revision="",
        used_by=definition.used_by,
    )
    return replace(prepared, revision=stage_definition_revision(prepared))


def validate_stage_definition(definition: StageDefinition) -> tuple[str, ...]:
    """Return standalone-stage errors without mutating state.

    Preconditions: destinations are absent because loops own wiring.
    Postconditions: every returned error is safe to show to a user.
    """
    errors: list[str] = []
    stage = definition.stage
    if not _ID.fullmatch(definition.definition_id):
        errors.append("Stage id must use lowercase letters, numbers, and hyphens.")
    if not definition.name.strip() or not stage.name.strip():
        errors.append("Stage name is required.")
    if stage.step_id != definition.definition_id:
        errors.append("Standalone stage id must match its definition id.")
    if stage.transitions:
        errors.append("Standalone stages declare outcomes; loops wire destinations.")
    if stage.stage_ref is not None or stage.overrides is not None:
        errors.append("A standalone stage cannot link to another stage.")
    if not definition.outcomes:
        errors.append("At least one outcome is required.")
    if len(set(definition.outcomes)) != len(definition.outcomes):
        errors.append("Stage outcomes must be unique.")
    if stage.kind in _AGENT_KINDS:
        if not stage.instructions.strip():
            errors.append("Agent stages need Markdown instructions.")
        if stage.agent is None:
            errors.append("Agent stages need an agent policy.")
        else:
            errors.extend(validate_agent_policy("Stage", stage.agent))
    if stage.kind == LoopStepKind.DETERMINISTIC_CHECK:
        if stage.check_adapter != "command" or not stage.check_command:
            errors.append("Deterministic checks need a command adapter and command.")
    if stage.kind == LoopStepKind.PR:
        config = stage.pr_config
        if config is None:
            errors.append("Create PR stages need PR configuration.")
        else:
            errors.extend(validate_pr_config("Stage", config))
    elif stage.pr_config is not None:
        errors.append("Only Create PR stages can declare PR configuration.")
    if stage.retry.max_attempts < 1 or stage.retry.timeout_minutes < 1:
        errors.append("Retry attempts and timeout must be positive.")
    return tuple(dict.fromkeys(errors))


def stage_definition_revision(definition: StageDefinition) -> str:
    """Return a stable content revision excluding transient catalog fields."""
    data = asdict(definition)
    data.pop("revision", None)
    data.pop("errors", None)
    data.pop("used_by", None)
    return hashlib.sha256(
        json.dumps(data, sort_keys=True, separators=(",", ":"), default=str).encode()
    ).hexdigest()[:6]


def repository_stage_copy(
    source: StageDefinition,
    *,
    definition_id: str,
    name: str,
) -> StageDefinition:
    """Fork any reusable stage into writable repository scope."""
    return replace(
        source,
        definition_id=definition_id,
        name=name.strip(),
        stage=replace(source.stage, step_id=definition_id, name=name.strip()),
        scope=StageDefinitionScope.LIBRARY,
        revision="",
        forked_from=source.definition_id,
        used_by=(),
        errors=(),
    )


def resolve_stage_link(
    instance: LoopStepDefinition,
    definition: StageDefinition,
) -> LoopStepDefinition:
    """Resolve one linked loop stage into the existing executable shape.

    Preconditions: ``instance`` carries loop identity/wiring and ``definition`` is
    valid. Postconditions: runtime fields come from the current library revision,
    sparse overrides win, and loop transitions remain untouched.
    """
    undeclared = set(instance.transitions) - set(definition.outcomes)
    if undeclared:
        labels = ", ".join(sorted(item.value for item in undeclared))
        raise StageDefinitionInvalid(
            f"Stage {definition.definition_id!r} does not declare outcomes: {labels}."
        )
    overrides = instance.overrides or StageOverrides()
    base = definition.stage
    values = {
        field: value
        for field in (
            "name",
            "instructions",
            "context",
            "agent",
            "report_contract",
            "retry",
            "check_adapter",
            "check_command",
            "note_required",
            "review_gate",
            "pr_config",
        )
        if (value := getattr(overrides, field)) is not None
    }
    return replace(
        base,
        **values,
        step_id=instance.step_id,
        transitions=dict(instance.transitions),
        stage_ref=StageDefinitionRef(definition.definition_id, definition.revision),
        overrides=instance.overrides,
    )


__all__ = [
    "StageDefinitionConflict",
    "StageDefinitionInvalid",
    "StageDefinitionNotFound",
    "StageDefinitionReadOnly",
    "prepare_stage_definition",
    "repository_stage_copy",
    "resolve_stage_link",
    "stage_definition_revision",
    "validate_stage_definition",
]
