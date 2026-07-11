"""Planning-specific loop definition and report helpers."""

from __future__ import annotations

import json

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
from src.domain.planning.dtos import (
    PlanArtifactSummary,
    PlanRunStatus,
    SubmitPlanArtifactReportRequest,
)

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

_DEFAULT_BLOCKER_INSTRUCTION = (
    "If user input is required, put the concrete blocker in the `blockers` field."
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
    if loop_status in {
        LoopStatus.PENDING,
        LoopStatus.RUNNING,
        LoopStatus.WAITING_REPORT,
        LoopStatus.ASSESSING,
    }:
        return PlanRunStatus.RUNNING
    if loop_status == LoopStatus.NEEDS_AGENT:
        return PlanRunStatus.NEEDS_ATTENTION
    if loop_status in {
        LoopStatus.BLOCKED_USER,
        LoopStatus.FAILED,
        LoopStatus.CANCELLED,
    }:
        return PlanRunStatus.BLOCKED
    return PlanRunStatus.COMPLETED_PENDING_REVIEW


def loop_status_for_run_status(status: PlanRunStatus) -> LoopStatus:
    """Infer loop status for legacy planning runs without loop metadata.

    Preconditions: ``status`` is a valid Planning run status.
    Postconditions: returns a displayable loop status.
    """
    if status == PlanRunStatus.RUNNING:
        return LoopStatus.RUNNING
    if status == PlanRunStatus.NEEDS_ATTENTION:
        return LoopStatus.NEEDS_AGENT
    if status == PlanRunStatus.BLOCKED:
        return LoopStatus.BLOCKED_USER
    return LoopStatus.COMPLETED


def continuation_prompt(artifact: PlanArtifactSummary, loop: dict[str, object]) -> str:
    """Build the prompt used to continue an incomplete artifact loop.

    Preconditions: ``loop`` is the stored loop snapshot for ``artifact``.
    Postconditions: returns a provider-facing prompt without mutating inputs.
    """
    next_prompt = str(loop.get("next_prompt") or "").strip()
    raw_findings = loop.get("findings", [])
    findings: list[str] = []
    if isinstance(raw_findings, list):
        findings = [
            str(item).strip()
            for item in raw_findings
            if isinstance(item, str) and item.strip()
        ]
    findings_text = "\n".join(f"- {item}" for item in findings)
    return (
        f"Continue the execution loop for planning artifact `{artifact.id}`: "
        f"{artifact.title}.\n\n"
        "Atelier assessed the latest report and the loop is not complete yet."
        + (f"\n\nAssessment findings:\n{findings_text}" if findings_text else "")
        + (f"\n\nNext instruction:\n{next_prompt}" if next_prompt else "")
        + "\n\n"
        + _report_contract(
            "Finish any remaining artifact work, then",
            blocker_instruction=_DEFAULT_BLOCKER_INSTRUCTION,
        )
    )


def initial_run_prompt(artifact: PlanArtifactSummary) -> str:
    """Build the prompt used to start an artifact execution run.

    Preconditions: ``artifact`` is executable and assigned to an agent.
    Postconditions: returns a provider-facing prompt without mutating inputs.
    """
    return (
        f"Start the execution loop for planning artifact `{artifact.id}`: "
        f"{artifact.title}.\n\n"
        f"Source artifact: {artifact.source_ref}\n\n"
        "Stay within this artifact's scope. "
        + _report_contract(
            "When the work reaches a stopping point,",
            blocker_instruction=_DEFAULT_BLOCKER_INSTRUCTION,
        )
    )


def blocker_resolved_prompt(
    artifact: PlanArtifactSummary,
    loop: dict[str, object],
    resolution_note: str,
) -> str:
    """Build the prompt used after a user marks a blocker resolved.

    Preconditions: ``loop`` is a blocked-user snapshot for ``artifact``.
    Postconditions: returns a provider-facing prompt without mutating inputs.
    """
    note = resolution_note.strip()
    blocker_text = ""
    findings = loop.get("findings", [])
    if isinstance(findings, list):
        blocker_text = "\n".join(
            f"- {str(item).strip()}"
            for item in findings
            if isinstance(item, str) and item.strip()
        )
    return (
        f"Resume the execution loop for planning artifact `{artifact.id}`: "
        f"{artifact.title}.\n\n"
        "The user marked the reported blocker as resolved."
        + (f"\n\nPrevious blocker:\n{blocker_text}" if blocker_text else "")
        + (f"\n\nResolution note:\n{note}" if note else "")
        + "\n\n"
        + _report_contract(
            "Continue the remaining work, then",
            blocker_instruction=(
                "If another user decision is required, put the concrete blocker "
                "in the `blockers` field."
            ),
        )
    )


def _report_contract(action: str, *, blocker_instruction: str) -> str:
    """Render the shared report contract for planning artifact loops.

    Preconditions: ``action`` is the sentence prefix before the report request.
    Postconditions: returns prompt text; no state is changed.
    """
    return (
        f"{action} respond with exactly one single-line JSON report using this "
        "shape:\n\n"
        f"{_report_example()}\n\n"
        "Do not wrap the JSON in Markdown fences. Use an explicit `None.` only "
        f"for fields where there is truly nothing to report. {blocker_instruction} "
        "If you need to propose a full source update for the artifact, include "
        "`proposed_source` in the same JSON object."
    )


def _report_example() -> str:
    """Render the single-line JSON marker required from loop agents.

    Preconditions: the planning report schema fields are ordered for display.
    Postconditions: returns an example matching the extraction contract.
    """
    return json.dumps(
        {
            "atelier_loop_report": {
                field.key: "..."
                for field in PLANNING_ARTIFACT_REPORT_SCHEMA.fields
            }
        },
        separators=(",", ":"),
    )


__all__ = [
    "PLANNING_ARTIFACT_LOOP_DEFINITION",
    "PLANNING_ARTIFACT_LOOP_DEFINITION_ID",
    "PLANNING_ARTIFACT_REPORT_SCHEMA",
    "PLANNING_ARTIFACT_REPORT_SCHEMA_ID",
    "assess_planning_report",
    "blocker_resolved_prompt",
    "build_report",
    "continuation_prompt",
    "initial_run_prompt",
    "loop_status_for_run_status",
    "run_status_for_loop",
]
