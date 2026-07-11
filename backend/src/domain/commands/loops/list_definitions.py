"""List built-in and repository loop definitions for one Work."""

from dataclasses import dataclass

from src.domain.commands.loops._root import WorkNotFound, resolve_working_root
from src.domain.loop.builtins import builtin_loop_definitions
from src.domain.loop.definitions import LoopRootUnavailable
from src.domain.loop.dtos import LoopDefinition
from src.domain.loop.ports import LoopDefinitionRepository
from src.domain.planning.ports import PlanningSessionRepository
from src.domain.workstore.ports import WorkStore


@dataclass(frozen=True)
class ListLoopDefinitionsRequest:
    """Input for listing loops available to one Work."""

    work_slug: str


def execute(
    workstore: WorkStore,
    planning_sessions: PlanningSessionRepository,
    repository: LoopDefinitionRepository,
    req: ListLoopDefinitionsRequest,
) -> tuple[LoopDefinition, ...]:
    """List built-in definitions followed by repository definitions."""
    root = resolve_working_root(workstore, planning_sessions, req.work_slug)
    return (*builtin_loop_definitions(), *repository.list_definitions(root))


__all__ = [
    "ListLoopDefinitionsRequest",
    "LoopRootUnavailable",
    "WorkNotFound",
    "execute",
]
