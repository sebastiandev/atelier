"""Fork a reusable loop definition into repository ownership."""

from dataclasses import dataclass

from src.domain.commands.loops._root import WorkNotFound, resolve_working_root
from src.domain.loop.builtins import builtin_loop_definition
from src.domain.loop.definitions import (
    LoopDefinitionConflict,
    LoopDefinitionInvalid,
    LoopDefinitionNotFound,
    LoopRootUnavailable,
    prepare_definition,
    repository_copy,
)
from src.domain.loop.dtos import LoopDefinition
from src.domain.loop.ports import LoopDefinitionRepository
from src.domain.planning.ports import PlanningSessionRepository
from src.domain.workstore.ports import WorkStore


@dataclass(frozen=True)
class ForkLoopDefinitionRequest:
    """Input for copying a loop into the current repository."""

    work_slug: str
    source_id: str
    definition_id: str
    name: str


def execute(
    workstore: WorkStore,
    planning_sessions: PlanningSessionRepository,
    repository: LoopDefinitionRepository,
    req: ForkLoopDefinitionRequest,
) -> LoopDefinition:
    """Create a repository copy of a built-in or custom definition."""
    root = resolve_working_root(workstore, planning_sessions, req.work_slug)
    source = builtin_loop_definition(req.source_id) or repository.get_definition(
        root, req.source_id
    )
    if source is None:
        raise LoopDefinitionNotFound(f"loop definition not found: {req.source_id}")
    if builtin_loop_definition(req.definition_id) is not None:
        raise LoopDefinitionConflict(
            f"loop definition id is reserved by a built-in: {req.definition_id}"
        )
    prepared = prepare_definition(
        repository_copy(source, definition_id=req.definition_id, name=req.name)
    )
    if not prepared.valid:
        raise LoopDefinitionInvalid(" ".join(prepared.errors))
    return repository.save_definition(root, prepared, expected_revision=None)


__all__ = [
    "ForkLoopDefinitionRequest",
    "LoopDefinitionConflict",
    "LoopDefinitionInvalid",
    "LoopDefinitionNotFound",
    "LoopRootUnavailable",
    "WorkNotFound",
    "execute",
]
