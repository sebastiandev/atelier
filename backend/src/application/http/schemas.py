"""HTTP request/response models.

Pydantic shapes the application layer exposes on the wire. Domain
entities cross the boundary as values; this module owns the
JSON-friendly representation. Path values flow as strings so neither
side has to do filesystem-existence validation.
"""

from datetime import datetime
from typing import Annotated, Any, Literal

from pydantic import BaseModel, Field

from src.domain.loop.dtos import (
    LoopContextKind,
    LoopDefinitionScope,
    LoopFindingSeverity,
    LoopOutcome,
    LoopPermission,
    LoopSessionPolicy,
    LoopStatus,
    LoopStepKind,
    LoopStepStatus,
)
from src.domain.models import (
    AgentStatus,
    ArtifactType,
    ChatGroundingKind,
    ChatMessageRole,
    ContextType,
    Persona,
    Provider,
    WorkStatus,
)
from src.domain.planning.dtos import (
    PlanArtifactKind,
    PlanArtifactStatus,
    PlanningDepth,
    PlanningFramework,
    PlanningPhase,
    PlanningProfile,
    PlanProposalStatus,
    PlanReadiness,
    PlanRunStatus,
    PlanTrackingKind,
)


class ContextSchema(BaseModel):
    type: ContextType
    value: str
    conn_id: str | None = None


class WorkChatRef(BaseModel):
    slug: str
    title: str


class NewWorkChatContextFolder(BaseModel):
    name: str = Field(min_length=1)
    mount_path: str = Field(min_length=1)
    chat_slug: str = Field(min_length=1)
    chat_title: str = Field(min_length=1)
    context_markdown: str = Field(min_length=1)
    context_filename: str = "context.md"


class WorkChatContextFolderSummary(BaseModel):
    name: str
    mount_path: str
    chat_slug: str
    chat_title: str
    context_filename: str
    absolute_path: str


class NewWorkRequest(BaseModel):
    name: str = Field(min_length=1)
    description: str
    contexts: list[ContextSchema] = Field(default_factory=list)
    # Optional. Omit for "loose work". Validated as an existing project at
    # the route layer — the FK enforces it again at insert time.
    project_slug: str | None = None
    from_chat: WorkChatRef | None = None
    chat_context_folders: list[NewWorkChatContextFolder] = Field(
        default_factory=list
    )


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
    from_chat: WorkChatRef | None = None


class WorkDetail(WorkSummary):
    contexts: list[ContextSchema]
    chat_context_folders: list[WorkChatContextFolderSummary] = Field(
        default_factory=list
    )


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
    # When set, fork the worktree from this existing agent in the same
    # work — new agent inherits source's uncommitted state in detached
    # HEAD. Used by the handoff flow.
    fork_from_agent: str | None = None
    # Optional branch name to create on the new worktree. ``None``
    # (default) leaves the worktree in detached HEAD; the agent decides
    # the branch name later via ``git switch -c``. Ignored when
    # ``fork_from_agent`` is set.
    branch_name: str | None = None


class PatchAgentRequest(BaseModel):
    """Partial update for an agent's mutable display fields. Today only
    ``name`` is mutable; the rest of the agent shape is FS-canonical and
    only set at create time."""

    name: str | None = Field(default=None, min_length=1)


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


class SwitchThreadRequest(BaseModel):
    """Body for POST /agents/{slug}/switch-thread — swap the agent's
    underlying provider thread. Currently only meaningful for Amp."""

    thread_id: str = Field(min_length=1)


class CompactAgentRequest(BaseModel):
    """Body for POST /agents/{slug}/compact."""

    reason: Literal["manual", "forced_context_limit"] = "manual"


class CompactChatRequest(BaseModel):
    """Body for POST /chats/{slug}/compact."""

    reason: Literal["manual", "forced_context_limit"] = "manual"


