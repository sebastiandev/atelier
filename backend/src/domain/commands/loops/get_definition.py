"""Get one built-in or repository loop definition."""

from dataclasses import dataclass

from src.domain.commands.loops._root import WorkNotFound, resolve_working_root
from src.domain.loop.builtins import builtin_loop_definition
from src.domain.loop.definitions import LoopDefinitionNotFound, LoopRootUnavailable
from src.domain.loop.dtos import LoopDefinition
from src.domain.loop.ports import LoopDefinitionRepository
from src.domain.planning.ports import PlanningSessionRepository
from src.domain.workstore.ports import WorkStore


@dataclass(frozen=True)
class GetLoopDefinitionRequest:
    """Input for reading one Work-scoped loop definition."""

    work_slug: str
    definition_id: str


def execute(
    workstore: WorkStore,
    planning_sessions: PlanningSessionRepository,
    repository: LoopDefinitionRepository,
    req: GetLoopDefinitionRequest,
) -> LoopDefinition:
    """Return one built-in or repository loop definition."""
    root = resolve_working_root(workstore, planning_sessions, req.work_slug)
    definition = builtin_loop_definition(req.definition_id)
    if definition is None:
        definition = repository.get_definition(root, req.definition_id)
    if definition is None:
        raise LoopDefinitionNotFound(
            f"loop definition not found: {req.definition_id}"
        )
    return definition


__all__ = [
    "GetLoopDefinitionRequest",
    "LoopDefinitionNotFound",
    "LoopRootUnavailable",
    "WorkNotFound",
    "execute",
]
