"""HTTP request/response models.

Pydantic shapes the application layer exposes on the wire. Domain
entities cross the boundary as values; this module owns the
JSON-friendly representation. Path values flow as strings so neither
side has to do filesystem-existence validation.
"""

from datetime import datetime
from typing import Annotated, Any, Literal

from pydantic import BaseModel, Field

from src.domain.models import (
    AgentStatus,
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
    status: WorkStatus
    created_at: datetime
    # Absolute path to ``~/Atelier/works/<slug>/`` — where Atelier itself
    # writes work.json, brief.md, agents/<slug>/, handoffs/, etc. Useful
    # for the UI's "reveal in Finder" affordance and for power-users
    # peeking at the canonical filesystem state. Not the agent workdir
    # (that's per-agent and lives on the Agent entity).
    atelier_path: str


class WorkDetail(WorkSummary):
    contexts: list[ContextSchema]


class NewAgentRequest(BaseModel):
    name: str = Field(min_length=1)
    persona: Persona
    role: str
    provider: Provider
    model: str
    # Working directory the adapter spawns in. Per-agent so a single
    # Work can span multiple repos.
    folder: str = Field(min_length=1)
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
    folder: str
    status: AgentStatus
    started_at: datetime
    stopped_at: datetime | None = None


class DetachResponse(BaseModel):
    """Result of POSTing /agents/{slug}/detach."""

    command: str
    """The shell command that resumes the CLI session — surfaced in a
    toast on success, copied to clipboard on launch failure."""

    launched: bool
    """True if Atelier successfully spawned a terminal window. False
    when the FE should copy ``command`` to the clipboard instead."""


class JiraConfigSchema(BaseModel):
    type: Literal["jira"]
    url: str = Field(min_length=1)
    email: str = Field(min_length=1)


class SentryConfigSchema(BaseModel):
    type: Literal["sentry"]
    org: str = Field(min_length=1)


class HoneycombConfigSchema(BaseModel):
    type: Literal["honeycomb"]
    env: str = Field(min_length=1)
    team: str | None = None


# Discriminated union — Pydantic picks the right config shape based on
# the ``type`` literal, surfacing field-shape errors as 422 rather than
# silently accepting (or dropping) keys that don't apply.
ConnectionConfigSchema = Annotated[
    JiraConfigSchema | SentryConfigSchema | HoneycombConfigSchema,
    Field(discriminator="type"),
]


class NewConnectionRequest(BaseModel):
    name: str = Field(min_length=1)
    token: str = Field(min_length=1)
    config: ConnectionConfigSchema


class PatchConnectionRequest(BaseModel):
    """Partial update. Pass ``token`` to rotate the keychain entry; pass
    ``config`` to replace the typed config wholesale."""

    name: str | None = Field(default=None, min_length=1)
    token: str | None = Field(default=None, min_length=1)
    config: ConnectionConfigSchema | None = None


class ConnectionRead(BaseModel):
    """Response shape for connection metadata. **No token field exists**
    — the token never leaves the keychain over the API."""

    slug: str
    name: str
    created_at: datetime
    config: ConnectionConfigSchema
    verified: bool
    last_used: datetime | None = None


class VerifyResponse(BaseModel):
    verified: bool
    error: str | None = None


__all__ = [
    "AgentSummary",
    "ConnectionRead",
    "ContextSchema",
    "DetachResponse",
    "NewAgentRequest",
    "NewConnectionRequest",
    "NewWorkRequest",
    "PatchConnectionRequest",
    "PatchWorkRequest",
    "VerifyResponse",
    "WorkDetail",
    "WorkSummary",
]
