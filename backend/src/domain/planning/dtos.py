"""DTOs for source-backed Work planning."""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import StrEnum
from typing import Literal

from src.domain.loop.dtos import (
    LoopChangedFile,
    LoopCriterionCoverage,
    LoopFinding,
    LoopPermission,
    LoopReportSource,
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
PlanReadiness = Literal["ready", "needs_detail"]

class PlanRunStatus(StrEnum):
    RUNNING = "running"
    NEEDS_ATTENTION = "needs_attention"
    WAITING_APPROVAL = "waiting_approval"
    BLOCKED = "blocked"
    COMPLETED_PENDING_REVIEW = "completed_pending_review"
    ACCEPTED = "accepted"


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
class SubmitPlanArtifactReportRequest:
    """Structured completion report for an artifact agent run."""

    artifact_id: str
    run_id: str | None = None
    agent_slug: str | None = None
    summary: str = ""
    divergences: str = ""
    skipped_scope: str = ""
    blockers: str = ""
    decisions: str = ""
    changes: str = ""
    validation_evidence: str = ""
    report_source: LoopReportSource = LoopReportSource.MANUAL


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
    loop_status: LoopStatus | None = None
    loop_status_reason: str = ""
    loop_attempt: int = 1
    loop_latest_assessment: list[str] = field(default_factory=list)
    loop_definition_id: str = ""
    loop_definition_name: str = ""
    loop_definition_revision: str = ""
    loop_current_stage_id: str = ""
    loop_stages: list[PlanLoopStageRun] = field(default_factory=list)


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
    artifact_root_path: str
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
    "SubmitPlanArtifactReportRequest",
    "UpdatePlanArtifactRequest",
    "WorkPlanView",
]