class CompactAgentResponse(BaseModel):
    agent_slug: str
    work_slug: str
    provider: Provider
    old_session_id: str
    new_session_id: str
    summary_path: str
    breadcrumb_written: bool
    breadcrumb_error: str | None = None


class AgentCompactionSummaryResponse(BaseModel):
    agent_slug: str
    work_slug: str
    filename: str
    summary_path: str
    content: str


class CompactChatResponse(BaseModel):
    chat_slug: str
    provider: Provider
    old_session_id: str
    new_session_id: str
    summary_path: str
    breadcrumb_written: bool
    breadcrumb_error: str | None = None


class ChatCompactionSummaryResponse(BaseModel):
    chat_slug: str
    filename: str
    summary_path: str
    content: str


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
    # 1-2 char monogram. FE derives it from name; required on the wire so
    # the backend doesn't have to know the FE's derivation rules.
    glyph: str = Field(min_length=1, max_length=2)
    # OKLCH hue 0-360 (inclusive lower, exclusive upper); enforced wider
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


class ArtifactSummary(BaseModel):
    """Artifact row, projected for the rail."""

    slug: str
    type: ArtifactType
    title: str
    status: str
    created_at: datetime
    agent_slug: str | None = None
    url: str | None = None
    repo: str | None = None
    doc_path: str | None = None
    # Doc-only enrichment, derived at list time. ``None`` for PR / Jira
    # artifacts and for doc artifacts whose path no longer resolves
    # under any known root (stale rows after a worktree wipe). The git-
    # state-vs-committed distinction now lives in ``status`` itself:
    # ``pending`` and ``committed`` are doc-only status values derived
    # from the file's relationship to its repo HEAD.
    location_kind: str | None = None


class NewHandoffRequest(BaseModel):
    """Caller picks the source agent; target is fixed to "new-agent" for v1."""

    source_agent_slug: str = Field(min_length=1)


class HandoffSummary(BaseModel):
    """Persisted Handoff row + the doc body so the FE can pre-fill the
    NewAgentDialog without a follow-up fetch."""

    slug: str
    source_agent_slug: str
    doc_path: str
    doc_text: str
    created_at: datetime
    target_agent_slug: str | None = None
    target_dialog: Literal["new-agent"] | None = None


# ---------------------------------------------------------------------------
# Chats
# ---------------------------------------------------------------------------


class ChatGroundingSchema(BaseModel):
    kind: ChatGroundingKind
    ref: str = Field(min_length=1)


class ChatMessageSchema(BaseModel):
    role: ChatMessageRole
    body: str
    created_at: datetime


class PlanningChatReadinessResponse(BaseModel):
    ready: bool
    summary: str


class ChatSummary(BaseModel):
    slug: str
    title: str
    provider: Provider
    model: str
    options: dict[str, Any] = Field(default_factory=dict)
    grounding: ChatGroundingSchema | None = None
    working_directory: str | None = None
    created_at: datetime
    updated_at: datetime
    promoted_to_work_slug: str | None = None
    message_count: int
    planning_readiness: PlanningChatReadinessResponse | None = None


class ChatDetail(ChatSummary):
    transcript: list[ChatMessageSchema]


class NewChatRequest(BaseModel):
    provider: Provider
    model: str = Field(min_length=1)
    first_message: str = Field(min_length=1)
    title: str | None = None
    grounding: ChatGroundingSchema | None = None
    working_directory: str | None = None
    # Provider-specific knobs for chats. Today the frontend exposes only
    # permission/mode, but this stays generic to match the agent surface.
    options: dict[str, Any] = Field(default_factory=dict)


class PatchChatRequest(BaseModel):
    title: str | None = Field(default=None, min_length=1)


class SendChatMessageRequest(BaseModel):
    body: str = Field(min_length=1)


class PromoteChatRequest(BaseModel):
    name: str = Field(min_length=1)
    description: str
    project_slug: str | None = None


class WorkChatContextDocResponse(BaseModel):
    path: str
    content: str


