"""DTOs for backend-owned execution loops."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Literal

LoopStatus = Literal[
    "pending",
    "running",
    "waiting_report",
    "assessing",
    "needs_agent",
    "blocked_user",
    "completed",
    "failed",
    "cancelled",
]
LoopReportSource = Literal["mcp_tool", "transcript_markdown", "manual"]
LoopAssessmentStatus = Literal["pass", "needs_agent", "blocked_user", "fail"]


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
    "LoopAssessment",
    "LoopAssessmentStatus",
    "LoopDefinition",
    "LoopReport",
    "LoopReportField",
    "LoopReportSchema",
    "LoopReportSource",
    "LoopRun",
    "LoopStatus",
]
