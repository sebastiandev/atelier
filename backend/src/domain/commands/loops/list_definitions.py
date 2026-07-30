"""List built-in and repository loop definitions for one Work."""

from dataclasses import dataclass

from src.domain.loop.catalog import list_available_definitions
from src.domain.loop.definitions import LoopRootUnavailable
from src.domain.loop.dtos import LoopDefinition
from src.domain.loop.ports import (
    LoopDefinitionLocations,
    LoopDefinitionRepository,
    LoopWorkingRootRepository,
)
from src.domain.loop.roots import (
    WorkNotFound,
    resolve_catalog_roots,
)
from src.domain.workstore.ports import WorkStore


@dataclass(frozen=True)
class ListLoopDefinitionsRequest:
    """Input for listing loops available to one Work."""

    work_slug: str | None = None
    root_path: str | None = None


def execute(
    workstore: WorkStore,
    work_roots: LoopWorkingRootRepository,
    locations: LoopDefinitionLocations,
    repository: LoopDefinitionRepository,
    req: ListLoopDefinitionsRequest,
) -> tuple[LoopDefinition, ...]:
    """List definitions visible in the requested global or Work catalog."""
    roots = resolve_catalog_roots(
        workstore,
        work_roots,
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
