"""REST routes for the reusable standalone-stage library."""

from __future__ import annotations

import subprocess
from dataclasses import replace
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Request, status

from src.application.http.schemas import (
    ForkStageDefinitionRequest,
    ImportPreviewRequest,
    ImportStageRequest,
    LoopStepDefinitionSchema,
    SaveStageDefinitionRequest,
    StageDefinitionResponse,
    StageImportPreviewResponse,
    TransportExportResponse,
)
from src.domain.commands import stages, stages_import
from src.domain.loop.dtos import StageDefinition, StageDefinitionScope
from src.domain.loop.ports import (
    LoopDefinitionLocations,
    LoopDefinitionRepository,
    StageDefinitionRepository,
)
from src.domain.loop.snapshots import loop_stage_from_snapshot, loop_stage_snapshot
from src.domain.loop.transport import TransportInvalid, export_stage
from src.infrastructure.filesystem.loop_transport import (
    dump_transport_document,
    parse_transport_document,
)
from src.infrastructure.filesystem.reveal import open_in_file_browser

router = APIRouter(tags=["stages"])


def _repository(request: Request) -> StageDefinitionRepository:
    return request.app.state.stage_definitions  # type: ignore[no-any-return]


def _loops(request: Request) -> LoopDefinitionRepository:
    return request.app.state.loop_definitions  # type: ignore[no-any-return]


def _locations(request: Request) -> LoopDefinitionLocations:
    return request.app.state.workspace_paths  # type: ignore[no-any-return]


RepositoryDep = Annotated[StageDefinitionRepository, Depends(_repository)]
LoopsDep = Annotated[LoopDefinitionRepository, Depends(_loops)]
LocationsDep = Annotated[LoopDefinitionLocations, Depends(_locations)]


@router.get("/stages", response_model=list[StageDefinitionResponse])
def list_stages_endpoint(
    repository: RepositoryDep,
    loops: LoopsDep,
    locations: LocationsDep,
    root_path: str | None = None,
) -> list[StageDefinitionResponse]:
    return [
        _response(item)
        for item in stages.list_stages(
            locations, repository, loops, stages.StageCatalogRequest(root_path=root_path)
        )
    ]


@router.get("/stages/{definition_id}", response_model=StageDefinitionResponse)
def get_stage_endpoint(
    definition_id: str,
    repository: RepositoryDep,
    loops: LoopsDep,
    locations: LocationsDep,
    root_path: str | None = None,
) -> StageDefinitionResponse:
    try:
        return _response(
            stages.get_stage(
                locations,
                repository,
                loops,
                stages.StageCatalogRequest(definition_id, root_path),
            )
        )
    except stages.StageDefinitionNotFound as exc:
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail=str(exc)) from exc


@router.get(
    "/stages/{definition_id}/export",
    response_model=TransportExportResponse,
)
def export_stage_endpoint(
    definition_id: str,
    repository: RepositoryDep,
    loops: LoopsDep,
    locations: LocationsDep,
    root_path: str | None = None,
) -> TransportExportResponse:
    try:
        definition = stages.get_stage(
            locations,
            repository,
            loops,
            stages.StageCatalogRequest(definition_id, root_path),
        )
    except stages.StageDefinitionNotFound as exc:
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail=str(exc)) from exc
    return TransportExportResponse(
        filename=f"{definition_id}.stage.yaml",
        content=dump_transport_document(export_stage(definition)),
    )


@router.post(
    "/stages/import/preview",
    response_model=StageImportPreviewResponse,
)
def preview_stage_import_endpoint(
    payload: ImportPreviewRequest,
    repository: RepositoryDep,
    loops: LoopsDep,
    locations: LocationsDep,
) -> StageImportPreviewResponse:
    try:
        preview = stages_import.preview(
            locations,
            repository,
            loops,
            stages_import.PreviewStageImportRequest(
                document=parse_transport_document(payload.content),
                name_override=payload.name,
                root_path=payload.root_path,
            ),
        )
    except TransportInvalid as exc:
        raise HTTPException(422, detail=str(exc)) from exc
    return StageImportPreviewResponse(
        name=preview.name,
        derived_id=preview.derived_id,
        id_collision=preview.id_collision,
        same_revision=preview.same_revision,
        local_scope=preview.local_scope,
        local_revision=preview.local_revision,
        used_by_count=preview.used_by_count,
        valid=preview.valid,
        errors=list(preview.errors),
        command_prefixes=list(preview.command_prefixes),
        grants_write=preview.grants_write,
    )


@router.post(
    "/stages/import",
    response_model=StageDefinitionResponse,
    status_code=status.HTTP_201_CREATED,
)
def import_stage_endpoint(
    payload: ImportStageRequest,
    repository: RepositoryDep,
    loops: LoopsDep,
    locations: LocationsDep,
) -> StageDefinitionResponse:
    try:
        created = stages_import.commit(
            locations,
            repository,
            loops,
            stages_import.ImportStageRequest(
                document=parse_transport_document(payload.content),
                name_override=payload.name,
                accepted_command_prefixes=tuple(payload.accepted_command_prefixes),
                action=payload.action,
                new_name=payload.new_name,
                root_path=payload.root_path,
            ),
        )
    except stages_import.StageDefinitionConflict as exc:
        raise HTTPException(409, detail=str(exc)) from exc
    except (
        TransportInvalid,
        stages_import.StageDefinitionInvalid,
        stages_import.StageDefinitionReadOnly,
        stages_import.StageImportNotAccepted,
        stages_import.StageImportConflictUnresolved,
        ValueError,
    ) as exc:
        raise HTTPException(422, detail=str(exc)) from exc
    return _response(created)


