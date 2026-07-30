"""Serialise one resolved loop into a portable transport document."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from src.domain.loop.catalog import locate_definition
from src.domain.loop.definitions import LoopDefinitionNotFound, LoopRootUnavailable
from src.domain.loop.dtos import LoopDefinition, LoopDefinitionScope, StageDefinition
from src.domain.loop.ports import (
    LoopDefinitionLocations,
    LoopDefinitionRepository,
    LoopWorkingRootRepository,
    StageDefinitionRepository,
)
from src.domain.loop.roots import WorkNotFound, resolve_catalog_roots
from src.domain.loop.stage_builtins import builtin_stage_definition
from src.domain.loop.transport import export_loop
from src.domain.workstore.ports import WorkStore


@dataclass(frozen=True)
class ExportLoopDefinitionRequest:
    """Identify one loop to serialise for sharing."""

    definition_id: str
    work_slug: str | None = None
    root_path: str | None = None
    scope: LoopDefinitionScope | None = None


def execute(
    workstore: WorkStore,
    work_roots: LoopWorkingRootRepository,
    locations: LoopDefinitionLocations,
    loop_repository: LoopDefinitionRepository,
    stage_repository: StageDefinitionRepository,
    req: ExportLoopDefinitionRequest,
) -> dict[str, Any]:
    """Return the transport document for one resolved loop.

    Preconditions: ``req.definition_id`` names a readable loop in the target
    catalog. Postconditions: linked stages carry their source outcomes; no
    filesystem content is changed.
    """
    roots = resolve_catalog_roots(
        workstore,
        work_roots,
        locations,
        work_slug=req.work_slug,
        root_path=req.root_path,
    )
    definition = locate_definition(
        loop_repository,
        roots,
        req.definition_id,
        scope=req.scope,
    ).definition
    linked_sources = _linked_sources(
        stage_repository,
        (roots.work, roots.library),
        definition,
    )
    return export_loop(definition, linked_sources)


def _linked_sources(
    stage_repository: StageDefinitionRepository,
    roots: tuple[str | None, str | None],
    definition: LoopDefinition,
) -> dict[str, StageDefinition]:
    sources: dict[str, StageDefinition] = {}
    for stage in definition.stages:
        if stage.stage_ref is None or stage.stage_ref.definition_id in sources:
            continue
        source_id = stage.stage_ref.definition_id
        found = builtin_stage_definition(source_id)
        for root in roots:
            if found is not None:
                break
            if root is not None:
                found = stage_repository.get_definition(root, source_id)
        if found is not None:
            sources[source_id] = found
    return sources


__all__ = [
    "ExportLoopDefinitionRequest",
    "LoopDefinitionNotFound",
    "LoopRootUnavailable",
    "WorkNotFound",
    "execute",
]
