"""HTTP request/response models.

Pydantic shapes the application layer exposes on the wire. Domain
entities cross the boundary as values; this module owns the
JSON-friendly representation. Path values flow as strings so neither
side has to do filesystem-existence validation.
"""

from datetime import datetime
from typing import Any

from pydantic import BaseModel, Field

from src.domain.models import (
    AgentStatus,
    ConnectionType,
    ContextType,
    Persona,
    Provider,
    WorkStatus,
)


class ContextSchema(BaseModel):
    type: ContextType
    value: str
    conn_id: str | None = None


class NewWorkRequest(BaseModel):
    name: str = Field(min_length=1)
    description: str
    folder: str
    contexts: list[ContextSchema] = Field(default_factory=list)


class PatchWorkRequest(BaseModel):
    name: str | None = Field(default=None, min_length=1)
    description: str | None = None
    status: WorkStatus | None = None
    contexts: list[ContextSchema] | None = None


class WorkSummary(BaseModel):
    slug: str
    name: str
    description: str
    folder: str
    status: WorkStatus
    created_at: datetime


class WorkDetail(WorkSummary):
    contexts: list[ContextSchema]


class NewAgentRequest(BaseModel):
    name: str = Field(min_length=1)
    persona: Persona
    role: str
    provider: Provider
    model: str
    # Provider-specific knobs (e.g. Claude's thinking_effort). The Spec
    # for ``provider`` validates the contents; unknown keys are rejected.
    options: dict[str, Any] = Field(default_factory=dict)
    contexts: list[ContextSchema] = Field(default_factory=list)


class AgentSummary(BaseModel):
    slug: str
    work_slug: str
    name: str
    persona: Persona
    role: str
    provider: Provider
    model: str
    status: AgentStatus
    started_at: datetime
    stopped_at: datetime | None = None


class NewConnectionRequest(BaseModel):
    type: ConnectionType
    name: str = Field(min_length=1)
    token: str = Field(min_length=1)
    url: str | None = None
    org: str | None = None
    region: str | None = None
    env: str | None = None
    team: str | None = None
    email: str | None = None


class PatchConnectionRequest(BaseModel):
    """Partial update — every field is optional. Pass ``token`` to rotate
    the keychain entry."""

    name: str | None = Field(default=None, min_length=1)
    token: str | None = Field(default=None, min_length=1)
    url: str | None = None
    org: str | None = None
    region: str | None = None
    env: str | None = None
    team: str | None = None
    email: str | None = None


class ConnectionRead(BaseModel):
    """Response shape for connection metadata. **No token field exists**
    — the token never leaves the keychain over the API."""

    slug: str
    type: ConnectionType
    name: str
    created_at: datetime
    url: str | None = None
    org: str | None = None
    region: str | None = None
    env: str | None = None
    team: str | None = None
    email: str | None = None
    verified: bool
    last_used: datetime | None = None


class VerifyResponse(BaseModel):
    verified: bool
    error: str | None = None


__all__ = [
    "AgentSummary",
    "ConnectionRead",
    "ContextSchema",
    "NewAgentRequest",
    "NewConnectionRequest",
    "NewWorkRequest",
    "PatchConnectionRequest",
    "PatchWorkRequest",
    "VerifyResponse",
    "WorkDetail",
    "WorkSummary",
]
