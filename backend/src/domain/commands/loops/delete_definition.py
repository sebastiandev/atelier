"""Delete one repository-owned loop definition."""

from dataclasses import dataclass

from src.domain.commands.loops._root import WorkNotFound, resolve_working_root
from src.domain.loop.builtins import builtin_loop_definition
from src.domain.loop.definitions import (
    LoopDefinitionNotFound,
    LoopDefinitionReadOnly,
    LoopRootUnavailable,
)
from src.domain.loop.ports import LoopDefinitionRepository
from src.domain.planning.ports import PlanningSessionRepository
from src.domain.workstore.ports import WorkStore


@dataclass(frozen=True)
class DeleteLoopDefinitionRequest:
    """Input for deleting one repository loop."""

    work_slug: str
    definition_id: str


def execute(
    workstore: WorkStore,
    planning_sessions: PlanningSessionRepository,
    repository: LoopDefinitionRepository,
    req: DeleteLoopDefinitionRequest,
) -> None:
    """Delete a custom loop while preserving immutable built-ins."""
    root = resolve_working_root(workstore, planning_sessions, req.work_slug)
    if builtin_loop_definition(req.definition_id) is not None:
        raise LoopDefinitionReadOnly("built-in loops cannot be deleted")
    repository.delete_definition(root, req.definition_id)


__all__ = [
    "DeleteLoopDefinitionRequest",
    "LoopDefinitionNotFound",
    "LoopDefinitionReadOnly",
    "LoopRootUnavailable",
    "WorkNotFound",
    "execute",
]
