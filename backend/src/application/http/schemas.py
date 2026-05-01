"""HTTP request/response models.

Pydantic shapes the application layer exposes on the wire. Domain
entities cross the boundary as values; this module owns the
JSON-friendly representation. Path values flow as strings so neither
side has to do filesystem-existence validation.
"""

from datetime import datetime

from pydantic import BaseModel, Field

from src.domain.models import ContextType, WorkStatus


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


__all__ = [
    "ContextSchema",
    "NewWorkRequest",
    "PatchWorkRequest",
    "WorkDetail",
    "WorkSummary",
]
