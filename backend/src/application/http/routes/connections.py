"""Connections REST router.

Thin endpoints — parse pydantic, build domain DTO, hand off to a command,
format the result. Tokens enter via NewConnectionRequest /
PatchConnectionRequest only and never come back out: ConnectionRead has
no ``token`` field at all.
"""

from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Request, status

from src.application.http.schemas import (
    ConnectionRead,
    NewConnectionRequest,
    PatchConnectionRequest,
    VerifyResponse,
)
from src.domain.commands.connections import (
    create as cmd_create,
)
from src.domain.commands.connections import (
    delete as cmd_delete,
)
from src.domain.commands.connections import (
    get as cmd_get,
)
from src.domain.commands.connections import (
    list_all as cmd_list_all,
)
from src.domain.commands.connections import (
    update as cmd_update,
)
from src.domain.commands.connections import (
    verify as cmd_verify,
)
from src.domain.connections import (
    ConnectionStore,
    CreateConnectionRequest,
    UpdateConnectionRequest,
    VerifyResult,
)
from src.domain.models import Connection

router = APIRouter()


def get_connection_store(request: Request) -> ConnectionStore:
    return request.app.state.connection_store  # type: ignore[no-any-return]


ConnectionStoreDep = Annotated[ConnectionStore, Depends(get_connection_store)]


@router.get("/connections", response_model=list[ConnectionRead])
def list_connections_endpoint(store: ConnectionStoreDep) -> list[ConnectionRead]:
    return [_to_read(c) for c in cmd_list_all.execute(store)]


@router.post(
    "/connections",
    response_model=ConnectionRead,
    status_code=status.HTTP_201_CREATED,
)
def create_connection_endpoint(
    payload: NewConnectionRequest, store: ConnectionStoreDep
) -> ConnectionRead:
    connection = cmd_create.execute(store, _to_create_request(payload))
    return _to_read(connection)


@router.get("/connections/{slug}", response_model=ConnectionRead)
def get_connection_endpoint(slug: str, store: ConnectionStoreDep) -> ConnectionRead:
    connection = cmd_get.execute(store, slug)
    if connection is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail=f"connection not found: {slug}")
    return _to_read(connection)


@router.patch("/connections/{slug}", response_model=ConnectionRead)
def patch_connection_endpoint(
    slug: str, payload: PatchConnectionRequest, store: ConnectionStoreDep
) -> ConnectionRead:
    try:
        connection = cmd_update.execute(store, _to_update_request(slug, payload))
    except ValueError as e:
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail=str(e)) from e
    return _to_read(connection)


@router.delete("/connections/{slug}", status_code=status.HTTP_204_NO_CONTENT)
def delete_connection_endpoint(slug: str, store: ConnectionStoreDep) -> None:
    cmd_delete.execute(store, slug)


@router.post("/connections/{slug}/verify", response_model=VerifyResponse)
def verify_connection_endpoint(slug: str, store: ConnectionStoreDep) -> VerifyResponse:
    try:
        result = cmd_verify.execute(store, slug)
    except ValueError as e:
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail=str(e)) from e
    return _to_verify_response(result)


# ---------------------------------------------------------------------------
# Translators
# ---------------------------------------------------------------------------


def _to_create_request(payload: NewConnectionRequest) -> CreateConnectionRequest:
    return CreateConnectionRequest(
        type=payload.type,
        name=payload.name,
        token=payload.token,
        url=payload.url,
        org=payload.org,
        region=payload.region,
        env=payload.env,
        team=payload.team,
        email=payload.email,
    )


def _to_update_request(slug: str, payload: PatchConnectionRequest) -> UpdateConnectionRequest:
    return UpdateConnectionRequest(
        slug=slug,
        name=payload.name,
        token=payload.token,
        url=payload.url,
        org=payload.org,
        region=payload.region,
        env=payload.env,
        team=payload.team,
        email=payload.email,
    )


def _to_read(connection: Connection) -> ConnectionRead:
    if connection.slug is None:
        raise RuntimeError("persisted Connection has no slug")
    return ConnectionRead(
        slug=connection.slug,
        type=connection.type,
        name=connection.name,
        created_at=connection.created_at,
        url=connection.url,
        org=connection.org,
        region=connection.region,
        env=connection.env,
        team=connection.team,
        email=connection.email,
        verified=connection.verified,
        last_used=connection.last_used,
    )


def _to_verify_response(result: VerifyResult) -> VerifyResponse:
    return VerifyResponse(verified=result.verified, error=result.error)
