"""DTOs for source-backed Work planning."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Literal

from src.domain.loop.dtos import (
    LoopBrief,
    LoopChangedFile,
    LoopCriterionCoverage,
    LoopDefinition,
    LoopFinding,
    LoopOutcome,
    LoopPermission,
    LoopReviewDecision,
    LoopRunStatus,
    LoopSessionPolicy,
    LoopStatus,
    LoopStepKind,
    LoopStepStatus,
)

PlanningDepth = Literal["minimal", "lightweight", "standard", "deep", "custom"]
PlanningFramework = Literal["bmad", "spec", "openspec", "custom"]
PlanningPhase = Literal["conversing", "planned"]
PlanningProfile = Literal[
    "feature", "refactor", "migration", "bugfix", "hotfix", "full_app", "custom"
]
PlanArtifactKind = Literal[
    "brief",
    "architecture",
    "spec",
    "scenario",
    "acceptance",
    "story",
    "task",
    "spike",
    "bug",
    "hotfix",
    "note",
]
PlanArtifactStatus = Literal["draft", "approved", "changed", "accepted"]
# How a story is being worked. ``None`` until the first launch; ``agents``
# is one-way -- a story once worked by hand-launched agents stays that way.
PlanWorkMode = Literal["loop", "agents"]
PlanReadiness = Literal["ready", "needs_detail"]

PlanRunStatus = LoopRunStatus


PlanProposalStatus = Literal["pending", "accepted", "rejected"]
PlanTrackingKind = Literal["jira", "pr", "blocker", "bug"]


@dataclass(frozen=True)
class PlanningFrameworkStatus:
    """Repository-local readiness for a selected planning framework."""

    framework: PlanningFramework
    label: str
    root_path: str
    ready: bool
    markers: list[str] = field(default_factory=list)
    setup_command: list[str] = field(default_factory=list)
    setup_hint: str = ""


@dataclass(frozen=True)
class PlanArtifactEntry:
    """Metadata-only contract for one framework-generated planning file."""

    path: str
    title: str
    artifact_kind: PlanArtifactKind
    executable: bool
    dependencies: tuple[str, ...] = ()


@dataclass(frozen=True)
class UpdatePlanArtifactRequest:
    """Approved replacement content for one source-backed plan artifact."""

    artifact_id: str
    content: str
    expected_hash: str


@dataclass(frozen=True)
class AcceptPlanArtifactRequest:
    """Reviewer acceptance summary for one executable plan artifact."""

    artifact_id: str
    summary: str
    agent_slug: str | None = None
    divergences: str = ""
    skipped_scope: str = ""
    blockers: str = ""
    decisions: str = ""
    changes: str = ""
    validation_evidence: str = ""


@dataclass(frozen=True)
class StartPlanArtifactRunRequest:
    """Start an agent run for one executable plan artifact."""

    artifact_id: str
    agent_slug: str
    loop_definition_id: str | None = None
    loop_revision: str | None = None


@dataclass(frozen=True)
class CreatePlanArtifactProposalRequest:
    """Full-document proposed source update for review."""

    artifact_id: str
    title: str
    proposed_content: str


@dataclass(frozen=True)
class ResolvePlanArtifactProposalRequest:
    """Accept or reject one proposed source update."""

    artifact_id: str
    proposal_id: str


@dataclass(frozen=True)
class LinkPlanArtifactTrackingRequest:
    """Attach lightweight tracking metadata to an artifact."""

    artifact_id: str
    kind: PlanTrackingKind
    title: str
    url: str = ""
    status: str = ""
    ref: str = ""
    notes: str = ""


@dataclass(frozen=True)
class CreatePlanBugRequest:
    """Create a source-backed bug from an artifact finding."""

    artifact_id: str
    title: str
    description: str


@dataclass(frozen=True)
class MarkPlanRunCleanedRequest:
    """Mark a transient artifact agent workspace as cleaned."""

    artifact_id: str
    agent_slug: str


@dataclass(frozen=True)
class PlanArtifactRun:
    """Structured execution state linked to one artifact."""

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
    brief_note: str = ""
    # The whole pinned brief, so a follow-up or a new run can seed the setup
    # screen from what this run actually ran with. `brief_note` stays as the
    # one-line summary the story card shows.
    brief: LoopBrief | None = None
    loop_status: LoopStatus | None = None
    loop_status_reason: str = ""
    loop_attempt: int = 1
    loop_latest_assessment: list[str] = field(default_factory=list)
    loop_definition_id: str = ""
    loop_definition_name: str = ""
    loop_definition_revision: str = ""
    loop_definition: LoopDefinition | None = None
    loop_current_stage_id: str = ""
    loop_stages: list[PlanLoopStageRun] = field(default_factory=list)
    loop_review_gate: dict[str, Any] | None = None
    waived_findings_count: int = 0
    waived_findings: list[str] = field(default_factory=list)
    feedback: list[dict[str, Any]] = field(default_factory=list)
    loop_pass_number: int = 1
    loop_passes: list[dict[str, Any]] = field(default_factory=list)
    pr: dict[str, Any] | None = None
    pr_comments: list[dict[str, Any]] = field(default_factory=list)


@dataclass(frozen=True)
class PlanLoopStageRun:
    """Displayable execution state for one snapshotted loop stage."""

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
    findings: list[str] = field(default_factory=list)
    changes: str = ""
    validation_evidence: str = ""
    divergences: str = ""
    skipped_scope: str = ""
    blocker: str = ""
    artifact_refs: list[str] = field(default_factory=list)
    finding_details: list[LoopFinding] = field(default_factory=list)
    criteria_coverage: list[LoopCriterionCoverage] = field(default_factory=list)
    changed_files: list[LoopChangedFile] = field(default_factory=list)
    resolved_context: list[str] = field(default_factory=list)
    context_warnings: list[str] = field(default_factory=list)
    reports: list[PlanLoopStageReport] = field(default_factory=list)
    push_at: str | None = None
    pr: dict[str, Any] | None = None
    addressed_comments: list[dict[str, Any]] = field(default_factory=list)
    feedback_instruction: str = ""
    approved_command_prefixes: list[str] = field(default_factory=list)


@dataclass(frozen=True)
class PlanLoopStageReport:
    """Immutable output from one completed occurrence of a loop stage."""

    outcome: LoopOutcome
    pass_number: int = 1
    agent_slug: str | None = None
    summary: str = ""
    findings: list[str] = field(default_factory=list)
    changes: str = ""
    validation_evidence: str = ""
    divergences: str = ""
    skipped_scope: str = ""
    blocker: str = ""
    artifact_refs: list[str] = field(default_factory=list)
    finding_details: list[LoopFinding] = field(default_factory=list)
    criteria_coverage: list[LoopCriterionCoverage] = field(default_factory=list)
    changed_files: list[LoopChangedFile] = field(default_factory=list)
    seq: int = 0
    recorded_at: str = ""
    review_decision: LoopReviewDecision | None = None
    push_at: str | None = None
    pr: dict[str, Any] | None = None
    addressed_comments: list[dict[str, Any]] = field(default_factory=list)
    feedback_instruction: str = ""


@dataclass(frozen=True)
class PlanArtifactProposal:
    """Reviewable source update proposed by an agent or reviewer."""

    id: str
    artifact_id: str
    title: str
    path: str
    source_hash: str
    proposed_content: str
    status: PlanProposalStatus
    created_at: str
    resolved_at: str | None = None


@dataclass(frozen=True)
class PlanTrackingLink:
    """Lightweight planning tracker item attached to one artifact."""

    id: str
    kind: PlanTrackingKind
    title: str
    url: str = ""
    status: str = ""
    ref: str = ""
    notes: str = ""
    created_at: str = ""


@dataclass(frozen=True)
class PlanArtifactSummary:
    """Indexed source document row rendered in the Plan outline."""

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
    launch_blockers: list[str] = field(default_factory=list)
    dependencies: list[str] = field(default_factory=list)
    runs: list[PlanArtifactRun] = field(default_factory=list)
    proposals: list[PlanArtifactProposal] = field(default_factory=list)
    tracking: list[PlanTrackingLink] = field(default_factory=list)
    accepted_summary_path: str | None = None
    work_mode: PlanWorkMode | None = None


@dataclass(frozen=True)
class PlanOverview:
    """Action-focused counts for the current Plan projection."""

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


@dataclass(frozen=True)
class WorkPlanView:
    """Normalized Plan View projected from source documents."""

    work_slug: str
    framework: PlanningFramework
    profile: PlanningProfile
    phase: PlanningPhase
    depth: PlanningDepth
    root_path: str
    planning_path: str
    plan_artifacts_path: str
    approved_at: str | None
    stale: bool
    artifacts: list[PlanArtifactSummary] = field(default_factory=list)
    overview: PlanOverview | None = None


@dataclass(frozen=True)
class PlanArtifactDetail:
    """A source-backed artifact plus its editable Markdown content."""

    artifact: PlanArtifactSummary
    content: str


__all__ = [
    "AcceptPlanArtifactRequest",
    "CreatePlanArtifactProposalRequest",
    "CreatePlanBugRequest",
    "LinkPlanArtifactTrackingRequest",
    "MarkPlanRunCleanedRequest",
    "PlanArtifactDetail",
    "PlanArtifactEntry",
    "PlanArtifactKind",
    "PlanArtifactProposal",
    "PlanArtifactRun",
    "PlanArtifactStatus",
    "PlanArtifactSummary",
    "PlanLoopStageRun",
    "PlanOverview",
    "PlanProposalStatus",
    "PlanReadiness",
    "PlanRunStatus",
    "PlanTrackingKind",
    "PlanTrackingLink",
    "PlanningDepth",
    "PlanningFramework",
    "PlanningPhase",
    "PlanningProfile",
    "ResolvePlanArtifactProposalRequest",
    "StartPlanArtifactRunRequest",
    "UpdatePlanArtifactRequest",
    "WorkPlanView",
]
