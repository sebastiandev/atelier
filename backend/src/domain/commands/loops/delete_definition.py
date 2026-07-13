"""Delete one repository-owned loop definition."""

from dataclasses import dataclass

from src.domain.commands.loops._root import (
    WorkNotFound,
    resolve_catalog_roots,
    resolve_working_root,
)
from src.domain.loop.catalog import LoopDefinitionRoots, locate_definition
from src.domain.loop.definitions import (
    LoopDefinitionNotFound,
    LoopDefinitionReadOnly,
    LoopRootUnavailable,
)
from src.domain.loop.dtos import LoopDefinitionScope
from src.domain.loop.ports import LoopDefinitionLocations, LoopDefinitionRepository
from src.domain.planning.ports import PlanningSessionRepository
from src.domain.workstore.ports import WorkStore


@dataclass(frozen=True)
class DeleteLoopDefinitionRequest:
    """Input for deleting one repository loop."""

    definition_id: str
    work_slug: str | None = None
    root_path: str | None = None
    scope: LoopDefinitionScope | None = None
    legacy_only: bool = False


def execute(
    workstore: WorkStore,
    planning_sessions: PlanningSessionRepository,
    locations: LoopDefinitionLocations,
    repository: LoopDefinitionRepository,
    req: DeleteLoopDefinitionRequest,
) -> None:
    """Delete a stored loop while preserving immutable built-ins."""
    if req.legacy_only:
        if req.work_slug is None:
            raise WorkNotFound("work slug is required for legacy loop storage")
        roots = LoopDefinitionRoots(
            library=resolve_working_root(
                workstore,
                planning_sessions,
                req.work_slug,
            )
        )
    else:
        roots = resolve_catalog_roots(
            workstore,
            planning_sessions,
            locations,
            work_slug=req.work_slug,
            root_path=req.root_path,
        )
    located = locate_definition(repository, roots, req.definition_id, scope=req.scope)
    if located.definition.scope == LoopDefinitionScope.BUILTIN or located.root is None:
        raise LoopDefinitionReadOnly("built-in loops cannot be deleted")
    repository.delete_definition(located.root, req.definition_id)


__all__ = [
    "DeleteLoopDefinitionRequest",
    "LoopDefinitionNotFound",
    "LoopDefinitionReadOnly",
    "LoopRootUnavailable",
    "WorkNotFound",
    "execute",
]
