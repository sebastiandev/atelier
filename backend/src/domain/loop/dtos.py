"""DTOs for backend-owned execution loops."""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import StrEnum
from pathlib import Path


class LoopStatus(StrEnum):
    PENDING = "pending"
    RUNNING = "running"
    WAITING_REPORT = "waiting_report"
    ASSESSING = "assessing"
    NEEDS_AGENT = "needs_agent"
    BLOCKED_USER = "blocked_user"
    COMPLETED = "completed"
    AWAITING_APPROVAL = "awaiting_approval"
    ACCEPTED = "accepted"
    CLEANED = "cleaned"
    FAILED = "failed"
    CANCELLED = "cancelled"


class LoopRunStatus(StrEnum):
    """Aggregate execution status shared by Loop and Planning projections."""

    RUNNING = "running"
    NEEDS_ATTENTION = "needs_attention"
    WAITING_APPROVAL = "waiting_approval"
    BLOCKED = "blocked"
    COMPLETED_PENDING_REVIEW = "completed_pending_review"
    ACCEPTED = "accepted"


class LoopStepStatus(StrEnum):
    """Durable lifecycle state for one stage in a loop run."""

    PENDING = "pending"
    RUNNING = "running"
    BLOCKED_USER = "blocked_user"
    PASSED = "passed"
    CHANGES_REQUESTED = "changes_requested"
    FAILED = "failed"
    CANCELLED = "cancelled"
    SKIPPED = "skipped"


class LoopTargetKind(StrEnum):
    """Supported inputs to the shared loop engine."""

    PLANNING_ARTIFACT = "planning_artifact"
    OBJECTIVE = "objective"


class LoopRunSourceKind(StrEnum):
    """What triggered a loop run, when something other than the user did.

    A run with no source is a plain loop run started from a goal (Loop
    mode). This is provenance, not a different kind of run: the engine,
    stores and routes must not branch on it. New triggers are new members
    here, not new run types.
    """

    STORY = "story"


@dataclass(frozen=True)
class LoopRunSource:
    """Where a loop run was triggered from."""

    kind: LoopRunSourceKind
    ref: str


class LoopRunKind(StrEnum):
    """Why a loop run was started: fresh, or seeded from a previous one."""

    INITIAL = "initial"
    AMEND = "amend"
    VERIFY = "verify"


class LoopFailureKind(StrEnum):
    """Machine-readable cause of a terminal loop failure."""

    PROVIDER_RUNTIME = "provider_runtime"
    TIMEOUT = "timeout"
    INVALID_REPORT = "invalid_report"
    STAGE_OUTCOME = "stage_outcome"


class LoopDefinitionScope(StrEnum):
    """Where a reusable loop definition is owned."""

    BUILTIN = "builtin"
    REPOSITORY = "repo"
    WORK = "work"


class LoopStepKind(StrEnum):
    """Supported execution behavior for one loop stage."""

    AGENT_TASK = "agent_task"
    AGENT_REVIEW = "agent_review"
    DETERMINISTIC_CHECK = "deterministic_check"
    USER_APPROVAL = "user_approval"
    PR = "pr"


class StageDefinitionScope(StrEnum):
    """Where a reusable standalone stage definition is owned."""

    BUILTIN = "builtin"
    REPOSITORY = "repo"


class LoopPermission(StrEnum):
    """Filesystem posture granted to an agent stage."""

    READ = "read"
    WRITE = "write"


class LoopSessionPolicy(StrEnum):
    """Whether a stage reuses or starts an agent session."""

    REUSE = "reuse"
    FRESH = "fresh"


class LoopContextKind(StrEnum):
    """Context references a stage can resolve at execution time."""

    TARGET = "target"
    PLAN_INDEX = "plan_index"
    ARTIFACT_DEPENDENCIES = "artifact_dependencies"
    WORKSPACE_DIFF = "workspace_diff"
    CHANGED_FILES = "changed_files"
    PREVIOUS_REPORT = "previous_report"
    FILES = "files"
    FOLDER = "folder"
    NOTE = "note"
    SHARED_CONTEXT = "shared_context"


class LoopBriefContextKind(StrEnum):
    """Adhoc context kinds accepted from a Work's run brief."""

    FILE = "file"
    FOLDER = "folder"
    URL = "url"
    NOTE = "note"


class LoopOutcome(StrEnum):
    """Outcomes that can transition a stage."""

    PASS = "pass"
    CHANGES_REQUESTED = "changes_requested"
    BLOCKED_USER = "blocked_user"
    FAILED = "failed"


class LoopReviewGateMode(StrEnum):
    """Who decides whether review findings return to implementation."""

    AUTOMATIC = "automatic"
    HUMAN_CHECK = "human_check"


class LoopReviewDecisionKind(StrEnum):
    """A human decision made at a held review gate."""

    SEND_BACK = "send_back"
    APPROVE_AS_IS = "approve_as_is"


