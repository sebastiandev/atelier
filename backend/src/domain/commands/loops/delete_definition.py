"""Delete one repository-owned loop definition."""

from dataclasses import dataclass

from src.domain.loop.catalog import locate_definition
from src.domain.loop.definitions import (
    LoopDefinitionNotFound,
    LoopDefinitionReadOnly,
    LoopRootUnavailable,
)
from src.domain.loop.dtos import LoopDefinitionScope
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
class DeleteLoopDefinitionRequest:
    """Input for deleting one repository loop."""

    definition_id: str
    work_slug: str | None = None
    root_path: str | None = None
    scope: LoopDefinitionScope | None = None


def execute(
    workstore: WorkStore,
    work_roots: LoopWorkingRootRepository,
    locations: LoopDefinitionLocations,
    repository: LoopDefinitionRepository,
    req: DeleteLoopDefinitionRequest,
) -> None:
    """Delete a stored loop while preserving immutable built-ins."""
    roots = resolve_catalog_roots(
        workstore,
        work_roots,
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
