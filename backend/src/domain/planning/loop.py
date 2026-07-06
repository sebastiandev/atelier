"""Planning-specific loop definition and report helpers."""

from __future__ import annotations

from src.domain.loop.dtos import (
    LoopAssessment,
    LoopDefinition,
    LoopReport,
    LoopReportField,
    LoopReportSchema,
    LoopReportSource,
    LoopStatus,
)
from src.domain.loop.service import assess_report
from src.domain.planning.dtos import PlanRunStatus, SubmitPlanArtifactReportRequest

PLANNING_ARTIFACT_LOOP_DEFINITION_ID = "planning-artifact-execution"
PLANNING_ARTIFACT_REPORT_SCHEMA_ID = "planning-artifact-report"

PLANNING_ARTIFACT_REPORT_SCHEMA = LoopReportSchema(
    schema_id=PLANNING_ARTIFACT_REPORT_SCHEMA_ID,
    fields=(
        LoopReportField("summary", "Summary", allow_explicit_none=False),
        LoopReportField("divergences", "Divergences"),
        LoopReportField("skipped_scope", "Skipped scope"),
        LoopReportField("blockers", "Blockers"),
        LoopReportField("decisions", "Decisions"),
        LoopReportField("changes", "Changes"),
        LoopReportField(
            "validation_evidence", "Validation evidence", allow_explicit_none=False
        ),
    ),
)

PLANNING_ARTIFACT_LOOP_DEFINITION = LoopDefinition(
    definition_id=PLANNING_ARTIFACT_LOOP_DEFINITION_ID,
    name="Planning Artifact Execution",
    trigger="planning_artifact_run",
    report_schema=PLANNING_ARTIFACT_REPORT_SCHEMA,
)


def build_report(
    req: SubmitPlanArtifactReportRequest,
    *,
    loop_run_id: str,
    report_id: str,
    submitted_at: str,
    source: LoopReportSource,
) -> LoopReport:
    """Build the generic loop report for a planning artifact report.

    Preconditions: ``req`` targets one planning artifact run.
    Postconditions: returns a generic report without mutating planning state.
    """
    return LoopReport(
        report_id=report_id,
        loop_run_id=loop_run_id,
        source=source,
        submitted_at=submitted_at,
        submitted_by=req.agent_slug,
        fields={
            "summary": req.summary,
            "divergences": req.divergences,
            "skipped_scope": req.skipped_scope,
            "blockers": req.blockers,
            "decisions": req.decisions,
            "changes": req.changes,
            "validation_evidence": req.validation_evidence,
        },
    )


def assess_planning_report(
    report: LoopReport, *, assessment_id: str, created_at: str
) -> LoopAssessment:
    """Assess a planning artifact loop report.

    Preconditions: report was built for the planning artifact schema.
    Postconditions: returns a generic loop assessment.
    """
    return assess_report(
        PLANNING_ARTIFACT_REPORT_SCHEMA,
        report,
        assessment_id=assessment_id,
        created_at=created_at,
    )


def run_status_for_loop(loop_status: LoopStatus) -> PlanRunStatus:
    """Map generic loop status to the legacy planning run status.

    Preconditions: ``loop_status`` is a valid loop state.
    Postconditions: returns the closest current Planning run status.
    """
    if loop_status in {"pending", "running", "waiting_report", "assessing"}:
        return "running"
    if loop_status == "needs_agent":
        return "needs_attention"
    if loop_status in {"blocked_user", "failed", "cancelled"}:
        return "blocked"
    return "completed_pending_review"


def loop_status_for_run_status(status: PlanRunStatus) -> LoopStatus:
    """Infer loop status for legacy planning runs without loop metadata.

    Preconditions: ``status`` is a valid Planning run status.
    Postconditions: returns a displayable loop status.
    """
    if status == "running":
        return "running"
    if status == "needs_attention":
        return "needs_agent"
    if status == "blocked":
        return "blocked_user"
    return "completed"


__all__ = [
    "PLANNING_ARTIFACT_LOOP_DEFINITION",
    "PLANNING_ARTIFACT_LOOP_DEFINITION_ID",
    "PLANNING_ARTIFACT_REPORT_SCHEMA",
    "PLANNING_ARTIFACT_REPORT_SCHEMA_ID",
    "assess_planning_report",
    "build_report",
    "loop_status_for_run_status",
    "run_status_for_loop",
]
