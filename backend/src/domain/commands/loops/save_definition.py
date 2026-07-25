"""Create or update one repository-owned loop definition."""

from dataclasses import dataclass, replace

from src.domain.loop.builtins import builtin_loop_definition
from src.domain.loop.catalog import LoopDefinitionRoots, locate_definition, writable_root
from src.domain.loop.definitions import (
    LoopDefinitionConflict,
    LoopDefinitionInvalid,
    LoopDefinitionNotFound,
    LoopDefinitionReadOnly,
    LoopRootUnavailable,
    prepare_definition,
)
from src.domain.loop.dtos import LoopDefinition, LoopDefinitionScope
from src.domain.loop.ports import (
    LoopDefinitionLocations,
    LoopDefinitionRepository,
    LoopWorkingRootRepository,
)
from src.domain.loop.roots import (
    WorkNotFound,
    resolve_catalog_roots,
    resolve_working_root,
)
from src.domain.workstore.ports import WorkStore


@dataclass(frozen=True)
class SaveLoopDefinitionRequest:
    """Input for saving one complete loop definition working copy."""

    definition: LoopDefinition
    work_slug: str | None = None
    root_path: str | None = None
    expected_revision: str | None = None
    legacy_only: bool = False


def execute(
    workstore: WorkStore,
    work_roots: LoopWorkingRootRepository,
    locations: LoopDefinitionLocations,
    repository: LoopDefinitionRepository,
    req: SaveLoopDefinitionRequest,
) -> LoopDefinition:
    """Validate and persist one global or Work-local loop definition."""
    scope = LoopDefinitionScope.REPOSITORY if req.legacy_only else req.definition.scope
    if scope == LoopDefinitionScope.BUILTIN:
        raise LoopDefinitionReadOnly("built-in loops must be forked before editing")
    if (
        scope == LoopDefinitionScope.REPOSITORY
        and builtin_loop_definition(req.definition.definition_id) is not None
    ):
        raise LoopDefinitionReadOnly("repository loops cannot replace a built-in id")
    if req.legacy_only:
        if req.work_slug is None:
            raise WorkNotFound("work slug is required for legacy loop storage")
        roots = LoopDefinitionRoots(
            library=resolve_working_root(
                workstore,
                work_roots,
                req.work_slug,
            )
        )
    else:
        roots = resolve_catalog_roots(
            workstore,
            work_roots,
            locations,
            work_slug=req.work_slug,
            root_path=req.root_path,
        )
    prepared = prepare_definition(replace(req.definition, scope=scope))
    if not prepared.valid:
        raise LoopDefinitionInvalid(" ".join(prepared.errors))
    root = writable_root(roots, scope)
    if req.expected_revision is not None:
        try:
            located = locate_definition(
                repository,
                roots,
                prepared.definition_id,
                scope=scope,
            )
        except LoopDefinitionNotFound:
            pass
        else:
            if located.root is not None:
                root = located.root
    return repository.save_definition(
        root,
        prepared,
        expected_revision=req.expected_revision,
        scope=scope,
    )


__all__ = [
    "LoopDefinitionConflict",
    "LoopDefinitionInvalid",
    "LoopDefinitionReadOnly",
    "LoopRootUnavailable",
    "SaveLoopDefinitionRequest",
    "WorkNotFound",
    "execute",
]
