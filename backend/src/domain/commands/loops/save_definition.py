"""Create or update one repository-owned loop definition."""

from dataclasses import dataclass, replace

from src.domain.commands.loops._root import WorkNotFound, resolve_working_root
from src.domain.loop.builtins import builtin_loop_definition
from src.domain.loop.definitions import (
    LoopDefinitionConflict,
    LoopDefinitionInvalid,
    LoopDefinitionReadOnly,
    LoopRootUnavailable,
    prepare_definition,
)
from src.domain.loop.dtos import LoopDefinition, LoopDefinitionScope
from src.domain.loop.ports import LoopDefinitionRepository
from src.domain.planning.ports import PlanningSessionRepository
from src.domain.workstore.ports import WorkStore


@dataclass(frozen=True)
class SaveLoopDefinitionRequest:
    """Input for saving one complete loop definition working copy."""

    work_slug: str
    definition: LoopDefinition
    expected_revision: str | None = None


def execute(
    workstore: WorkStore,
    planning_sessions: PlanningSessionRepository,
    repository: LoopDefinitionRepository,
    req: SaveLoopDefinitionRequest,
) -> LoopDefinition:
    """Validate and persist one repository loop definition."""
    root = resolve_working_root(workstore, planning_sessions, req.work_slug)
    if req.definition.scope == LoopDefinitionScope.BUILTIN:
        raise LoopDefinitionReadOnly("built-in loops must be forked before editing")
    if builtin_loop_definition(req.definition.definition_id) is not None:
        raise LoopDefinitionReadOnly("repository loops cannot replace a built-in id")
    prepared = prepare_definition(
        replace(req.definition, scope=LoopDefinitionScope.REPOSITORY)
    )
    if not prepared.valid:
        raise LoopDefinitionInvalid(" ".join(prepared.errors))
    return repository.save_definition(
        root, prepared, expected_revision=req.expected_revision
    )


__all__ = [
    "LoopDefinitionConflict",
    "LoopDefinitionInvalid",
    "LoopDefinitionReadOnly",
    "LoopRootUnavailable",
    "SaveLoopDefinitionRequest",
    "WorkNotFound",
    "execute",
]
