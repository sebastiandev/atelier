"""Create or update one repository-owned loop definition."""

from dataclasses import dataclass, replace

from src.domain.loop.builtins import builtin_loop_definition
from src.domain.loop.catalog import locate_definition, writable_root
from src.domain.loop.definitions import (
    LoopDefinitionConflict,
    LoopDefinitionInvalid,
    LoopDefinitionNotFound,
    LoopDefinitionReadOnly,
    LoopRootUnavailable,
    prepare_definition,
    slugify_definition_id,
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
class SaveLoopDefinitionRequest:
    """Input for saving one complete loop definition working copy."""

    definition: LoopDefinition
    work_slug: str | None = None
    root_path: str | None = None
    expected_revision: str | None = None


def execute(
    workstore: WorkStore,
    work_roots: LoopWorkingRootRepository,
    locations: LoopDefinitionLocations,
    repository: LoopDefinitionRepository,
    req: SaveLoopDefinitionRequest,
) -> LoopDefinition:
    """Validate and persist one global or Work-local loop definition.

    On create (no ``expected_revision``) the id is minted from the name here,
    not taken from the client — a taken id then fails as an ordinary revision
    conflict. On update the id is fixed and the name may drift from it.
    """
    scope = req.definition.scope
    if scope == LoopDefinitionScope.BUILTIN:
        raise LoopDefinitionReadOnly("built-in loops must be forked before editing")
    if scope == LoopDefinitionScope.WORK:
        # Work-scoped copies are gone. Saving from one story's run setup wrote
        # a copy that shadowed the library for every story in that Work, for
        # good, with nothing on screen to say so -- an edit in July kept a Work
        # on a stale loop through every library update after it. A run is
        # already immune to later edits: it pins its own definition snapshot.
        # So the overlay never protected the run, it only decided what future
        # runs silently inherited. Persisting an edit is `Save to library`.
        raise LoopDefinitionInvalid(
            "Work-scoped loops are no longer written; save to the library instead."
        )
    definition = req.definition
    if req.expected_revision is None:
        derived = slugify_definition_id(definition.name)
        if not derived:
            raise LoopDefinitionInvalid("loop name has no id-usable characters")
        definition = replace(definition, definition_id=derived)
    if (
        scope == LoopDefinitionScope.LIBRARY
        and builtin_loop_definition(definition.definition_id) is not None
    ):
        raise LoopDefinitionReadOnly("library loops cannot replace a built-in id")
    roots = resolve_catalog_roots(
        workstore,
        work_roots,
        locations,
        work_slug=req.work_slug,
        root_path=req.root_path,
    )
    prepared = prepare_definition(replace(definition, scope=scope))
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
