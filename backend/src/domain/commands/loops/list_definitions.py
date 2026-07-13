"""List built-in and repository loop definitions for one Work."""

from dataclasses import dataclass

from src.domain.commands.loops._root import (
    WorkNotFound,
    resolve_catalog_roots,
    resolve_working_root,
)
from src.domain.loop.catalog import LoopDefinitionRoots, list_available_definitions
from src.domain.loop.definitions import LoopRootUnavailable
from src.domain.loop.dtos import LoopDefinition
from src.domain.loop.ports import LoopDefinitionLocations, LoopDefinitionRepository
from src.domain.planning.ports import PlanningSessionRepository
from src.domain.workstore.ports import WorkStore


@dataclass(frozen=True)
class ListLoopDefinitionsRequest:
    """Input for listing loops available to one Work."""

    work_slug: str | None = None
    root_path: str | None = None
    legacy_only: bool = False


def execute(
    workstore: WorkStore,
    planning_sessions: PlanningSessionRepository,
    locations: LoopDefinitionLocations,
    repository: LoopDefinitionRepository,
    req: ListLoopDefinitionsRequest,
) -> tuple[LoopDefinition, ...]:
    """List definitions visible in the requested global or Work catalog."""
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
    return list_available_definitions(repository, roots)


__all__ = [
    "ListLoopDefinitionsRequest",
    "LoopRootUnavailable",
    "WorkNotFound",
    "execute",
]