# ---------------------------------------------------------------------------
# Work planning
# ---------------------------------------------------------------------------


class PlanningFrameworkStatusRequest(BaseModel):
    root_path: str = Field(min_length=1)
    framework: PlanningFramework


class PlanningFrameworkStatusResponse(BaseModel):
    framework: PlanningFramework
    label: str
    root_path: str
    ready: bool
    markers: list[str]
    setup_command: list[str]
    setup_hint: str


class StartPlanningChatRequest(BaseModel):
    root_path: str = Field(min_length=1)
    idea: str = ""
    artifact_root_path: str | None = None
    framework: PlanningFramework
    profile: PlanningProfile
    provider: Provider
    model: str = Field(min_length=1)
    options: dict[str, Any] = Field(default_factory=dict)


class StartPlanningSetupChatRequest(BaseModel):
    root_path: str = Field(min_length=1)
    framework: PlanningFramework
    profile: PlanningProfile
    provider: Provider
    model: str = Field(min_length=1)
    options: dict[str, Any] = Field(default_factory=dict)


class StartWorkPlanRequest(BaseModel):
    root_path: str | None = None
    artifact_root_path: str | None = None
    framework: PlanningFramework | None = None
    profile: PlanningProfile | None = None
    provider: Provider | None = None
    model: str | None = None
    options: dict[str, Any] = Field(default_factory=dict)
    planning_chat_slug: str | None = None


class PlanMaterializationStatusResponse(BaseModel):
    state: Literal[
        "idle",
        "running",
        "waiting_permission",
        "stalled",
        "complete",
        "failed",
    ]
    chat_slug: str | None = None
    updated_at: str | None = None
    last_seq: int | None = None
    last_event_type: str | None = None
    last_event_summary: str = ""
    message: str = ""
    tool_name: str | None = None


class PlanOverviewResponse(BaseModel):
    total: int
    ready: int
    needs_detail: int
    approved: int
    changed: int
    accepted: int
    executable: int
    running: int
    review: int
    blocked: int


class PlanLoopFindingResponse(BaseModel):
    text: str
    severity: LoopFindingSeverity
    location: str = ""


class PlanLoopCriterionResponse(BaseModel):
    text: str
    met: bool
    note: str = ""


class PlanLoopChangedFileResponse(BaseModel):
    path: str
    additions: int = 0
    deletions: int = 0


class PlanLoopStageRunResponse(BaseModel):
    id: str
    name: str
    kind: LoopStepKind
    status: LoopStepStatus
    attempt: int = 0
    max_attempts: int = 1
    agent_slug: str | None = None
    permissions: LoopPermission | None = None
    session: LoopSessionPolicy | None = None
    summary: str = ""
    findings: list[str] = Field(default_factory=list)
    changes: str = ""
    validation_evidence: str = ""
    divergences: str = ""
    skipped_scope: str = ""
    blocker: str = ""
    artifact_refs: list[str] = Field(default_factory=list)
    finding_details: list[PlanLoopFindingResponse] = Field(default_factory=list)
    criteria_coverage: list[PlanLoopCriterionResponse] = Field(default_factory=list)
    changed_files: list[PlanLoopChangedFileResponse] = Field(default_factory=list)
    resolved_context: list[str] = Field(default_factory=list)
    context_warnings: list[str] = Field(default_factory=list)


class PlanArtifactRunResponse(BaseModel):
    id: str
    agent_slug: str
    status: PlanRunStatus
    started_at: str
    completed_at: str | None = None
    cleanup_at: str | None = None
    report_path: str | None = None
    summary: str = ""
    divergences: str = ""
    skipped_scope: str = ""
    blockers: str = ""
    decisions: str = ""
    changes: str = ""
    validation_evidence: str = ""
    loop_status: LoopStatus | None = None
    loop_status_reason: str = ""
    loop_attempt: int = 1
    loop_latest_assessment: list[str] = Field(default_factory=list)
    loop_definition_id: str = ""
    loop_definition_name: str = ""
    loop_definition_revision: str = ""
    loop_current_stage_id: str = ""
    loop_stages: list[PlanLoopStageRunResponse] = Field(default_factory=list)


