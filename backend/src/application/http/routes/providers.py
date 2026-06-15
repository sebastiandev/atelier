"""GET /api/providers — descriptors for providers available to new sessions.

The frontend's new-agent dialog reads this to render the right form
fields per provider (primary selector + extra option enums). Both the
descriptor and the request validator are produced by the same ``Spec``
object, so the wire format and the server's validation cannot drift.
Legacy providers can stay registered for existing agents without being
offered for newly created agents/chats.
"""

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from src.domain.agents import NEW_SESSION_PROVIDERS, SPECS, ProviderDescriptor
from src.infrastructure.agents.opencode_models import list_opencode_models

router = APIRouter()


class OpenCodeModelResponse(BaseModel):
    value: str
    label: str


@router.get("/providers", response_model=list[ProviderDescriptor])
def list_providers() -> list[ProviderDescriptor]:
    return [SPECS[name].describe() for name in NEW_SESSION_PROVIDERS]


@router.get("/providers/opencode/models", response_model=list[OpenCodeModelResponse])
def list_opencode_provider_models(refresh: bool = False) -> list[OpenCodeModelResponse]:
    try:
        models = list_opencode_models(refresh=refresh)
    except RuntimeError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc
    return [
        OpenCodeModelResponse(value=item.value, label=item.label)
        for item in models
    ]
