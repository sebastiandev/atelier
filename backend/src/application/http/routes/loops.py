"""HTTP routes for reusable Work-scoped loop definitions."""

from __future__ import annotations

import subprocess
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Request, status

from src.application.http.schemas import (
    ForkLoopDefinitionRequest,
    LoopDefinitionResponse,
    LoopStepDefinitionSchema,
    SaveLoopDefinitionRequest,
)
from src.domain.commands.loops import (
    delete_definition,
    fork_definition,
    get_definition,
    list_definitions,
    reveal_definition,
    save_definition,
)
from src.domain.loop.dtos import (
    LoopAgentPolicy,
    LoopContextReference,
    LoopDefinition,
    LoopDefinitionScope,
    LoopReportField,
    LoopReportSchema,
    LoopRetryPolicy,
    LoopStepDefinition,
)
from src.domain.loop.ports import LoopDefinitionLocations, LoopDefinitionRepository
from src.domain.planning.ports import PlanningSessionRepository
from src.domain.workstore.ports import WorkStore
from src.infrastructure.filesystem.reveal import open_in_file_browser

router = APIRouter(tags=["loops"])

_REPORT_SCHEMA = LoopReportSchema(
    schema_id="multi-stage-loop-report",
    fields=(LoopReportField("summary", "Summary", allow_explicit_none=False),),
)


def _workstore(request: Request) -> WorkStore:
    return request.app.state.workstore  # type: ignore[no-any-return]


def _planning_sessions(request: Request) -> PlanningSessionRepository:
    return request.app.state.planning_sessions  # type: ignore[no-any-return]


def _definitions(request: Request) -> LoopDefinitionRepository:
    return request.app.state.loop_definitions  # type: ignore[no-any-return]


def _locations(request: Request) -> LoopDefinitionLocations:
    return request.app.state.workspace_paths  # type: ignore[no-any-return]


WorkStoreDep = Annotated[WorkStore, Depends(_workstore)]
PlanningSessionsDep = Annotated[
    PlanningSessionRepository, Depends(_planning_sessions)
]
DefinitionsDep = Annotated[LoopDefinitionRepository, Depends(_definitions)]
LocationsDep = Annotated[LoopDefinitionLocations, Depends(_locations)]


@router.get("/loops", response_model=list[LoopDefinitionResponse])
def list_loops_endpoint(
    workstore: WorkStoreDep,
    planning_sessions: PlanningSessionsDep,
    locations: LocationsDep,
    definitions: DefinitionsDep,
    work_slug: str | None = None,
    root_path: str | None = None,
) -> list[LoopDefinitionResponse]:
    return _list(
        work_slug,
        root_path,
        workstore,
        planning_sessions,
        locations,
        definitions,
    )


@router.post(
    "/loops",
    response_model=LoopDefinitionResponse,
    status_code=status.HTTP_201_CREATED,
)
def create_loop_endpoint(
    payload: SaveLoopDefinitionRequest,
    workstore: WorkStoreDep,
    planning_sessions: PlanningSessionsDep,
    locations: LocationsDep,
    definitions: DefinitionsDep,
) -> LoopDefinitionResponse:
    if payload.expected_revision is not None:
        raise HTTPException(
            status.HTTP_422_UNPROCESSABLE_CONTENT,
            detail="POST creates loops and must not include expected_revision",
        )
    return _save(
        payload,
        workstore,
        planning_sessions,
        locations,
        definitions,
    )


@router.get("/loops/{definition_id}", response_model=LoopDefinitionResponse)
def get_loop_endpoint(
    definition_id: str,
    workstore: WorkStoreDep,
    planning_sessions: PlanningSessionsDep,
    locations: LocationsDep,
    definitions: DefinitionsDep,
    work_slug: str | None = None,
    root_path: str | None = None,
    scope: LoopDefinitionScope | None = None,
) -> LoopDefinitionResponse:
    return _get(
        definition_id,
        work_slug,
        root_path,
        scope,
        workstore,
        planning_sessions,
        locations,
        definitions,
    )


