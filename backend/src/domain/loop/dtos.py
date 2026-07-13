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


class LoopStepStatus(StrEnum):
    """Durable lifecycle state for one stage in a loop run."""

    PENDING = "pending"
    RUNNING = "running"
    BLOCKED_USER = "blocked_user"
    PASSED = "passed"
    CHANGES_REQUESTED = "changes_requested"
    FAILED = "failed"
    CANCELLED = "cancelled"


class LoopTargetKind(StrEnum):
    """Supported inputs to the shared loop engine."""

    PLANNING_ARTIFACT = "planning_artifact"
    OBJECTIVE = "objective"


class LoopReportSource(StrEnum):
    MCP_TOOL = "mcp_tool"
    TRANSCRIPT_MARKDOWN = "transcript_markdown"
    MANUAL = "manual"


class LoopAssessmentStatus(StrEnum):
    PASS = "pass"
    NEEDS_AGENT = "needs_agent"
    BLOCKED_USER = "blocked_user"
    FAIL = "fail"


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
    SHARED_CONTEXT = "shared_context"


class LoopOutcome(StrEnum):
    """Outcomes that can transition a stage."""

    PASS = "pass"
    CHANGES_REQUESTED = "changes_requested"
    BLOCKED_USER = "blocked_user"
    FAILED = "failed"


class LoopFindingSeverity(StrEnum):
    """Severity assigned by an independent review stage."""

    HIGH = "high"
    MEDIUM = "medium"
    LOW = "low"
    RESOLVED = "resolved"


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
class LoopReport:
    """Structured report submitted by an agent, tool, transcript, or user."""

    report_id: str
    loop_run_id: str
    source: LoopReportSource
    fields: dict[str, str]
    submitted_at: str
    submitted_by: str | None = None
    raw_ref: str | None = None


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


@dataclass(frozen=True)
class LoopCheckResult:
    """Bounded process result returned by a check-runner adapter."""

    exit_code: int | None
    stdout: str = ""
    stderr: str = ""
    timed_out: bool = False


@dataclass(frozen=True)
class LoopAssessment:
    """Backend decision about whether a loop report is complete enough."""

    assessment_id: str
    loop_run_id: str
    status: LoopAssessmentStatus
    findings: list[str] = field(default_factory=list)
    next_prompt: str = ""
    created_at: str = ""
    assessor: str = "deterministic"


__all__ = [
    "LoopAgentPolicy",
    "LoopAssessment",
    "LoopAssessmentStatus",
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
    "LoopReport",
    "LoopReportField",
    "LoopReportSchema",
    "LoopReportSource",
    "LoopRetryPolicy",
    "LoopRun",
    "LoopSessionPolicy",
    "LoopStageReport",
    "LoopStatus",
    "LoopStepDefinition",
    "LoopStepKind",
    "LoopStepStatus",
    "LoopTargetKind",
]
