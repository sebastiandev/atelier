"""Structured report extraction for multi-stage loop actors."""

from __future__ import annotations

import json
import re
from typing import Any

from src.domain.loop.dtos import (
    LoopChangedFile,
    LoopCommentReply,
    LoopCriterionCoverage,
    LoopFinding,
    LoopFindingSeverity,
    LoopOutcome,
    LoopStageReport,
)

_REPORT_RE = re.compile(
    r'^\s*(\{\s*"atelier_loop_step_report"\s*:.*\})\s*$',
    re.MULTILINE,
)


def extract_latest_stage_report(
    events: list[dict[str, Any]],
) -> tuple[LoopStageReport | None, int] | None:
    """Read the latest complete assistant message as a stage report.

    Preconditions: events are ordered transcript rows for the active stage.
    Postconditions: returns ``(None, seq)`` for an invalid report so repair can
    be bounded independently from a genuinely idle agent.
    """
    messages = [
        (event.get("text"), event.get("seq"))
        for event in events
        if event.get("type") == "message_complete"
        and isinstance(event.get("text"), str)
        and isinstance(event.get("seq"), int)
    ]
    if not messages:
        return None
    text, seq = messages[-1]
    assert isinstance(text, str) and isinstance(seq, int)
    return _parse_stage_report(text), seq


def _parse_stage_report(text: str) -> LoopStageReport | None:
    match = _REPORT_RE.search(text)
    if match is None:
        return None
    try:
        raw = json.loads(match.group(1)).get("atelier_loop_step_report")
        if not isinstance(raw, dict):
            return None
        raw_outcome = raw.get("outcome")
        if not isinstance(raw_outcome, str):
            return None
        outcome = LoopOutcome(raw_outcome)
    except (json.JSONDecodeError, TypeError, ValueError):
        return None
    summary = _text(raw.get("summary"))
    if not summary:
        return None
    finding_details = _findings(raw.get("findings"))
    findings = tuple(item.text for item in finding_details)
    refs = _strings(raw.get("artifact_refs"))
    return LoopStageReport(
        outcome=outcome,
        summary=summary,
        findings=findings,
        changes=_text(raw.get("changes")),
        validation_evidence=_text(raw.get("validation_evidence")),
        divergences=_text(raw.get("divergences")),
        skipped_scope=_text(raw.get("skipped_scope")),
        blocker=_text(raw.get("blocker")),
        artifact_refs=refs,
        finding_details=finding_details,
        criteria_coverage=_criteria(raw.get("criteria")),
        changed_files=_changed_files(raw.get("changed_files")),
        comment_replies=_comment_replies(raw.get("comment_replies")),
    )


def _comment_replies(value: object) -> tuple[LoopCommentReply, ...]:
    """Read what the push did about each pull-request comment it answered.

    Optional everywhere: only the PR stage is asked for it, and a stage that
    omits it falls back to citing the commit alone rather than to the pass
    summary, which is not an answer to any particular comment.
    """
    if not isinstance(value, list):
        return ()
    replies: list[LoopCommentReply] = []
    for item in value:
        if not isinstance(item, dict):
            continue
        comment_id = _text(item.get("comment_id"))
        reply = _text(item.get("reply"))
        if comment_id and reply:
            replies.append(LoopCommentReply(comment_id=comment_id, reply=reply))
    return tuple(replies)


def _text(value: object) -> str:
    return value.strip() if isinstance(value, str) else ""


def _strings(value: object) -> tuple[str, ...]:
    if not isinstance(value, list):
        return ()
    return tuple(item.strip() for item in value if isinstance(item, str) and item.strip())


def _findings(value: object) -> tuple[LoopFinding, ...]:
    if not isinstance(value, list):
        return ()
    findings: list[LoopFinding] = []
    for item in value:
        if isinstance(item, str) and item.strip():
            findings.append(LoopFinding(text=item.strip()))
            continue
        if not isinstance(item, dict):
            continue
        text = _text(item.get("text"))
        if not text:
            continue
        raw_severity = _text(item.get("severity"))
        try:
            severity = LoopFindingSeverity(raw_severity)
        except ValueError:
            severity = LoopFindingSeverity.MEDIUM
        findings.append(
            LoopFinding(
                text=text,
                severity=severity,
                location=_text(item.get("location")),
            )
        )
    return tuple(findings)


def _criteria(value: object) -> tuple[LoopCriterionCoverage, ...]:
    if not isinstance(value, list):
        return ()
    rows: list[LoopCriterionCoverage] = []
    for item in value:
        if not isinstance(item, dict):
            continue
        text = _text(item.get("text"))
        met = item.get("met")
        if text and isinstance(met, bool):
            rows.append(
                LoopCriterionCoverage(
                    text=text,
                    met=met,
                    note=_text(item.get("note")),
                )
            )
    return tuple(rows)


def _changed_files(value: object) -> tuple[LoopChangedFile, ...]:
    if not isinstance(value, list):
        return ()
    rows: list[LoopChangedFile] = []
    for item in value:
        if not isinstance(item, dict):
            continue
        path = _text(item.get("path"))
        if not path:
            continue
        additions = item.get("additions")
        deletions = item.get("deletions")
        rows.append(
            LoopChangedFile(
                path=path,
                additions=additions if isinstance(additions, int) else 0,
                deletions=deletions if isinstance(deletions, int) else 0,
            )
        )
    return tuple(rows)


__all__ = ["extract_latest_stage_report"]