@router.put("/loops/{definition_id}", response_model=LoopDefinitionResponse)
@router.patch("/loops/{definition_id}", response_model=LoopDefinitionResponse)
def update_loop_endpoint(
    definition_id: str,
    payload: SaveLoopDefinitionRequest,
    workstore: WorkStoreDep,
    planning_sessions: PlanningSessionsDep,
    locations: LocationsDep,
    definitions: DefinitionsDep,
) -> LoopDefinitionResponse:
    _require_matching_id(payload.id, definition_id)
    if payload.expected_revision is None:
        raise HTTPException(
            status.HTTP_422_UNPROCESSABLE_CONTENT,
            detail="PUT/PATCH updates require expected_revision",
        )
    return _save(
        payload,
        workstore,
        planning_sessions,
        locations,
        definitions,
    )


@router.post(
    "/loops/{definition_id}/fork",
    response_model=LoopDefinitionResponse,
    status_code=status.HTTP_201_CREATED,
)
def fork_loop_endpoint(
    definition_id: str,
    payload: ForkLoopDefinitionRequest,
    workstore: WorkStoreDep,
    planning_sessions: PlanningSessionsDep,
    locations: LocationsDep,
    definitions: DefinitionsDep,
) -> LoopDefinitionResponse:
    return _fork(
        definition_id,
        payload,
        workstore,
        planning_sessions,
        locations,
        definitions,
    )


@router.post(
    "/loops/{definition_id}/reveal",
    status_code=status.HTTP_204_NO_CONTENT,
)
def reveal_loop_endpoint(
    definition_id: str,
    workstore: WorkStoreDep,
    planning_sessions: PlanningSessionsDep,
    locations: LocationsDep,
    definitions: DefinitionsDep,
    work_slug: str | None = None,
    root_path: str | None = None,
    scope: LoopDefinitionScope | None = None,
) -> None:
    _reveal(
        definition_id,
        work_slug,
        root_path,
        scope,
        workstore,
        planning_sessions,
        locations,
        definitions,
    )


@router.delete(
    "/loops/{definition_id}",
    status_code=status.HTTP_204_NO_CONTENT,
)
def delete_loop_endpoint(
    definition_id: str,
    workstore: WorkStoreDep,
    planning_sessions: PlanningSessionsDep,
    locations: LocationsDep,
    definitions: DefinitionsDep,
    work_slug: str | None = None,
    root_path: str | None = None,
    scope: LoopDefinitionScope | None = None,
) -> None:
    _delete(
        definition_id,
        work_slug,
        root_path,
        scope,
        workstore,
        planning_sessions,
        locations,
        definitions,
    )


@router.get(
    "/works/{work_slug}/loop-definitions",
    response_model=list[LoopDefinitionResponse],
)
def list_loop_definitions_endpoint(
    work_slug: str,
    workstore: WorkStoreDep,
    planning_sessions: PlanningSessionsDep,
    locations: LocationsDep,
    definitions: DefinitionsDep,
) -> list[LoopDefinitionResponse]:
    return _list(
        work_slug,
        None,
        workstore,
        planning_sessions,
        locations,
        definitions,
        legacy_only=True,
    )


@router.post(
    "/works/{work_slug}/loop-definitions",
    response_model=LoopDefinitionResponse,
    status_code=status.HTTP_201_CREATED,
)
def create_loop_definition_endpoint(
    work_slug: str,
    payload: SaveLoopDefinitionRequest,
    workstore: WorkStoreDep,
    planning_sessions: PlanningSessionsDep,
    locations: LocationsDep,
    definitions: DefinitionsDep,
) -> LoopDefinitionResponse:
    return _save(
        payload,
        workstore,
        planning_sessions,
        locations,
        definitions,
        work_slug=work_slug,
        legacy_only=True,
    )


@router.get(
    "/works/{work_slug}/loop-definitions/{definition_id}",
    response_model=LoopDefinitionResponse,
)
def get_loop_definition_endpoint(
    work_slug: str,
    definition_id: str,
    workstore: WorkStoreDep,
    planning_sessions: PlanningSessionsDep,
    locations: LocationsDep,
    definitions: DefinitionsDep,
) -> LoopDefinitionResponse:
    return _get(
        definition_id,
        work_slug,
        None,
        None,
        workstore,
        planning_sessions,
        locations,
        definitions,
        legacy_only=True,
    )


