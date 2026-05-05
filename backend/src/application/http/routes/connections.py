"""Connections REST router.

Thin endpoints — parse pydantic, build domain DTO, hand off to a command,
format the result. Tokens enter via NewConnectionRequest /
PatchConnectionRequest only and never come back out: ConnectionRead has
no ``token`` field at all.
"""

from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Request, status

from src.application.http.schemas import (
    ConnectionConfigSchema,
    ConnectionRead,
    HoneycombConfigSchema,
    JiraConfigSchema,
    NewConnectionRequest,
    PatchConnectionRequest,
    SentryConfigSchema,
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
    DESCRIPTORS,
    ConnectionConfig,
    ConnectionDescriptor,
    ConnectionStore,
    CreateConnectionRequest,
    HoneycombConfig,
    JiraConfig,
    SentryConfig,
    UpdateConnectionRequest,
    VerifyResult,
)
from src.domain.models import Connection

router = APIRouter()


def get_connection_store(request: Request) -> ConnectionStore:
    return request.app.state.connection_store  # type: ignore[no-any-return]


ConnectionStoreDep = Annotated[ConnectionStore, Depends(get_connection_store)]


@router.get("/connections/types", response_model=list[ConnectionDescriptor])
def list_connection_types_endpoint() -> list[ConnectionDescriptor]:
    """The set of source types the FE can render forms for.

    Includes per-type form fields, doc URL, glyph, and capability flags
    (verifiable, context_fetchable). The FE uses ``context_fetchable``
    to filter the agent-context picker — picking a non-fetchable type
    would 422 at agent creation."""
    return list(DESCRIPTORS.values())


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
        type=payload.config.type,
        name=payload.name,
        token=payload.token,
        config=_schema_to_config(payload.config),
    )


def _to_update_request(slug: str, payload: PatchConnectionRequest) -> UpdateConnectionRequest:
    return UpdateConnectionRequest(
        slug=slug,
        name=payload.name,
        token=payload.token,
        config=_schema_to_config(payload.config) if payload.config else None,
    )


def _schema_to_config(schema: ConnectionConfigSchema) -> ConnectionConfig:
    if isinstance(schema, JiraConfigSchema):
        return JiraConfig(url=schema.url, email=schema.email)
    if isinstance(schema, SentryConfigSchema):
        return SentryConfig(org=schema.org)
    return HoneycombConfig(env=schema.env, team=schema.team)


def _config_to_schema(config: ConnectionConfig) -> ConnectionConfigSchema:
    if isinstance(config, JiraConfig):
        return JiraConfigSchema(type="jira", url=config.url, email=config.email)
    if isinstance(config, SentryConfig):
        return SentryConfigSchema(type="sentry", org=config.org)
    return HoneycombConfigSchema(type="honeycomb", env=config.env, team=config.team)


def _to_read(connection: Connection) -> ConnectionRead:
    if connection.slug is None:
        raise RuntimeError("persisted Connection has no slug")
    assert isinstance(connection.config, JiraConfig | SentryConfig | HoneycombConfig)
    return ConnectionRead(
        slug=connection.slug,
        name=connection.name,
        created_at=connection.created_at,
        config=_config_to_schema(connection.config),
        verified=connection.verified,
        last_used=connection.last_used,
    )


def _to_verify_response(result: VerifyResult) -> VerifyResponse:
    return VerifyResponse(verified=result.verified, error=result.error)
