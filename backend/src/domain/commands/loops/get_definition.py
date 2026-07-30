"""Get one built-in or repository loop definition."""

from dataclasses import dataclass

from src.domain.loop.catalog import locate_definition
from src.domain.loop.definitions import LoopDefinitionNotFound, LoopRootUnavailable
from src.domain.loop.dtos import LoopDefinition, LoopDefinitionScope
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
class GetLoopDefinitionRequest:
    """Input for reading one Work-scoped loop definition."""

    definition_id: str
    work_slug: str | None = None
    root_path: str | None = None
    scope: LoopDefinitionScope | None = None


def execute(
    workstore: WorkStore,
    work_roots: LoopWorkingRootRepository,
    locations: LoopDefinitionLocations,
    repository: LoopDefinitionRepository,
    req: GetLoopDefinitionRequest,
) -> LoopDefinition:
    """Return one definition from the requested global or Work catalog."""
    roots = resolve_catalog_roots(
        workstore,
        work_roots,
        locations,
        work_slug=req.work_slug,
        root_path=req.root_path,
    )
    return locate_definition(
        repository,
        roots,
        req.definition_id,
        scope=req.scope,
    ).definition


__all__ = [
    "GetLoopDefinitionRequest",
    "LoopDefinitionNotFound",
    "LoopRootUnavailable",
    "WorkNotFound",
    "execute",
]
