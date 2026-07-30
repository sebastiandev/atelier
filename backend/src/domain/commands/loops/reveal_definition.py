"""Resolve the filesystem directory for one repository loop definition."""

from dataclasses import dataclass
from pathlib import Path

from src.domain.loop.catalog import locate_definition
from src.domain.loop.definitions import LoopDefinitionNotFound, LoopRootUnavailable
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
class RevealLoopDefinitionRequest:
    """Command input for revealing one saved repository loop."""

    definition_id: str
    work_slug: str | None = None
    root_path: str | None = None
    scope: LoopDefinitionScope | None = None


def execute(
    workstore: WorkStore,
    work_roots: LoopWorkingRootRepository,
    locations: LoopDefinitionLocations,
    repository: LoopDefinitionRepository,
    req: RevealLoopDefinitionRequest,
) -> Path:
    """Return the trusted directory for a saved loop.

    Preconditions: the requested stored definition and owning root exist.
    Postconditions: returns a root-contained path without changing filesystem state.
    """
    roots = resolve_catalog_roots(
        workstore,
        work_roots,
        locations,
        work_slug=req.work_slug,
        root_path=req.root_path,
    )
    located = locate_definition(repository, roots, req.definition_id, scope=req.scope)
    if located.root is None:
        raise LoopDefinitionNotFound(f"saved loop definition not found: {req.definition_id}")
    return repository.definition_dir(located.root, req.definition_id)


__all__ = [
    "LoopDefinitionNotFound",
    "LoopRootUnavailable",
    "RevealLoopDefinitionRequest",
    "WorkNotFound",
    "execute",
]
