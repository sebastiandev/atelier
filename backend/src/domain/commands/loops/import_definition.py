"""Preview and atomically commit an imported loop transport document.

Import is a two-call use case. ``preview`` is a read-only classification of
an uploaded document against the recipient's own library (id collision,
per-stage inline/link status, the safety surface). ``commit`` reconstitutes
any linked stages and creates the loop as a single all-or-nothing unit: if
any stage or the loop itself fails, every stage this call created or replaced
is rolled back so no half-imported loop survives.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, replace

from src.domain.loop.builtins import builtin_loop_definitions
from src.domain.loop.definitions import (
    LoopDefinitionConflict,
    LoopDefinitionInvalid,
    slugify_definition_id,
    validate_definition,
)
from src.domain.loop.dtos import (
    LoopDefinition,
    LoopDefinitionScope,
    LoopReportField,
    LoopReportSchema,
    LoopStepDefinition,
    StageDefinition,
    StageDefinitionRef,
    StageDefinitionScope,
)
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
    StageDefinitionReadOnly,
    prepare_stage_definition,
)
from src.domain.loop.transport import (
    ImportedLoop,
    ImportedStage,
    StageImportStatus,
    classify_linked_stage,
    overrides_from_drift,
    parse_loop_import,
    reconstituted_stage_definition,
    stage_command_prefixes,
    stage_grants_write,
)

_REPORT_SCHEMA = LoopReportSchema(
    schema_id="multi-stage-loop-report",
    fields=(LoopReportField("summary", "Summary", allow_explicit_none=False),),
)

_REPLACE = "replace"
_USE_EXISTING = "use_existing"
_NEW = "new"


class ImportNotAccepted(ValueError):
    """The importer did not accept a surfaced approved-command prefix."""


class ImportConflictUnresolved(ValueError):
    """A different-revision stage conflict has no importer resolution."""


# ---------------------------------------------------------------------------
# Preview
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class StageImportPlan:
    """How one imported stage will be handled, for the preview surface."""

    stage_id: str
    name: str
    kind: str
    status: StageImportStatus
    linked_id: str | None
    local_exists: bool
    local_scope: str | None
    local_revision: str | None
    used_by_count: int
    command_prefixes: tuple[str, ...]
    grants_write: bool


@dataclass(frozen=True)
class LoopImportPreview:
    """The read-only decision surface for one uploaded loop document."""

    name: str
    derived_id: str
    id_collision: bool
    valid: bool
    errors: tuple[str, ...]
    stages: tuple[StageImportPlan, ...]


@dataclass(frozen=True)
class PreviewLoopImportRequest:
    """Inputs for previewing one uploaded loop document."""

    document: object
    name_override: str | None = None
    root_path: str | None = None


def preview(
    locations: LoopDefinitionLocations,
    loop_repository: LoopDefinitionRepository,
    stage_repository: StageDefinitionRepository,
    req: PreviewLoopImportRequest,
) -> LoopImportPreview:
    """Classify one uploaded loop against the local library.

    Preconditions: ``req.document`` is a parsed transport mapping.
    Postconditions: no filesystem content is changed.
    """
    imported = parse_loop_import(req.document)
    name = req.name_override or imported.name
    derived_id = slugify_definition_id(name)
    root = req.root_path or locations.loop_library_root()
    local_stages = _local_stage_index(loop_repository, stage_repository, root)
    id_collision = bool(derived_id) and derived_id in _existing_loop_ids(
        loop_repository, root
    )
    plans = tuple(_plan_stage(item, local_stages) for item in imported.stages)
    errors = validate_definition(_inline_reconstruction(imported, name, derived_id))
    return LoopImportPreview(
        name=name,
        derived_id=derived_id,
        id_collision=id_collision,
        valid=not errors,
        errors=errors,
        stages=plans,
    )


def _plan_stage(
    item: ImportedStage,
    local_stages: dict[str, StageDefinition],
) -> StageImportPlan:
    local = (
        local_stages.get(item.linked_from.definition_id)
        if item.linked_from is not None
        else None
    )
    return StageImportPlan(
        stage_id=item.stage.step_id,
        name=item.stage.name,
        kind=item.stage.kind.value,
        status=classify_linked_stage(item, local),
        linked_id=item.linked_from.definition_id if item.linked_from else None,
        local_exists=local is not None,
        local_scope=local.scope.value if local is not None else None,
        local_revision=local.revision if local is not None else None,
        used_by_count=len(local.used_by) if local is not None else 0,
        command_prefixes=stage_command_prefixes(item.stage),
        grants_write=stage_grants_write(item.stage),
    )


# ---------------------------------------------------------------------------
# Commit
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class StageResolution:
    """One importer decision for a different-revision stage conflict."""

    stage_id: str
    action: str
    new_name: str | None = None


@dataclass(frozen=True)
class ImportLoopRequest:
    """Inputs for committing one uploaded loop document."""

    document: object
    name_override: str | None = None
    accepted_command_prefixes: tuple[str, ...] = ()
    resolutions: tuple[StageResolution, ...] = ()
    root_path: str | None = None


def commit(
    locations: LoopDefinitionLocations,
    loop_repository: LoopDefinitionRepository,
    stage_repository: StageDefinitionRepository,
    req: ImportLoopRequest,
) -> LoopDefinition:
    """Reconstitute stages and create the imported loop atomically.

    Preconditions: ``req.document`` is a parsed transport mapping; every
    different-revision conflict carries a resolution and every surfaced
    approved-command prefix is accepted. Postconditions: the loop and all its
    new/replaced stages commit together, or nothing is written.
    """
    imported = parse_loop_import(req.document)
    name = req.name_override or imported.name
    derived_id = slugify_definition_id(name)
    if not derived_id:
        raise LoopDefinitionInvalid("loop name has no id-usable characters")
    root = req.root_path or locations.loop_library_root()

    errors = validate_definition(_inline_reconstruction(imported, name, derived_id))
    if errors:
        raise LoopDefinitionInvalid(" ".join(errors))
    _require_accepted(imported, req.accepted_command_prefixes)
    if derived_id in _existing_loop_ids(loop_repository, root):
        raise LoopDefinitionConflict(f"loop id already exists: {derived_id}")

    local_stages = _local_stage_index(loop_repository, stage_repository, root)
    resolutions = {item.stage_id: item for item in req.resolutions}
    taken = (
        set(local_stages)
        | {item.definition_id for item in builtin_stage_definitions()}
    )
    created_targets: dict[str, StageDefinitionRef] = {}
    compensations: list[Callable[[], None]] = []

    try:
        loop_stages = [
            _reconstitute(
                item,
                local_stages,
                resolutions,
                created_targets,
                taken,
                stage_repository,
                root,
                compensations,
            )
            for item in imported.stages
        ]
        loop = LoopDefinition(
            definition_id=derived_id,
            name=name,
            trigger="artifact_or_objective",
            report_schema=_REPORT_SCHEMA,
            description=imported.description,
            scope=LoopDefinitionScope.LIBRARY,
            stages=tuple(loop_stages),
        )
        compensations.append(
            lambda: _safe_delete_loop(loop_repository, root, derived_id)
        )
        saved = loop_repository.save_definition(
            root,
            loop,
            expected_revision=None,
            scope=LoopDefinitionScope.LIBRARY,
        )
        if not saved.valid:
            raise LoopDefinitionInvalid(" ".join(saved.errors))
        return saved
    except Exception:
        for undo in reversed(compensations):
            undo()
        raise


def _reconstitute(
    item: ImportedStage,
    local_stages: dict[str, StageDefinition],
    resolutions: dict[str, StageResolution],
    created_targets: dict[str, StageDefinitionRef],
    taken: set[str],
    stage_repository: StageDefinitionRepository,
    root: str,
    compensations: list[Callable[[], None]],
) -> LoopStepDefinition:
    if item.linked_from is None:
        return replace(item.stage, stage_ref=None, overrides=None)
    source_id = item.linked_from.definition_id
    local = local_stages.get(source_id)
    status = classify_linked_stage(item, local)

    if status == StageImportStatus.LINK_CLEAN:
        if local is not None:
            # Same-revision base: link to it and re-apply the loop-local diff,
            # so a renamed / re-instructed stage keeps those changes.
            ref = StageDefinitionRef(source_id, local.revision)
            overrides = overrides_from_drift(item.stage, local.stage)
            return replace(item.stage, stage_ref=ref, overrides=overrides)
        if source_id in created_targets:
            ref = created_targets[source_id]
        else:
            ref = _create_stage(item, source_id, stage_repository, root, compensations)
            created_targets[source_id] = ref
            taken.add(source_id)
        return replace(item.stage, stage_ref=ref, overrides=None)

    resolution = resolutions.get(item.stage.step_id)
    if resolution is None:
        raise ImportConflictUnresolved(
            f"stage {item.stage.step_id!r} needs a conflict resolution"
        )
    if resolution.action == _USE_EXISTING and local is not None:
        # Keep the local stage and discard the imported body (per the decision
        # table): the loop runs the recipient's version, no overrides.
        return replace(
            item.stage,
            stage_ref=StageDefinitionRef(source_id, local.revision),
            overrides=None,
        )
    if resolution.action == _REPLACE and local is not None:
        ref = created_targets.get(source_id) or _replace_stage(
            item, local, stage_repository, root, compensations
        )
        created_targets[source_id] = ref
    elif resolution.action == _NEW:
        new_id = _mint_unique_id(
            slugify_definition_id(resolution.new_name or item.stage.name), taken
        )
        taken.add(new_id)
        ref = _create_stage(item, new_id, stage_repository, root, compensations)
    else:
        raise ImportConflictUnresolved(
            f"stage {item.stage.step_id!r} has an invalid resolution: {resolution.action!r}"
        )
    return replace(item.stage, stage_ref=ref, overrides=None)


def _create_stage(
    item: ImportedStage,
    definition_id: str,
    stage_repository: StageDefinitionRepository,
    root: str,
    compensations: list[Callable[[], None]],
) -> StageDefinitionRef:
    saved = _persist_stage(
        stage_repository,
        root,
        reconstituted_stage_definition(item, definition_id),
        expected_revision=None,
    )
    compensations.append(
        lambda: _safe_delete(stage_repository, root, definition_id)
    )
    return StageDefinitionRef(definition_id, saved.revision)


def _replace_stage(
    item: ImportedStage,
    local: StageDefinition,
    stage_repository: StageDefinitionRepository,
    root: str,
    compensations: list[Callable[[], None]],
) -> StageDefinitionRef:
    saved = _persist_stage(
        stage_repository,
        root,
        reconstituted_stage_definition(item, local.definition_id),
        expected_revision=local.revision,
    )
    replaced_revision = saved.revision
    compensations.append(
        lambda: _restore(stage_repository, root, local, replaced_revision)
    )
    return StageDefinitionRef(local.definition_id, replaced_revision)


def _persist_stage(
    stage_repository: StageDefinitionRepository,
    root: str,
    definition: StageDefinition,
    *,
    expected_revision: str | None,
) -> StageDefinition:
    prepared = prepare_stage_definition(
        replace(definition, scope=StageDefinitionScope.LIBRARY)
    )
    if not prepared.valid:
        raise StageDefinitionInvalid(" ".join(prepared.errors))
    if builtin_stage_definition(prepared.definition_id) is not None:
        raise StageDefinitionReadOnly(
            f"cannot import over the built-in stage {prepared.definition_id!r}"
        )
    return stage_repository.save_definition(
        root, prepared, expected_revision=expected_revision
    )


def _restore(
    stage_repository: StageDefinitionRepository,
    root: str,
    local: StageDefinition,
    replaced_revision: str,
) -> None:
    stage_repository.save_definition(
        root,
        replace(local, scope=StageDefinitionScope.LIBRARY),
        expected_revision=replaced_revision,
    )


def _safe_delete(
    stage_repository: StageDefinitionRepository, root: str, definition_id: str
) -> None:
    try:
        stage_repository.delete_definition(root, definition_id)
    except StageDefinitionConflict:  # pragma: no cover - defensive
        pass
    except ValueError:  # pragma: no cover - already gone
        pass


# ---------------------------------------------------------------------------
# Shared helpers
# ---------------------------------------------------------------------------


def _inline_reconstruction(
    imported: ImportedLoop, name: str, definition_id: str
) -> LoopDefinition:
    return LoopDefinition(
        definition_id=definition_id,
        name=name,
        trigger="artifact_or_objective",
        report_schema=_REPORT_SCHEMA,
        description=imported.description,
        scope=LoopDefinitionScope.LIBRARY,
        stages=tuple(
            replace(item.stage, stage_ref=None, overrides=None)
            for item in imported.stages
        ),
    )


def _require_accepted(
    imported: ImportedLoop, accepted: tuple[str, ...]
) -> None:
    surfaced: set[str] = set()
    for item in imported.stages:
        surfaced.update(stage_command_prefixes(item.stage))
    missing = surfaced - set(accepted)
    if missing:
        raise ImportNotAccepted(
            "approved command prefixes must be accepted: "
            + ", ".join(sorted(missing))
        )


def _local_stage_index(
    loop_repository: LoopDefinitionRepository,
    stage_repository: StageDefinitionRepository,
    root: str,
) -> dict[str, StageDefinition]:
    """Built-in and library stages keyed by id, each with its loop usage.

    Mirrors the stage-catalog listing without importing that feature command,
    so the loop-command layer stays free of sibling command imports.
    """
    usage: dict[str, list[str]] = {}
    for loop in loop_repository.list_definitions(root):
        for stage in loop.stages:
            if stage.stage_ref is not None:
                usage.setdefault(stage.stage_ref.definition_id, []).append(
                    loop.definition_id
                )
    index: dict[str, StageDefinition] = {
        item.definition_id: item for item in builtin_stage_definitions()
    }
    index.update(
        {item.definition_id: item for item in stage_repository.list_definitions(root)}
    )
    return {
        definition_id: replace(item, used_by=tuple(usage.get(definition_id, ())))
        for definition_id, item in index.items()
    }


def _safe_delete_loop(
    loop_repository: LoopDefinitionRepository, root: str, definition_id: str
) -> None:
    try:
        loop_repository.delete_definition(root, definition_id)
    except ValueError:  # pragma: no cover - never written / already gone
        pass


def _existing_loop_ids(
    loop_repository: LoopDefinitionRepository, root: str
) -> set[str]:
    ids = {item.definition_id for item in builtin_loop_definitions()}
    ids.update(item.definition_id for item in loop_repository.list_definitions(root))
    return ids


def _mint_unique_id(base: str, taken: set[str]) -> str:
    if not base:
        raise LoopDefinitionInvalid("new stage name has no id-usable characters")
    if base not in taken:
        return base
    suffix = 2
    while f"{base}-{suffix}" in taken:
        suffix += 1
    return f"{base}-{suffix}"


__all__ = [
    "ImportConflictUnresolved",
    "ImportLoopRequest",
    "ImportNotAccepted",
    "LoopDefinitionConflict",
    "LoopDefinitionInvalid",
    "LoopImportPreview",
    "PreviewLoopImportRequest",
    "StageImportPlan",
    "StageResolution",
    "commit",
    "preview",
]