@router.post(
    "/stages",
    response_model=StageDefinitionResponse,
    status_code=status.HTTP_201_CREATED,
)
def create_stage_endpoint(
    payload: SaveStageDefinitionRequest,
    repository: RepositoryDep,
    locations: LocationsDep,
) -> StageDefinitionResponse:
    if payload.expected_revision is not None:
        raise HTTPException(422, detail="POST creates stages and cannot include expected_revision")
    return _save(payload, repository, locations)


@router.put("/stages/{definition_id}", response_model=StageDefinitionResponse)
@router.patch("/stages/{definition_id}", response_model=StageDefinitionResponse)
def update_stage_endpoint(
    definition_id: str,
    payload: SaveStageDefinitionRequest,
    repository: RepositoryDep,
    locations: LocationsDep,
) -> StageDefinitionResponse:
    if payload.id != definition_id:
        raise HTTPException(422, detail="path and payload stage ids must match")
    if payload.expected_revision is None:
        raise HTTPException(422, detail="PUT/PATCH updates require expected_revision")
    return _save(payload, repository, locations)


@router.post(
    "/stages/{definition_id}/fork",
    response_model=StageDefinitionResponse,
    status_code=status.HTTP_201_CREATED,
)
def fork_stage_endpoint(
    definition_id: str,
    payload: ForkStageDefinitionRequest,
    repository: RepositoryDep,
    loops: LoopsDep,
    locations: LocationsDep,
) -> StageDefinitionResponse:
    try:
        return _response(
            stages.fork_stage(
                locations,
                repository,
                loops,
                stages.ForkStageRequest(
                    source_id=definition_id,
                    name=payload.name,
                    root_path=payload.root_path,
                ),
            )
        )
    except stages.StageDefinitionNotFound as exc:
        raise HTTPException(404, detail=str(exc)) from exc
    except (stages.StageDefinitionConflict, stages.StageDefinitionInvalid) as exc:
        raise HTTPException(409, detail=str(exc)) from exc


@router.post("/stages/{definition_id}/reveal", status_code=204)
def reveal_stage_endpoint(
    definition_id: str,
    repository: RepositoryDep,
    locations: LocationsDep,
    root_path: str | None = None,
) -> None:
    try:
        path = stages.reveal_stage(
            locations,
            repository,
            stages.StageCatalogRequest(definition_id, root_path),
        )
        open_in_file_browser(str(path))
    except stages.StageDefinitionNotFound as exc:
        raise HTTPException(404, detail=str(exc)) from exc
    except (OSError, subprocess.SubprocessError) as exc:
        raise HTTPException(500, detail=f"reveal failed: {exc}") from exc


@router.delete("/stages/{definition_id}", status_code=204)
def delete_stage_endpoint(
    definition_id: str,
    repository: RepositoryDep,
    locations: LocationsDep,
    root_path: str | None = None,
) -> None:
    try:
        stages.delete_stage(
            locations,
            repository,
            stages.StageCatalogRequest(definition_id, root_path),
        )
    except stages.StageDefinitionNotFound as exc:
        raise HTTPException(404, detail=str(exc)) from exc
    except stages.StageDefinitionReadOnly as exc:
        raise HTTPException(422, detail=str(exc)) from exc


def _save(
    payload: SaveStageDefinitionRequest,
    repository: StageDefinitionRepository,
    locations: LoopDefinitionLocations,
) -> StageDefinitionResponse:
    try:
        stage = loop_stage_from_snapshot(
            payload.stage.model_dump(mode="json", exclude_none=True, by_alias=True)
        )
        definition = StageDefinition(
            definition_id=payload.id,
            name=payload.name,
            description=payload.description,
            scope=StageDefinitionScope.LIBRARY,
            forked_from=payload.forked_from,
            outcomes=tuple(payload.outcomes),
            stage=replace(
                stage,
                step_id=payload.id,
                name=payload.name,
                transitions={},
                stage_ref=None,
                overrides=None,
            ),
        )
        return _response(
            stages.save_stage(
                locations,
                repository,
                stages.SaveStageRequest(definition, payload.root_path, payload.expected_revision),
            )
        )
    except stages.StageDefinitionConflict as exc:
        raise HTTPException(409, detail=str(exc)) from exc
    except (
        stages.StageDefinitionInvalid,
        stages.StageDefinitionReadOnly,
        ValueError,
    ) as exc:
        raise HTTPException(422, detail=str(exc)) from exc


def _response(definition: StageDefinition) -> StageDefinitionResponse:
    return StageDefinitionResponse(
        id=definition.definition_id,
        name=definition.name,
        description=definition.description,
        scope=definition.scope.value,
        revision=definition.revision,
        valid=definition.valid,
        errors=list(definition.errors),
        forked_from=definition.forked_from,
        used_by=list(definition.used_by),
        outcomes=list(definition.outcomes),
        stage=LoopStepDefinitionSchema.model_validate(
            loop_stage_snapshot(definition.stage)
        ),
    )


__all__ = ["router"]