class LoopFindingSeverity(StrEnum):
    """Severity assigned by an independent review stage."""

    HIGH = "high"
    MEDIUM = "medium"
    LOW = "low"
    RESOLVED = "resolved"


@dataclass(frozen=True)
class LoopReviewDecision:
    """The findings and instruction selected at one held review occurrence."""

    decision: LoopReviewDecisionKind
    enforced_findings: tuple[int, ...] = ()
    instruction: str = ""


@dataclass(frozen=True)
class LoopReportField:
    """One configured field in a loop report schema."""

    key: str
    title: str
    required: bool = True
    allow_explicit_none: bool = True


@dataclass(frozen=True)
class LoopReportSchema:
    """Configurable report structure required from a loop agent."""

    schema_id: str
    fields: tuple[LoopReportField, ...]


@dataclass(frozen=True)
class LoopDefinition:
    """Reusable configuration for one type of backend loop."""

    definition_id: str
    name: str
    trigger: str
    report_schema: LoopReportSchema
    retry_limit: int = 2
    description: str = ""
    scope: LoopDefinitionScope = LoopDefinitionScope.BUILTIN
    revision: str = ""
    is_default: bool = False
    forked_from: str | None = None
    stages: tuple[LoopStepDefinition, ...] = ()
    errors: tuple[str, ...] = ()

    @property
    def valid(self) -> bool:
        """Return whether the definition passed structural validation."""
        return not self.errors


@dataclass(frozen=True)
class LoopContextReference:
    """One source of stage context resolved from the working root or run."""

    kind: LoopContextKind
    required: bool = False
    paths: tuple[str, ...] = ()
    step: str | None = None
    ref: str | None = None


@dataclass(frozen=True)
class LoopContextResolutionRequest:
    """Filesystem-safe inputs for resolving one stage's context index."""

    root_path: Path
    work_slug: str
    target_ref: str
    plan_index_ref: str
    dependencies: tuple[str, ...]
    shared_context_refs: tuple[str, ...]
    references: tuple[LoopContextReference, ...]


@dataclass(frozen=True)
class LoopContextResolution:
    """Resolved context references plus launch-blocking and optional misses."""

    entries: tuple[str, ...] = ()
    missing_required: tuple[str, ...] = ()
    warnings: tuple[str, ...] = ()


@dataclass(frozen=True)
class LoopAgentPolicy:
    """Provider and permission defaults for an agent-backed stage."""

    session: LoopSessionPolicy = LoopSessionPolicy.FRESH
    permissions: LoopPermission | None = None
    provider: str | None = None
    model: str | None = None
    effort: str | None = None
    fast: bool | None = None
    approved_command_prefixes: tuple[str, ...] | None = None


@dataclass(frozen=True)
class LoopBriefAgent:
    """Per-run execution overrides for one agent-backed stage."""

    provider: str | None = None
    model: str | None = None
    options: dict[str, object] = field(default_factory=dict)


@dataclass(frozen=True)
class LoopReviewGate:
    """Definition-owned policy for one changes-requested return edge."""

    mode: LoopReviewGateMode = LoopReviewGateMode.AUTOMATIC
    max_passes: int = 3
    locked: bool = False


@dataclass(frozen=True)
class LoopPrConfig:
    """Create-PR inputs persisted with a reusable stage or run snapshot."""

    name_template: str = "{goal} - {work-id}"
    description_mode: str = "automatic"
    description_instructions: str = ""
    manual_body: str = ""
    status: str = "draft"
    base_branch: str = "master"
    branch_name: str | None = None


@dataclass(frozen=True)
class StageDefinitionRef:
    """Revision provenance for one reusable stage linked into a loop."""

    definition_id: str
    revision: str


@dataclass(frozen=True)
class StageOverrides:
    """Sparse loop-local changes layered over one reusable stage definition."""

    name: str | None = None
    instructions: str | None = None
    context: tuple[LoopContextReference, ...] | None = None
    agent: LoopAgentPolicy | None = None
    report_contract: str | None = None
    retry: LoopRetryPolicy | None = None
    check_adapter: str | None = None
    check_command: tuple[str, ...] | None = None
    note_required: bool | None = None
    review_gate: LoopReviewGate | None = None
    pr_config: LoopPrConfig | None = None


@dataclass(frozen=True)
class LoopBriefContext:
    """One Work-owned context reference attached to a stage brief."""

    kind: LoopBriefContextKind
    value: str


@dataclass(frozen=True)
class LoopStageBrief:
    """Work-owned notes, context, and execution overrides for one stage."""

    stage_id: str
    note: str = ""
    context: tuple[LoopBriefContext, ...] = ()
    agent: LoopBriefAgent | None = None
    review_gate: LoopReviewGateMode | None = None
    approved_command_prefixes: tuple[str, ...] | None = None


