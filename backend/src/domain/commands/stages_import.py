"""Preview and commit an imported standalone-stage transport document.

A standalone stage has no ``use:`` references, so import needs no
reconstitution — only a single id-collision decision, mirroring a normal
mint-from-name create.
"""

from __future__ import annotations

from dataclasses import dataclass, replace

from src.domain.commands.stages import (
    SaveStageRequest,
    StageCatalogRequest,
    StageDefinitionConflict,
    StageDefinitionInvalid,
    StageDefinitionReadOnly,
    list_stages,
    save_stage,
)
from src.domain.loop.definitions import slugify_definition_id
from src.domain.loop.dtos import StageDefinition, StageDefinitionScope
from src.domain.loop.ports import (
    LoopDefinitionLocations,
    LoopDefinitionRepository,
    StageDefinitionRepository,
)
from src.domain.loop.stages import prepare_stage_definition
from src.domain.loop.transport import (
    ImportedStandaloneStage,
    TransportInvalid,
    parse_stage_import,
    stage_command_prefixes,
    stage_grants_write,
)

_REPLACE = "replace"
_USE_EXISTING = "use_existing"
_NEW = "new"


class StageImportNotAccepted(ValueError):
    """The importer did not accept a surfaced approved-command prefix."""


class StageImportConflictUnresolved(ValueError):
    """An id-collision has no importer resolution."""


@dataclass(frozen=True)
class StageImportPreview:
    """The read-only decision surface for one uploaded stage document."""

    name: str
    derived_id: str
    id_collision: bool
    same_revision: bool
    local_scope: str | None
    local_revision: str | None
    used_by_count: int
    valid: bool
    errors: tuple[str, ...]
    command_prefixes: tuple[str, ...]
    grants_write: bool


@dataclass(frozen=True)
class PreviewStageImportRequest:
    """Inputs for previewing one uploaded stage document."""

    document: object
    name_override: str | None = None
    root_path: str | None = None


@dataclass(frozen=True)
class ImportStageRequest:
    """Inputs for committing one uploaded stage document."""

    document: object
    name_override: str | None = None
    accepted_command_prefixes: tuple[str, ...] = ()
    action: str | None = None
    new_name: str | None = None
    root_path: str | None = None


def preview(
    locations: LoopDefinitionLocations,
    stage_repository: StageDefinitionRepository,
    loop_repository: LoopDefinitionRepository,
    req: PreviewStageImportRequest,
) -> StageImportPreview:
    """Classify one uploaded stage against the local library (no writes)."""
    imported = parse_stage_import(req.document)
    name = req.name_override or imported.name
    derived_id = slugify_definition_id(name)
    local = _local_index(locations, stage_repository, loop_repository, req.root_path).get(
        derived_id
    )
    candidate = _candidate(imported, derived_id or "placeholder", name)
    same_revision = local is not None and local.revision == candidate.revision
    return StageImportPreview(
        name=name,
        derived_id=derived_id,
        id_collision=local is not None,
        same_revision=same_revision,
        local_scope=local.scope.value if local is not None else None,
        local_revision=local.revision if local is not None else None,
        used_by_count=len(local.used_by) if local is not None else 0,
        valid=candidate.valid,
        errors=candidate.errors,
        command_prefixes=stage_command_prefixes(imported.stage),
        grants_write=stage_grants_write(imported.stage),
    )


def commit(
    locations: LoopDefinitionLocations,
    stage_repository: StageDefinitionRepository,
    loop_repository: LoopDefinitionRepository,
    req: ImportStageRequest,
) -> StageDefinition:
    """Create the imported stage, resolving a single id-collision decision.

    Preconditions: every surfaced approved-command prefix is accepted.
    Postconditions: exactly one stage is created or replaced, or nothing is
    written (``use_existing`` / identical content).
    """
    imported = parse_stage_import(req.document)
    name = req.name_override or imported.name
    derived_id = slugify_definition_id(name)
    if not derived_id:
        raise StageDefinitionInvalid("stage name has no id-usable characters")
    _require_accepted(imported, req.accepted_command_prefixes)

    local = _local_index(locations, stage_repository, loop_repository, req.root_path).get(
        derived_id
    )
    candidate = _candidate(imported, derived_id, name)

    if local is None:
        return save_stage(
            locations,
            stage_repository,
            SaveStageRequest(candidate, req.root_path, expected_revision=None),
        )
    if req.action == _USE_EXISTING or local.revision == candidate.revision:
        return local
    if req.action == _REPLACE:
        return save_stage(
            locations,
            stage_repository,
            SaveStageRequest(candidate, req.root_path, expected_revision=local.revision),
        )
    if req.action == _NEW:
        renamed = req.new_name or name
        return save_stage(
            locations,
            stage_repository,
            SaveStageRequest(
                replace(
                    candidate,
                    name=renamed,
                    stage=replace(candidate.stage, name=renamed),
                ),
                req.root_path,
                expected_revision=None,
            ),
        )
    raise StageImportConflictUnresolved(
        f"stage id {derived_id!r} already exists and needs a resolution"
    )


def _candidate(
    imported: ImportedStandaloneStage, definition_id: str, name: str
) -> StageDefinition:
    return prepare_stage_definition(
        StageDefinition(
            definition_id=definition_id,
            name=name,
            description=imported.description,
            scope=StageDefinitionScope.LIBRARY,
            forked_from=imported.forked_from,
            outcomes=imported.outcomes,
            stage=replace(
                imported.stage,
                step_id=definition_id,
                name=name,
                transitions={},
                stage_ref=None,
                overrides=None,
            ),
        )
    )


def _require_accepted(
    imported: ImportedStandaloneStage, accepted: tuple[str, ...]
) -> None:
    missing = set(stage_command_prefixes(imported.stage)) - set(accepted)
    if missing:
        raise StageImportNotAccepted(
            "approved command prefixes must be accepted: "
            + ", ".join(sorted(missing))
        )


def _local_index(
    locations: LoopDefinitionLocations,
    stage_repository: StageDefinitionRepository,
    loop_repository: LoopDefinitionRepository,
    root_path: str | None,
) -> dict[str, StageDefinition]:
    return {
        item.definition_id: item
        for item in list_stages(
            locations,
            stage_repository,
            loop_repository,
            StageCatalogRequest(root_path=root_path),
        )
    }


__all__ = [
    "ImportStageRequest",
    "PreviewStageImportRequest",
    "StageDefinitionConflict",
    "StageDefinitionInvalid",
    "StageDefinitionReadOnly",
    "StageImportConflictUnresolved",
    "StageImportNotAccepted",
    "StageImportPreview",
    "TransportInvalid",
    "commit",
    "preview",
]