@router.post(
    "/works/{work_slug}/loop-definitions/{definition_id}/reveal",
    status_code=status.HTTP_204_NO_CONTENT,
)
def reveal_loop_definition_endpoint(
    work_slug: str,
    definition_id: str,
    workstore: WorkStoreDep,
    planning_sessions: PlanningSessionsDep,
    locations: LocationsDep,
    definitions: DefinitionsDep,
) -> None:
    _reveal(
        definition_id,
        work_slug,
        None,
        None,
        workstore,
        planning_sessions,
        locations,
        definitions,
        legacy_only=True,
    )


@router.put(
    "/works/{work_slug}/loop-definitions/{definition_id}",
    response_model=LoopDefinitionResponse,
)
def update_loop_definition_endpoint(
    work_slug: str,
    definition_id: str,
    payload: SaveLoopDefinitionRequest,
    workstore: WorkStoreDep,
    planning_sessions: PlanningSessionsDep,
    locations: LocationsDep,
    definitions: DefinitionsDep,
) -> LoopDefinitionResponse:
    _require_matching_id(payload.id, definition_id)
    return _save(
        payload,
        workstore,
        planning_sessions,
        locations,
        definitions,
        work_slug=work_slug,
        legacy_only=True,
    )


@router.post(
    "/works/{work_slug}/loop-definitions/{definition_id}/fork",
    response_model=LoopDefinitionResponse,
    status_code=status.HTTP_201_CREATED,
)
def fork_loop_definition_endpoint(
    work_slug: str,
    definition_id: str,
    payload: ForkLoopDefinitionRequest,
    workstore: WorkStoreDep,
    planning_sessions: PlanningSessionsDep,
    locations: LocationsDep,
    definitions: DefinitionsDep,
) -> LoopDefinitionResponse:
    return _fork(
        definition_id,
        payload,
        workstore,
        planning_sessions,
        locations,
        definitions,
        work_slug=work_slug,
        legacy_only=True,
    )


@router.delete(
    "/works/{work_slug}/loop-definitions/{definition_id}",
    status_code=status.HTTP_204_NO_CONTENT,
)
def delete_loop_definition_endpoint(
    work_slug: str,
    definition_id: str,
    workstore: WorkStoreDep,
    planning_sessions: PlanningSessionsDep,
    locations: LocationsDep,
    definitions: DefinitionsDep,
) -> None:
    _delete(
        definition_id,
        work_slug,
        None,
        None,
        workstore,
        planning_sessions,
        locations,
        definitions,
        legacy_only=True,
    )


def _list(
    work_slug: str | None,
    root_path: str | None,
    workstore: WorkStore,
    planning_sessions: PlanningSessionRepository,
    locations: LoopDefinitionLocations,
    definitions: LoopDefinitionRepository,
    *,
    legacy_only: bool = False,
) -> list[LoopDefinitionResponse]:
    try:
        rows = list_definitions.execute(
            workstore,
            planning_sessions,
            locations,
            definitions,
            list_definitions.ListLoopDefinitionsRequest(
                work_slug=work_slug,
                root_path=root_path,
                legacy_only=legacy_only,
            ),
        )
    except list_definitions.WorkNotFound as exc:
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail=str(exc)) from exc
    except list_definitions.LoopRootUnavailable as exc:
        raise HTTPException(status.HTTP_409_CONFLICT, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(
            status.HTTP_422_UNPROCESSABLE_CONTENT,
            detail=str(exc),
        ) from exc
    return [_to_response(row) for row in rows]


def _get(
    definition_id: str,
    work_slug: str | None,
    root_path: str | None,
    scope: LoopDefinitionScope | None,
    workstore: WorkStore,
    planning_sessions: PlanningSessionRepository,
    locations: LoopDefinitionLocations,
    definitions: LoopDefinitionRepository,
    *,
    legacy_only: bool = False,
) -> LoopDefinitionResponse:
    try:
        row = get_definition.execute(
            workstore,
            planning_sessions,
            locations,
            definitions,
            get_definition.GetLoopDefinitionRequest(
                definition_id=definition_id,
                work_slug=work_slug,
                root_path=root_path,
                scope=scope,
                legacy_only=legacy_only,
            ),
        )
    except (get_definition.WorkNotFound, get_definition.LoopDefinitionNotFound) as exc:
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail=str(exc)) from exc
    except get_definition.LoopRootUnavailable as exc:
        raise HTTPException(status.HTTP_409_CONFLICT, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(
            status.HTTP_422_UNPROCESSABLE_CONTENT,
            detail=str(exc),
        ) from exc
    return _to_response(row)


def _save(
    payload: SaveLoopDefinitionRequest,
    workstore: WorkStore,
    planning_sessions: PlanningSessionRepository,
    locations: LoopDefinitionLocations,
    definitions: LoopDefinitionRepository,
    *,
    work_slug: str | None = None,
    legacy_only: bool = False,
) -> LoopDefinitionResponse:
    try:
        row = save_definition.execute(
            workstore,
            planning_sessions,
            locations,
            definitions,
            save_definition.SaveLoopDefinitionRequest(
                definition=_to_domain(
                    payload,
                    scope=(
                        LoopDefinitionScope.REPOSITORY
                        if legacy_only
                        else payload.scope
                    ),
                ),
                work_slug=work_slug or payload.work_slug,
                root_path=payload.root_path,
                expected_revision=payload.expected_revision,
                legacy_only=legacy_only,
            ),
        )
    except save_definition.WorkNotFound as exc:
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail=str(exc)) from exc
    except save_definition.LoopDefinitionConflict as exc:
        raise HTTPException(status.HTTP_409_CONFLICT, detail=str(exc)) from exc
    except (
        save_definition.LoopDefinitionInvalid,
        save_definition.LoopDefinitionReadOnly,
        save_definition.LoopRootUnavailable,
        ValueError,
    ) as exc:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, detail=str(exc)) from exc
    return _to_response(row)


