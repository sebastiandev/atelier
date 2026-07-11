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
from src.domain.loop.ports import LoopDefinitionRepository
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


WorkStoreDep = Annotated[WorkStore, Depends(_workstore)]
PlanningSessionsDep = Annotated[
    PlanningSessionRepository, Depends(_planning_sessions)
]
DefinitionsDep = Annotated[LoopDefinitionRepository, Depends(_definitions)]


@router.get(
    "/works/{work_slug}/loop-definitions",
    response_model=list[LoopDefinitionResponse],
)
def list_loop_definitions_endpoint(
    work_slug: str,
    workstore: WorkStoreDep,
    planning_sessions: PlanningSessionsDep,
    definitions: DefinitionsDep,
) -> list[LoopDefinitionResponse]:
    try:
        rows = list_definitions.execute(
            workstore,
            planning_sessions,
            definitions,
            list_definitions.ListLoopDefinitionsRequest(work_slug),
        )
    except list_definitions.WorkNotFound as exc:
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail=str(exc)) from exc
    except list_definitions.LoopRootUnavailable as exc:
        raise HTTPException(status.HTTP_409_CONFLICT, detail=str(exc)) from exc
    return [_to_response(row) for row in rows]


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
    definitions: DefinitionsDep,
) -> LoopDefinitionResponse:
    return _save(work_slug, payload, workstore, planning_sessions, definitions)


@router.get(
    "/works/{work_slug}/loop-definitions/{definition_id}",
    response_model=LoopDefinitionResponse,
)
def get_loop_definition_endpoint(
    work_slug: str,
    definition_id: str,
    workstore: WorkStoreDep,
    planning_sessions: PlanningSessionsDep,
    definitions: DefinitionsDep,
) -> LoopDefinitionResponse:
    try:
        row = get_definition.execute(
            workstore,
            planning_sessions,
            definitions,
            get_definition.GetLoopDefinitionRequest(work_slug, definition_id),
        )
    except (get_definition.WorkNotFound, get_definition.LoopDefinitionNotFound) as exc:
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail=str(exc)) from exc
    except get_definition.LoopRootUnavailable as exc:
        raise HTTPException(status.HTTP_409_CONFLICT, detail=str(exc)) from exc
    return _to_response(row)


@router.post(
    "/works/{work_slug}/loop-definitions/{definition_id}/reveal",
    status_code=status.HTTP_204_NO_CONTENT,
)
def reveal_loop_definition_endpoint(
    work_slug: str,
    definition_id: str,
    workstore: WorkStoreDep,
    planning_sessions: PlanningSessionsDep,
    definitions: DefinitionsDep,
) -> None:
    try:
        target = reveal_definition.execute(
            workstore,
            planning_sessions,
            definitions,
            reveal_definition.RevealLoopDefinitionRequest(
                work_slug=work_slug,
                definition_id=definition_id,
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
    except (OSError, subprocess.SubprocessError) as exc:
        raise HTTPException(
            status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"reveal failed: {exc}",
        ) from exc


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
    definitions: DefinitionsDep,
) -> LoopDefinitionResponse:
    if payload.id != definition_id:
        raise HTTPException(
            status.HTTP_422_UNPROCESSABLE_CONTENT,
            detail="loop id must match the URL",
        )
    return _save(work_slug, payload, workstore, planning_sessions, definitions)


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
    definitions: DefinitionsDep,
) -> LoopDefinitionResponse:
    try:
        row = fork_definition.execute(
            workstore,
            planning_sessions,
            definitions,
            fork_definition.ForkLoopDefinitionRequest(
                work_slug=work_slug,
                source_id=definition_id,
                definition_id=payload.id,
                name=payload.name,
            ),
        )
    except (fork_definition.WorkNotFound, fork_definition.LoopDefinitionNotFound) as exc:
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail=str(exc)) from exc
    except fork_definition.LoopDefinitionConflict as exc:
        raise HTTPException(status.HTTP_409_CONFLICT, detail=str(exc)) from exc
    except (
        fork_definition.LoopDefinitionInvalid,
        fork_definition.LoopRootUnavailable,
    ) as exc:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, detail=str(exc)) from exc
    return _to_response(row)


@router.delete(
    "/works/{work_slug}/loop-definitions/{definition_id}",
    status_code=status.HTTP_204_NO_CONTENT,
)
def delete_loop_definition_endpoint(
    work_slug: str,
    definition_id: str,
    workstore: WorkStoreDep,
    planning_sessions: PlanningSessionsDep,
    definitions: DefinitionsDep,
) -> None:
    try:
        delete_definition.execute(
            workstore,
            planning_sessions,
            definitions,
            delete_definition.DeleteLoopDefinitionRequest(
                work_slug, definition_id
            ),
        )
    except (delete_definition.WorkNotFound, delete_definition.LoopDefinitionNotFound) as exc:
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail=str(exc)) from exc
    except delete_definition.LoopDefinitionReadOnly as exc:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, detail=str(exc)) from exc
    except delete_definition.LoopRootUnavailable as exc:
        raise HTTPException(status.HTTP_409_CONFLICT, detail=str(exc)) from exc


def _save(
    work_slug: str,
    payload: SaveLoopDefinitionRequest,
    workstore: WorkStore,
    planning_sessions: PlanningSessionRepository,
    definitions: LoopDefinitionRepository,
) -> LoopDefinitionResponse:
    try:
        row = save_definition.execute(
            workstore,
            planning_sessions,
            definitions,
            save_definition.SaveLoopDefinitionRequest(
                work_slug=work_slug,
                definition=_to_domain(payload),
                expected_revision=payload.expected_revision,
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


def _to_domain(payload: SaveLoopDefinitionRequest) -> LoopDefinition:
    return LoopDefinition(
        definition_id=payload.id,
        name=payload.name,
        description=payload.description,
        trigger="artifact_or_objective",
        report_schema=_REPORT_SCHEMA,
        scope=LoopDefinitionScope.REPOSITORY,
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
