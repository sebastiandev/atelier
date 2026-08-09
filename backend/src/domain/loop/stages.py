"""Validation, revisioning, and link resolution for reusable stages."""

from __future__ import annotations

import hashlib
import json
import re
from dataclasses import asdict, replace

from src.domain.loop.definitions import stage_shape_errors
from src.domain.loop.dtos import (
    LoopStepDefinition,
    StageDefinition,
    StageDefinitionRef,
    StageDefinitionScope,
    StageOverrides,
)

_ID = re.compile(r"^[a-z0-9]+(?:-[a-z0-9]+)*$")


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
    errors.extend(stage_shape_errors(stage, "Stage"))
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

    Only *routed* outcomes (a non-null destination) must be declared by the
    source stage. A transition set to ``-`` (no destination) is not wired, so it
    never requires the stage to declare that outcome — matching how
    ``validate_definition`` skips null destinations for inline stages.
    """
    wired = {
        outcome
        for outcome, destination in instance.transitions.items()
        if destination is not None
    }
    undeclared = wired - set(definition.outcomes)
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
            "inputs",
            "reports",
            "history",
            "agent",
            "retry",
            "check_adapter",
            "check_command",
            "note_required",
            "review_gate",
            "pr_config",
        )
        if (value := getattr(overrides, field)) is not None
        # An override may name a field the linked stage's kind does not own --
        # a `pr_config` over a check, say. That used to be a validation
        # message; applying it now would be a TypeError out of `replace`, so it
        # is dropped, which is what the old rule effectively did.
        and hasattr(base, field)
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