def _fork(
    definition_id: str,
    payload: ForkLoopDefinitionRequest,
    workstore: WorkStore,
    planning_sessions: PlanningSessionRepository,
    locations: LoopDefinitionLocations,
    definitions: LoopDefinitionRepository,
    *,
    work_slug: str | None = None,
    legacy_only: bool = False,
) -> LoopDefinitionResponse:
    try:
        row = fork_definition.execute(
            workstore,
            planning_sessions,
            locations,
            definitions,
            fork_definition.ForkLoopDefinitionRequest(
                source_id=definition_id,
                definition_id=payload.id,
                name=payload.name,
                target_scope=payload.scope,
                work_slug=work_slug or payload.work_slug,
                root_path=payload.root_path,
                legacy_only=legacy_only,
            ),
        )
    except (fork_definition.WorkNotFound, fork_definition.LoopDefinitionNotFound) as exc:
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail=str(exc)) from exc
    except fork_definition.LoopDefinitionConflict as exc:
        raise HTTPException(status.HTTP_409_CONFLICT, detail=str(exc)) from exc
    except (
        fork_definition.LoopDefinitionInvalid,
        fork_definition.LoopRootUnavailable,
        ValueError,
    ) as exc:
        raise HTTPException(
            status.HTTP_422_UNPROCESSABLE_CONTENT,
            detail=str(exc),
        ) from exc
    return _to_response(row)


