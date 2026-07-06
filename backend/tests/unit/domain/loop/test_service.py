from __future__ import annotations

from src.domain.loop.dtos import LoopReport
from src.domain.loop.service import assess_report, is_explicit_none
from src.domain.planning.loop import PLANNING_ARTIFACT_REPORT_SCHEMA


def test_assesses_complete_report_as_pass() -> None:
    report = _report(
        {
            "summary": "Implemented the artifact.",
            "divergences": "None.",
            "skipped_scope": "None.",
            "blockers": "None.",
            "decisions": "Used the existing service boundary.",
            "changes": "backend/src/domain/planning/service.py",
            "validation_evidence": "pytest passed",
        }
    )

    assessment = assess_report(
        PLANNING_ARTIFACT_REPORT_SCHEMA,
        report,
        assessment_id="assess-001",
        created_at="2026-07-03T00:00:00+00:00",
    )

    assert assessment.status == "pass"
    assert assessment.findings == []


def test_assesses_missing_required_fields_as_needs_agent() -> None:
    report = _report({"summary": "Started.", "blockers": "None."})

    assessment = assess_report(
        PLANNING_ARTIFACT_REPORT_SCHEMA,
        report,
        assessment_id="assess-001",
        created_at="2026-07-03T00:00:00+00:00",
    )

    assert assessment.status == "needs_agent"
    assert "Missing required report field: divergences." in assessment.findings
    assert "Missing required report field: validation_evidence." in assessment.findings
    assert "validation_evidence" in assessment.next_prompt


def test_rejects_none_for_fields_that_need_real_content() -> None:
    report = _report(
        {
            "summary": "None.",
            "divergences": "None.",
            "skipped_scope": "None.",
            "blockers": "None.",
            "decisions": "None.",
            "changes": "None.",
            "validation_evidence": "None.",
        }
    )

    assessment = assess_report(
        PLANNING_ARTIFACT_REPORT_SCHEMA,
        report,
        assessment_id="assess-001",
        created_at="2026-07-03T00:00:00+00:00",
    )

    assert assessment.status == "needs_agent"
    assert "Missing required report field: summary." in assessment.findings
    assert "Missing required report field: validation_evidence." in assessment.findings


def test_assesses_reported_blocker_as_user_blocked() -> None:
    report = _report(
        {
            "summary": "Could not finish.",
            "divergences": "None.",
            "skipped_scope": "Story remains incomplete.",
            "blockers": "Need the user to choose an API contract.",
            "decisions": "None.",
            "changes": "None.",
            "validation_evidence": "Not run because the API contract is blocked.",
        }
    )

    assessment = assess_report(
        PLANNING_ARTIFACT_REPORT_SCHEMA,
        report,
        assessment_id="assess-001",
        created_at="2026-07-03T00:00:00+00:00",
    )

    assert assessment.status == "blocked_user"
    assert assessment.findings == [
        "Blocker reported: Need the user to choose an API contract."
    ]


def test_none_detection_is_exact_enough() -> None:
    assert is_explicit_none("No blockers.")
    assert not is_explicit_none("No blockers except the missing API key.")


def _report(fields: dict[str, str]) -> LoopReport:
    return LoopReport(
        report_id="report-001",
        loop_run_id="loop-story-001-agt-1",
        source="manual",
        submitted_at="2026-07-03T00:00:00+00:00",
        fields=fields,
    )
