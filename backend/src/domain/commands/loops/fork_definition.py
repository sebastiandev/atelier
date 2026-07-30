"""Fork a reusable loop definition into repository ownership."""

from dataclasses import dataclass

from src.domain.loop.builtins import builtin_loop_definition
from src.domain.loop.catalog import (
    locate_definition,
    writable_root,
)
from src.domain.loop.definitions import (
    LoopDefinitionConflict,
    LoopDefinitionInvalid,
    LoopDefinitionNotFound,
    LoopRootUnavailable,
    prepare_definition,
    repository_copy,
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
)
from src.domain.workstore.ports import WorkStore


@dataclass(frozen=True)
class ForkLoopDefinitionRequest:
    """Input for copying a loop into the current repository."""

    source_id: str
    definition_id: str
    name: str
    target_scope: LoopDefinitionScope | None = None
    work_slug: str | None = None
    root_path: str | None = None


def execute(
    workstore: WorkStore,
    work_roots: LoopWorkingRootRepository,
    locations: LoopDefinitionLocations,
    repository: LoopDefinitionRepository,
    req: ForkLoopDefinitionRequest,
) -> LoopDefinition:
    """Create a global or Work-local copy of an available definition."""
    target_scope = req.target_scope or (
        LoopDefinitionScope.WORK
        if req.work_slug is not None
        else LoopDefinitionScope.LIBRARY
    )
    roots = resolve_catalog_roots(
        workstore,
        work_roots,
        locations,
        work_slug=req.work_slug,
        root_path=req.root_path,
    )
    source = locate_definition(repository, roots, req.source_id).definition
    if (
        target_scope == LoopDefinitionScope.LIBRARY
        and builtin_loop_definition(req.definition_id) is not None
    ):
        raise LoopDefinitionConflict(
            f"loop definition id is reserved by a built-in: {req.definition_id}"
        )
    prepared = prepare_definition(
        repository_copy(
            source,
            definition_id=req.definition_id,
            name=req.name,
            scope=target_scope,
        )
    )
    if not prepared.valid:
        raise LoopDefinitionInvalid(" ".join(prepared.errors))
    return repository.save_definition(
        writable_root(roots, target_scope),
        prepared,
        expected_revision=None,
        scope=target_scope,
    )


__all__ = [
    "ForkLoopDefinitionRequest",
    "LoopDefinitionConflict",
    "LoopDefinitionInvalid",
    "LoopDefinitionNotFound",
    "LoopRootUnavailable",
    "WorkNotFound",
    "execute",
]