class PlanArtifactProposalResponse(BaseModel):
    id: str
    artifact_id: str
    title: str
    path: str
    source_hash: str
    proposed_content: str
    status: PlanProposalStatus
    created_at: str
    resolved_at: str | None = None


class PlanTrackingLinkResponse(BaseModel):
    id: str
    kind: PlanTrackingKind
    title: str
    url: str = ""
    status: str = ""
    ref: str = ""
    notes: str = ""
    created_at: str


class PlanArtifactResponse(BaseModel):
    id: str
    kind: PlanArtifactKind
    title: str
    path: str
    source_ref: str
    source_hash: str
    status: PlanArtifactStatus
    readiness: PlanReadiness
    executable: bool
    launchable: bool
    launch_blockers: list[str] = Field(default_factory=list)
    dependencies: list[str] = Field(default_factory=list)
    runs: list[PlanArtifactRunResponse] = Field(default_factory=list)
    proposals: list[PlanArtifactProposalResponse] = Field(default_factory=list)
    tracking: list[PlanTrackingLinkResponse] = Field(default_factory=list)
    accepted_summary_path: str | None = None


class WorkPlanResponse(BaseModel):
    work_slug: str
    framework: PlanningFramework
    profile: PlanningProfile
    phase: PlanningPhase
    depth: PlanningDepth
    root_path: str
    planning_path: str
    artifact_root_path: str
    approved_at: str | None = None
    stale: bool
    overview: PlanOverviewResponse
    artifacts: list[PlanArtifactResponse]


class StartWorkPlanResponse(BaseModel):
    plan: WorkPlanResponse | None = None
    materialization_status: PlanMaterializationStatusResponse


class PlanArtifactDetailResponse(BaseModel):
    artifact: PlanArtifactResponse
    content: str


class UpdatePlanArtifactRequest(BaseModel):
    content: str = Field(min_length=1)
    expected_hash: str = Field(min_length=1)


class AcceptPlanArtifactRequest(BaseModel):
    summary: str = ""
    agent_slug: str | None = None
    divergences: str = ""
    skipped_scope: str = ""
    blockers: str = ""
    decisions: str = ""
    changes: str = ""
    validation_evidence: str = ""


class StartPlanArtifactRunRequest(BaseModel):
    agent_slug: str | None = Field(default=None, min_length=1)
    loop_definition_id: str | None = None
    loop_revision: str | None = None


class ResumePlanArtifactRunRequest(BaseModel):
    resolution_note: str = ""


class RequestPlanRunChangesRequest(BaseModel):
    note: str = Field(min_length=1)


class CreatePlanArtifactProposalRequest(BaseModel):
    title: str = ""
    proposed_content: str = Field(min_length=1)


class LinkPlanArtifactTrackingRequest(BaseModel):
    kind: PlanTrackingKind
    title: str = ""
    url: str = ""
    status: str = ""
    ref: str = ""
    notes: str = ""


class CreatePlanBugRequest(BaseModel):
    title: str = Field(min_length=1)
    description: str = ""


# ---------------------------------------------------------------------------
# Reusable loops
# ---------------------------------------------------------------------------


class LoopContextReferenceSchema(BaseModel):
    kind: LoopContextKind
    required: bool = False
    paths: list[str] = Field(default_factory=list)
    step: str | None = None
    ref: str | None = None


class LoopAgentPolicySchema(BaseModel):
    session: LoopSessionPolicy = LoopSessionPolicy.FRESH
    permissions: LoopPermission = LoopPermission.READ
    provider: str | None = None
    model: str | None = None
    effort: str | None = None


class LoopRetryPolicySchema(BaseModel):
    max_attempts: int = Field(default=2, ge=1, le=20)
    timeout_minutes: int = Field(default=20, ge=1, le=1440)


