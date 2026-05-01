"""HTTP request/response models.

Pydantic shapes the application layer exposes on the wire. Domain
entities cross the boundary as values; this module owns the
JSON-friendly representation. Path values flow as strings so neither
side has to do filesystem-existence validation.
"""

from datetime import datetime

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


__all__ = [
    "AgentSummary",
    "ContextSchema",
    "NewAgentRequest",
    "NewWorkRequest",
    "PatchWorkRequest",
    "WorkDetail",
    "WorkSummary",
]
