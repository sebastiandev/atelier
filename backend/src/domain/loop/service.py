"""Pure loop validation and assessment actions."""

from __future__ import annotations

import re

from src.domain.loop.dtos import LoopAssessment, LoopReport, LoopReportSchema

_NONE_VALUES = {
    "none",
    "none.",
    "n/a",
    "n/a.",
    "na",
    "na.",
    "no",
    "no.",
    "no blockers",
    "no blockers.",
    "no divergences",
    "no divergences.",
    "no skipped scope",
    "no skipped scope.",
    "not applicable",
    "not applicable.",
}


def assess_report(
    schema: LoopReportSchema,
    report: LoopReport,
    *,
    assessment_id: str,
    created_at: str,
) -> LoopAssessment:
    """Assess one report against a schema.

    Preconditions: ``report.fields`` uses schema field keys.
    Postconditions: returns a deterministic assessment without mutating inputs.
    """
    missing = missing_required_fields(schema, report)
    if missing:
        return LoopAssessment(
            assessment_id=assessment_id,
            loop_run_id=report.loop_run_id,
            status="needs_agent",
            findings=[f"Missing required report field: {field}." for field in missing],
            next_prompt=_continue_prompt(missing),
            created_at=created_at,
        )

    blockers = _field(report, "blockers")
    if blockers and not is_explicit_none(blockers):
        return LoopAssessment(
            assessment_id=assessment_id,
            loop_run_id=report.loop_run_id,
            status="blocked_user",
            findings=[f"Blocker reported: {blockers}"],
            created_at=created_at,
        )

    return LoopAssessment(
        assessment_id=assessment_id,
        loop_run_id=report.loop_run_id,
        status="pass",
        created_at=created_at,
    )


def missing_required_fields(schema: LoopReportSchema, report: LoopReport) -> list[str]:
    """Return required schema fields not satisfied by the report.

    Preconditions: ``schema`` describes the report contract.
    Postconditions: result order matches schema field order.
    """
    missing: list[str] = []
    for field in schema.fields:
        if not field.required:
            continue
        value = _field(report, field.key)
        if value and (field.allow_explicit_none or not is_explicit_none(value)):
            continue
        missing.append(field.key)
    return missing


def is_explicit_none(value: str) -> bool:
    """Return whether a report field explicitly declares no content.

    Preconditions: value is a user/agent supplied report field.
    Postconditions: returns ``True`` only for common exact none-like answers.
    """
    normalized = re.sub(r"\s+", " ", value.strip().lower())
    return normalized in _NONE_VALUES


def _field(report: LoopReport, key: str) -> str:
    return report.fields.get(key, "").strip()


def _continue_prompt(missing: list[str]) -> str:
    labels = ", ".join(missing)
    return (
        "Continue the assigned work loop and submit a complete report. "
        f"The report is missing: {labels}."
    )


__all__ = ["assess_report", "is_explicit_none", "missing_required_fields"]