class LoopStepDefinitionSchema(BaseModel):
    id: str = Field(min_length=1)
    name: str = Field(min_length=1)
    kind: LoopStepKind
    instructions: str = ""
    context: list[LoopContextReferenceSchema] = Field(default_factory=list)
    agent: LoopAgentPolicySchema | None = None
    report_contract: str = "generic"
    retry: LoopRetryPolicySchema = Field(default_factory=LoopRetryPolicySchema)
    transitions: dict[LoopOutcome, str | None] = Field(default_factory=dict)
    check_adapter: str | None = None
    check_command: list[str] = Field(default_factory=list)


class LoopDefinitionResponse(BaseModel):
    id: str
    name: str
    description: str
    scope: LoopDefinitionScope
    revision: str
    valid: bool
    errors: list[str] = Field(default_factory=list)
    is_default: bool = False
    forked_from: str | None = None
    stages: list[LoopStepDefinitionSchema] = Field(default_factory=list)


class SaveLoopDefinitionRequest(BaseModel):
    id: str = Field(min_length=1)
    name: str = Field(min_length=1)
    description: str = ""
    expected_revision: str | None = None
    forked_from: str | None = None
    stages: list[LoopStepDefinitionSchema] = Field(default_factory=list)


class ForkLoopDefinitionRequest(BaseModel):
    id: str = Field(min_length=1)
    name: str = Field(min_length=1)


class MarkPlanRunCleanedRequest(BaseModel):
    agent_slug: str = Field(min_length=1)


__all__ = [
    "AcceptPlanArtifactRequest",
    "AgentSummary",
    "ArtifactSummary",
    "ChatCompactionSummaryResponse",
    "ChatDetail",
    "ChatGroundingSchema",
    "ChatMessageSchema",
    "ChatSummary",
    "CompactChatRequest",
    "CompactChatResponse",
    "ConnectionRead",
    "ContextSchema",
    "CreatePlanArtifactProposalRequest",
    "CreatePlanBugRequest",
    "DetachResponse",
    "ForkLoopDefinitionRequest",
    "HandoffSummary",
    "LinkPlanArtifactTrackingRequest",
    "LoopAgentPolicySchema",
    "LoopContextReferenceSchema",
    "LoopDefinitionResponse",
    "LoopRetryPolicySchema",
    "LoopStepDefinitionSchema",
    "MarkPlanRunCleanedRequest",
    "NewAgentRequest",
    "NewChatRequest",
    "NewConnectionRequest",
    "NewHandoffRequest",
    "NewProjectRequest",
    "NewWorkRequest",
    "PatchAgentRequest",
    "PatchConnectionRequest",
    "PatchProjectRequest",
    "PatchWorkRequest",
    "PlanArtifactDetailResponse",
    "PlanArtifactProposalResponse",
    "PlanArtifactResponse",
    "PlanArtifactRunResponse",
    "PlanLoopStageRunResponse",
    "PlanMaterializationStatusResponse",
    "PlanOverviewResponse",
    "PlanTrackingLinkResponse",
    "PlanningFrameworkStatusRequest",
    "PlanningFrameworkStatusResponse",
    "ProjectDetail",
    "ProjectSummary",
    "PromoteChatRequest",
    "RequestPlanRunChangesRequest",
    "ResumePlanArtifactRunRequest",
    "SaveLoopDefinitionRequest",
    "SendChatMessageRequest",
    "StartPlanArtifactRunRequest",
    "StartPlanningChatRequest",
    "StartPlanningSetupChatRequest",
    "StartWorkPlanRequest",
    "StartWorkPlanResponse",
    "SwitchThreadRequest",
    "UpdatePlanArtifactRequest",
    "VerifyResponse",
    "WorkChatContextDocResponse",
    "WorkChatContextFolderSummary",
    "WorkChatRef",
    "WorkDetail",
    "WorkPlanResponse",
    "WorkSummary",
]