@dataclass(frozen=True)
class LoopBrief:
    """Task-specific input saved on a Work and pinned by each run."""

    goal: str
    stages: tuple[LoopStageBrief, ...] = ()


@dataclass(frozen=True)
class LoopRetryPolicy:
    """Bounded retry and timeout configuration for one stage."""

    max_attempts: int = 2
    timeout_minutes: int = 20


@dataclass(frozen=True)
class LoopStepDefinition:
    """One ordered stage in a reusable loop definition."""

    step_id: str
    name: str
    kind: LoopStepKind
    instructions: str = ""
    context: tuple[LoopContextReference, ...] = ()
    agent: LoopAgentPolicy | None = None
    report_contract: str = "generic"
    retry: LoopRetryPolicy = field(default_factory=LoopRetryPolicy)
    transitions: dict[LoopOutcome, str | None] = field(default_factory=dict)
    check_adapter: str | None = None
    check_command: tuple[str, ...] = ()
    note_required: bool | None = None
    review_gate: LoopReviewGate | None = None
    pr_config: LoopPrConfig | None = None
    stage_ref: StageDefinitionRef | None = None
    overrides: StageOverrides | None = None


@dataclass(frozen=True)
class StageDefinition:
    """A reusable loop-independent stage and its declared outcomes."""

    definition_id: str
    name: str
    stage: LoopStepDefinition
    outcomes: tuple[LoopOutcome, ...]
    description: str = ""
    scope: StageDefinitionScope = StageDefinitionScope.BUILTIN
    revision: str = ""
    forked_from: str | None = None
    used_by: tuple[str, ...] = ()
    errors: tuple[str, ...] = ()

    @property
    def valid(self) -> bool:
        """Return whether standalone-stage validation succeeded."""
        return not self.errors


@dataclass(frozen=True)
class LoopRun:
    """One execution instance of a loop definition."""

    loop_run_id: str
    definition_id: str
    parent_ref: str
    status: LoopStatus
    status_reason: str = ""
    attempt: int = 1
    agent_slug: str | None = None
    launch_packet_ref: str | None = None
    latest_report_id: str | None = None
    latest_assessment_id: str | None = None


@dataclass(frozen=True)
class LoopStageReport:
    """Validated structured output emitted by one multi-stage loop actor."""

    outcome: LoopOutcome
    summary: str
    findings: tuple[str, ...] = ()
    changes: str = ""
    validation_evidence: str = ""
    divergences: str = ""
    skipped_scope: str = ""
    blocker: str = ""
    artifact_refs: tuple[str, ...] = ()
    finding_details: tuple[LoopFinding, ...] = ()
    criteria_coverage: tuple[LoopCriterionCoverage, ...] = ()
    changed_files: tuple[LoopChangedFile, ...] = ()


@dataclass(frozen=True)
class LoopFinding:
    """One structured review finding."""

    text: str
    severity: LoopFindingSeverity = LoopFindingSeverity.MEDIUM
    location: str = ""


@dataclass(frozen=True)
class LoopCriterionCoverage:
    """Review evidence for one acceptance criterion."""

    text: str
    met: bool
    note: str = ""


@dataclass(frozen=True)
class LoopChangedFile:
    """One changed-file reference reported by a stage."""

    path: str
    additions: int = 0
    deletions: int = 0


@dataclass(frozen=True)
class LoopCheckRequest:
    """One deterministic command check executed in a run workspace."""

    workdir: Path
    argv: tuple[str, ...]
    timeout_seconds: float
    changed_files: tuple[str, ...] = ()


@dataclass(frozen=True)
class LoopCheckResult:
    """Bounded process result returned by a check-runner adapter."""

    exit_code: int | None
    stdout: str = ""
    stderr: str = ""
    timed_out: bool = False


__all__ = [
    "LoopAgentPolicy",
    "LoopBrief",
    "LoopBriefAgent",
    "LoopBriefContext",
    "LoopBriefContextKind",
    "LoopChangedFile",
    "LoopCheckRequest",
    "LoopCheckResult",
    "LoopContextKind",
    "LoopContextReference",
    "LoopContextResolution",
    "LoopContextResolutionRequest",
    "LoopCriterionCoverage",
    "LoopDefinition",
    "LoopDefinitionScope",
    "LoopFinding",
    "LoopFindingSeverity",
    "LoopOutcome",
    "LoopPermission",
    "LoopPrConfig",
    "LoopReportField",
    "LoopReportSchema",
    "LoopRetryPolicy",
    "LoopReviewGate",
    "LoopReviewGateMode",
    "LoopRun",
    "LoopRunKind",
    "LoopRunStatus",
    "LoopSessionPolicy",
    "LoopStageBrief",
    "LoopStageReport",
    "LoopStatus",
    "LoopStepDefinition",
    "LoopStepKind",
    "LoopStepStatus",
    "LoopTargetKind",
    "StageDefinition",
    "StageDefinitionRef",
    "StageDefinitionScope",
    "StageOverrides",
]
