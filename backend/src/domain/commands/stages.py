"""Commands for the reusable standalone-stage library."""

from __future__ import annotations

from dataclasses import dataclass, replace
from pathlib import Path

from src.domain.loop.definitions import slugify_definition_id
from src.domain.loop.dtos import StageDefinition, StageDefinitionScope
from src.domain.loop.ports import (
    LoopDefinitionLocations,
    LoopDefinitionRepository,
    StageDefinitionRepository,
)
from src.domain.loop.stage_builtins import (
    builtin_stage_definition,
    builtin_stage_definitions,
)
from src.domain.loop.stages import (
    StageDefinitionConflict,
    StageDefinitionInvalid,
    StageDefinitionNotFound,
    StageDefinitionReadOnly,
    prepare_stage_definition,
    repository_stage_copy,
)


@dataclass(frozen=True)
class StageCatalogRequest:
    """Identify a reusable stage catalog and, optionally, one definition."""

    definition_id: str | None = None
    root_path: str | None = None


@dataclass(frozen=True)
class SaveStageRequest:
    """Carry a complete standalone stage working copy to persistence."""

    definition: StageDefinition
    root_path: str | None = None
    expected_revision: str | None = None


@dataclass(frozen=True)
class ForkStageRequest:
    """Describe a writable copy of an existing reusable stage."""

    source_id: str
    name: str
    root_path: str | None = None


def list_stages(
    locations: LoopDefinitionLocations,
    repository: StageDefinitionRepository,
    loops: LoopDefinitionRepository,
    req: StageCatalogRequest,
) -> tuple[StageDefinition, ...]:
    """List built-in and repository stages with linked-loop usage counts."""
    root = _root(locations, req.root_path)
    usage: dict[str, list[str]] = {}
    for loop in loops.list_definitions(root):
        for stage in loop.stages:
            if stage.stage_ref is not None:
                key = stage.stage_ref.definition_id
                usage.setdefault(key, []).append(loop.definition_id)
    rows = {item.definition_id: item for item in builtin_stage_definitions()}
    rows.update({item.definition_id: item for item in repository.list_definitions(root)})
    return tuple(
        replace(item, used_by=tuple(usage.get(item.definition_id, ())))
        for item in sorted(
            rows.values(),
            key=lambda row: (row.scope != StageDefinitionScope.BUILTIN, row.name.casefold()),
        )
    )


def get_stage(
    locations: LoopDefinitionLocations,
    repository: StageDefinitionRepository,
    loops: LoopDefinitionRepository,
    req: StageCatalogRequest,
) -> StageDefinition:
    """Return one reusable stage including its current usage count."""
    if req.definition_id is None:
        raise StageDefinitionNotFound("stage definition id is required")
    found = next(
        (
            item
            for item in list_stages(locations, repository, loops, req)
            if item.definition_id == req.definition_id
        ),
        None,
    )
    if found is None:
        raise StageDefinitionNotFound(f"stage definition not found: {req.definition_id}")
    return found


def save_stage(
    locations: LoopDefinitionLocations,
    repository: StageDefinitionRepository,
    req: SaveStageRequest,
) -> StageDefinition:
    """Validate and create or update one repository stage.

    On create the id is minted from the name (`slugify_definition_id`), like
    loops; on update the id is fixed and the name may drift from it.
    """
    if req.definition.scope == StageDefinitionScope.BUILTIN:
        raise StageDefinitionReadOnly("built-in stages must be forked before editing")
    definition = req.definition
    if req.expected_revision is None:
        derived = slugify_definition_id(definition.name)
        if not derived:
            raise StageDefinitionInvalid("stage name has no id-usable characters")
        definition = replace(
            definition,
            definition_id=derived,
            stage=replace(definition.stage, step_id=derived),
        )
    if builtin_stage_definition(definition.definition_id) is not None:
        raise StageDefinitionReadOnly("repository stages cannot replace a built-in id")
    prepared = prepare_stage_definition(
        replace(definition, scope=StageDefinitionScope.LIBRARY)
    )
    if not prepared.valid:
        raise StageDefinitionInvalid(" ".join(prepared.errors))
    return repository.save_definition(
        _root(locations, req.root_path),
        prepared,
        expected_revision=req.expected_revision,
    )


def fork_stage(
    locations: LoopDefinitionLocations,
    repository: StageDefinitionRepository,
    loops: LoopDefinitionRepository,
    req: ForkStageRequest,
) -> StageDefinition:
    """Fork a built-in or repository stage under a new repository id."""
    source = get_stage(
        locations,
        repository,
        loops,
        StageCatalogRequest(req.source_id, req.root_path),
    )
    definition_id = slugify_definition_id(req.name)
    if not definition_id:
        raise StageDefinitionInvalid("stage name has no id-usable characters")
    copy = repository_stage_copy(source, definition_id=definition_id, name=req.name)
    return save_stage(
        locations,
        repository,
        SaveStageRequest(copy, req.root_path),
    )


def delete_stage(
    locations: LoopDefinitionLocations,
    repository: StageDefinitionRepository,
    req: StageCatalogRequest,
) -> None:
    """Delete one repository stage while preserving built-ins."""
    if req.definition_id is None:
        raise StageDefinitionNotFound("stage definition id is required")
    if builtin_stage_definition(req.definition_id) is not None:
        raise StageDefinitionReadOnly("built-in stages cannot be deleted")
    repository.delete_definition(_root(locations, req.root_path), req.definition_id)


def reveal_stage(
    locations: LoopDefinitionLocations,
    repository: StageDefinitionRepository,
    req: StageCatalogRequest,
) -> Path:
    """Return the trusted directory for one saved repository stage."""
    if req.definition_id is None:
        raise StageDefinitionNotFound("stage definition id is required")
    root = _root(locations, req.root_path)
    if repository.get_definition(root, req.definition_id) is None:
        raise StageDefinitionNotFound(f"saved stage definition not found: {req.definition_id}")
    return repository.definition_dir(root, req.definition_id)


def _root(locations: LoopDefinitionLocations, root_path: str | None) -> str:
    return root_path or locations.loop_library_root()


__all__ = [
    "ForkStageRequest",
    "SaveStageRequest",
    "StageCatalogRequest",
    "StageDefinitionConflict",
    "StageDefinitionInvalid",
    "StageDefinitionNotFound",
    "StageDefinitionReadOnly",
    "delete_stage",
    "fork_stage",
    "get_stage",
    "list_stages",
    "reveal_stage",
    "save_stage",
]