def _delete(
    definition_id: str,
    work_slug: str | None,
    root_path: str | None,
    scope: LoopDefinitionScope | None,
    workstore: WorkStore,
    planning_sessions: PlanningSessionRepository,
    locations: LoopDefinitionLocations,
    definitions: LoopDefinitionRepository,
    *,
    legacy_only: bool = False,
) -> None:
    try:
        delete_definition.execute(
            workstore,
            planning_sessions,
            locations,
            definitions,
            delete_definition.DeleteLoopDefinitionRequest(
                definition_id=definition_id,
                work_slug=work_slug,
                root_path=root_path,
                scope=scope,
                legacy_only=legacy_only,
            ),
        )
    except (delete_definition.WorkNotFound, delete_definition.LoopDefinitionNotFound) as exc:
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail=str(exc)) from exc
    except delete_definition.LoopDefinitionReadOnly as exc:
        raise HTTPException(
            status.HTTP_422_UNPROCESSABLE_CONTENT,
            detail=str(exc),
        ) from exc
    except delete_definition.LoopRootUnavailable as exc:
        raise HTTPException(status.HTTP_409_CONFLICT, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(
            status.HTTP_422_UNPROCESSABLE_CONTENT,
            detail=str(exc),
        ) from exc


def _reveal(
    definition_id: str,
    work_slug: str | None,
    root_path: str | None,
    scope: LoopDefinitionScope | None,
    workstore: WorkStore,
    planning_sessions: PlanningSessionRepository,
    locations: LoopDefinitionLocations,
    definitions: LoopDefinitionRepository,
    *,
    legacy_only: bool = False,
) -> None:
    try:
        target = reveal_definition.execute(
            workstore,
            planning_sessions,
            locations,
            definitions,
            reveal_definition.RevealLoopDefinitionRequest(
                definition_id=definition_id,
                work_slug=work_slug,
                root_path=root_path,
                scope=scope,
                legacy_only=legacy_only,
            ),
        )
        open_in_file_browser(str(target))
    except (
        reveal_definition.WorkNotFound,
        reveal_definition.LoopDefinitionNotFound,
    ) as exc:
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail=str(exc)) from exc
    except reveal_definition.LoopRootUnavailable as exc:
        raise HTTPException(status.HTTP_409_CONFLICT, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(
            status.HTTP_422_UNPROCESSABLE_CONTENT,
            detail=str(exc),
        ) from exc
    except (OSError, subprocess.SubprocessError) as exc:
        raise HTTPException(
            status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"reveal failed: {exc}",
        ) from exc


def _require_matching_id(payload_id: str, definition_id: str) -> None:
    if payload_id != definition_id:
        raise HTTPException(
            status.HTTP_422_UNPROCESSABLE_CONTENT,
            detail="loop id must match the URL",
        )


def _to_domain(
    payload: SaveLoopDefinitionRequest,
    *,
    scope: LoopDefinitionScope,
) -> LoopDefinition:
    return LoopDefinition(
        definition_id=payload.id,
        name=payload.name,
        description=payload.description,
        trigger="artifact_or_objective",
        report_schema=_REPORT_SCHEMA,
        scope=scope,
        forked_from=payload.forked_from,
        stages=tuple(
            LoopStepDefinition(
                step_id=stage.id,
                name=stage.name,
                kind=stage.kind,
                instructions=stage.instructions,
                context=tuple(
                    LoopContextReference(
                        kind=context.kind,
                        required=context.required,
                        paths=tuple(context.paths),
                        step=context.step,
                        ref=context.ref,
                    )
                    for context in stage.context
                ),
                agent=(
                    LoopAgentPolicy(
                        session=stage.agent.session,
                        permissions=stage.agent.permissions,
                        provider=stage.agent.provider,
                        model=stage.agent.model,
                        effort=stage.agent.effort,
                    )
                    if stage.agent is not None
                    else None
                ),
                report_contract=stage.report_contract,
                retry=LoopRetryPolicy(
                    max_attempts=stage.retry.max_attempts,
                    timeout_minutes=stage.retry.timeout_minutes,
                ),
                transitions=dict(stage.transitions),
                check_adapter=stage.check_adapter,
                check_command=tuple(stage.check_command),
            )
            for stage in payload.stages
        ),
    )


def _to_response(definition: LoopDefinition) -> LoopDefinitionResponse:
    return LoopDefinitionResponse(
        id=definition.definition_id,
        name=definition.name,
        description=definition.description,
        scope=definition.scope,
        revision=definition.revision,
        valid=definition.valid,
        errors=list(definition.errors),
        is_default=definition.is_default,
        forked_from=definition.forked_from,
        stages=[
            LoopStepDefinitionSchema.model_validate({
                "id": stage.step_id,
                "name": stage.name,
                "kind": stage.kind,
                "instructions": stage.instructions,
                "context": [
                    {
                        "kind": context.kind,
                        "required": context.required,
                        "paths": list(context.paths),
                        "step": context.step,
                        "ref": context.ref,
                    }
                    for context in stage.context
                ],
                "agent": (
                    {
                        "session": stage.agent.session,
                        "permissions": stage.agent.permissions,
                        "provider": stage.agent.provider,
                        "model": stage.agent.model,
                        "effort": stage.agent.effort,
                    }
                    if stage.agent is not None
                    else None
                ),
                "report_contract": stage.report_contract,
                "retry": {
                    "max_attempts": stage.retry.max_attempts,
                    "timeout_minutes": stage.retry.timeout_minutes,
                },
                "transitions": stage.transitions,
                "check_adapter": stage.check_adapter,
                "check_command": list(stage.check_command),
            })
            for stage in definition.stages
        ],
    )


__all__ = ["router"]
