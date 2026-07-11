"""Validation and revision actions for reusable loop definitions."""

from __future__ import annotations

import hashlib
import json
import re
from dataclasses import asdict, replace
from pathlib import PurePosixPath

from src.domain.loop.dtos import (
    LoopContextKind,
    LoopDefinition,
    LoopDefinitionScope,
    LoopOutcome,
    LoopStepDefinition,
    LoopStepKind,
)

_ID = re.compile(r"^[a-z0-9]+(?:-[a-z0-9]+)*$")
_TERMINALS = {"pause", "fail", "complete"}


class LoopDefinitionNotFound(ValueError):
    """The requested reusable loop does not exist."""


class LoopDefinitionConflict(ValueError):
    """The reusable loop changed since the caller loaded it."""


class LoopDefinitionInvalid(ValueError):
    """The reusable loop does not satisfy its structural contract."""


class LoopDefinitionReadOnly(ValueError):
    """A built-in loop cannot be changed or deleted in place."""


class LoopRootUnavailable(ValueError):
    """The Work has no persisted working root for repository loops."""


def prepare_definition(definition: LoopDefinition) -> LoopDefinition:
    """Validate and revision-hash one loop definition.

    Preconditions: ``definition`` is a parsed domain value.
    Postconditions: returns a new value containing deterministic errors and revision.
    """
    errors = validate_definition(definition)
    prepared = replace(definition, errors=errors, revision="")
    return replace(prepared, revision=definition_revision(prepared))


def validate_definition(definition: LoopDefinition) -> tuple[str, ...]:
    """Return structural errors for one reusable loop definition.

    Preconditions: paths are unresolved repository-relative references.
    Postconditions: no input state or filesystem content is changed.
    """
    errors: list[str] = []
    if not _ID.fullmatch(definition.definition_id):
        errors.append("Loop id must use lowercase letters, numbers, and hyphens.")
    if not definition.name.strip():
        errors.append("Loop name is required.")
    if not definition.stages:
        errors.append("At least one stage is required.")
        return tuple(errors)
    if definition.stages[0].kind != LoopStepKind.AGENT_TASK:
        errors.append("The first stage must be an implementation agent stage.")

    ids = [stage.step_id for stage in definition.stages]
    known = set(ids)
    if len(known) != len(ids):
        errors.append("Stage ids must be unique.")
    for stage in definition.stages:
        errors.extend(_validate_stage(stage, known))

    reachable = _reachable_stage_ids(definition.stages)
    for stage_id in ids:
        if stage_id not in reachable:
            errors.append(f"Stage {stage_id!r} is unreachable from the first stage.")
    if not any(stage.kind == LoopStepKind.USER_APPROVAL for stage in definition.stages):
        errors.append("A loop must include a human approval stage.")
    if _pass_transition_has_cycle(definition.stages):
        errors.append("Pass transitions cannot contain a cycle.")
    return tuple(dict.fromkeys(errors))


def definition_revision(definition: LoopDefinition) -> str:
    """Return a stable short content hash for a loop definition.

    Preconditions: ``definition`` contains all referenced instruction bodies.
    Postconditions: the hash excludes transient validation and revision fields.
    """
    data = asdict(definition)
    data.pop("revision", None)
    data.pop("errors", None)
    payload = json.dumps(data, sort_keys=True, separators=(",", ":"), default=str)
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()[:6]


def repository_copy(
    source: LoopDefinition, *, definition_id: str, name: str
) -> LoopDefinition:
    """Fork a built-in or repository definition into repository scope.

    Preconditions: ``definition_id`` is intended as a new repository id.
    Postconditions: returns an unsaved copy with no revision or errors.
    """
    return replace(
        source,
        definition_id=definition_id,
        name=name.strip(),
        scope=LoopDefinitionScope.REPOSITORY,
        revision="",
        is_default=False,
        forked_from=source.definition_id,
        errors=(),
    )


def _validate_stage(stage: LoopStepDefinition, known: set[str]) -> list[str]:
    errors: list[str] = []
    label = f"Stage {stage.step_id!r}"
    if not _ID.fullmatch(stage.step_id):
        errors.append(f"{label} has an invalid id.")
    if not stage.name.strip():
        errors.append(f"{label} needs a name.")
    if stage.kind in {LoopStepKind.AGENT_TASK, LoopStepKind.AGENT_REVIEW}:
        if not stage.instructions.strip():
            errors.append(f"{label} needs Markdown instructions.")
        if stage.agent is None:
            errors.append(f"{label} needs an agent policy.")
    if stage.kind == LoopStepKind.DETERMINISTIC_CHECK:
        if stage.check_adapter != "command":
            errors.append(f"{label} needs the supported 'command' check adapter.")
        if not stage.check_command:
            errors.append(f"{label} needs a check command.")
    if stage.retry.max_attempts < 1:
        errors.append(f"{label} retry limit must be at least one.")
    if stage.retry.timeout_minutes < 1:
        errors.append(f"{label} timeout must be at least one minute.")

    for context in stage.context:
        if context.kind == LoopContextKind.PREVIOUS_REPORT:
            if not context.step or context.step not in known:
                errors.append(f"{label} references an unknown previous-report stage.")
        for path in context.paths:
            if not _safe_relative(path):
                errors.append(f"{label} has an unsafe context path: {path!r}.")

    for outcome, destination in stage.transitions.items():
        if destination is None:
            continue
        if destination not in known and destination not in _TERMINALS:
            errors.append(
                f"{label} {outcome.value!r} transition targets unknown stage "
                f"{destination!r}."
            )
        if destination == stage.step_id and outcome != LoopOutcome.CHANGES_REQUESTED:
            errors.append(f"{label} can only loop to itself on changes requested.")
    if stage.kind != LoopStepKind.USER_APPROVAL and not stage.transitions.get(
        LoopOutcome.PASS
    ):
        errors.append(f"{label} needs a pass destination.")
    return errors


def _safe_relative(path: str) -> bool:
    pure = PurePosixPath(path)
    return bool(path) and not pure.is_absolute() and ".." not in pure.parts


def _reachable_stage_ids(stages: tuple[LoopStepDefinition, ...]) -> set[str]:
    known = {stage.step_id: stage for stage in stages}
    pending = [stages[0].step_id]
    seen: set[str] = set()
    while pending:
        current = pending.pop()
        if current in seen:
            continue
        seen.add(current)
        stage = known[current]
        pending.extend(
            destination
            for destination in stage.transitions.values()
            if destination in known and destination not in seen
        )
    return seen


def _pass_transition_has_cycle(stages: tuple[LoopStepDefinition, ...]) -> bool:
    known = {stage.step_id: stage for stage in stages}
    visiting: set[str] = set()
    visited: set[str] = set()

    def visit(step_id: str) -> bool:
        if step_id in visiting:
            return True
        if step_id in visited:
            return False
        visiting.add(step_id)
        destination = known[step_id].transitions.get(LoopOutcome.PASS)
        if destination in known and visit(destination):
            return True
        visiting.remove(step_id)
        visited.add(step_id)
        return False

    return any(visit(step_id) for step_id in known)


__all__ = [
    "LoopDefinitionConflict",
    "LoopDefinitionInvalid",
    "LoopDefinitionNotFound",
    "LoopDefinitionReadOnly",
    "LoopRootUnavailable",
    "definition_revision",
    "prepare_definition",
    "repository_copy",
    "validate_definition",
]
