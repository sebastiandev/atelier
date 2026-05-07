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
    # Optional. Omit for "loose work". Validated as an existing project at
    # the route layer — the FK enforces it again at insert time.
    project_slug: str | None = None


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
    # Optional grouping link. ``null`` is "loose work" — first-class, not
    # a hidden bucket. Frontend resolves slug → name/glyph/color via the
    # /api/projects payload.
    project_slug: str | None = None
    # Aggregated child counts. Populated by ``list_works`` for the workspace
    # cards; default 0 for endpoints that don't have a ready-made counts
    # dict (e.g. ``create_work`` returning the freshly-created shell, where
    # both are still 0 anyway).
    agent_count: int = 0
    artifact_count: int = 0


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
    # The directory the adapter actually runs in. For git source folders
    # this is ``<workspace>/works/<work>/worktrees/<agent>/`` once
    # provisioned by the WorktreeManager; for non-git sources (or before
    # provisioning) it falls back to ``folder``. Surfaced on the agent
    # tile so the user can reveal it in their file browser.
    worktree_path: str


class DetachResponse(BaseModel):
    """Result of POSTing /agents/{slug}/detach."""

    command: str
    """The shell command that resumes the CLI session — surfaced in a
    toast on success, copied to clipboard on launch failure."""

    launched: bool
    """True if Atelier successfully spawned a terminal window. False
    when the FE should copy ``command`` to the clipboard instead."""


class CompleteWorkResponse(BaseModel):
    """Result of POSTing /works/{slug}/complete."""

    work_slug: str

    agent_count: int
    """How many agents were on the work. All had their supervisor task
    stopped and worktree removed (both idempotent — actual side effects
    depend on prior state). The FE uses this for the success toast."""


class MoveWorkRequest(BaseModel):
    """Body for POST /works/{slug}/project — re-parent a work."""

    project_slug: str | None = None
    """``null`` moves the work to Loose (no project). A non-null slug
    must reference an existing project (422 otherwise)."""


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


# ---------------------------------------------------------------------------
# Projects
# ---------------------------------------------------------------------------


class NewProjectRequest(BaseModel):
    name: str = Field(min_length=1)
    description: str = ""
    # 1–2 char monogram. FE derives it from name; required on the wire so
    # the backend doesn't have to know the FE's derivation rules.
    glyph: str = Field(min_length=1, max_length=2)
    # OKLCH hue 0–360 (inclusive lower, exclusive upper); enforced wider
    # than the prototype's 7-swatch palette so future palette tweaks don't
    # need a schema bump.
    color: int = Field(ge=0, le=360)
    pinned: bool = False
    default_jira_conn: str | None = None
    default_sentry_conn: str | None = None


class PatchProjectRequest(BaseModel):
    name: str | None = Field(default=None, min_length=1)
    description: str | None = None
    glyph: str | None = Field(default=None, min_length=1, max_length=2)
    color: int | None = Field(default=None, ge=0, le=360)
    pinned: bool | None = None
    default_jira_conn: str | None = None
    default_sentry_conn: str | None = None


class ProjectSummary(BaseModel):
    slug: str
    name: str
    description: str
    glyph: str
    color: int
    pinned: bool
    default_jira_conn: str | None = None
    default_sentry_conn: str | None = None
    created_at: datetime


class ProjectDetail(ProjectSummary):
    """Reserved for future fields specific to the detail view (counts of
    active/completed work, recent items, etc.). Today it equals Summary.
    """


__all__ = [
    "AgentSummary",
    "ConnectionRead",
    "ContextSchema",
    "DetachResponse",
    "NewAgentRequest",
    "NewConnectionRequest",
    "NewProjectRequest",
    "NewWorkRequest",
    "PatchConnectionRequest",
    "PatchProjectRequest",
    "PatchWorkRequest",
    "ProjectDetail",
    "ProjectSummary",
    "VerifyResponse",
    "WorkDetail",
    "WorkSummary",
]
