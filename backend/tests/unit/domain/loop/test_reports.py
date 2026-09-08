"""Public-interface tests for structured loop stage reports."""

import json

import pytest

from src.domain.loop.dtos import LoopFindingSeverity, LoopOutcome
from src.domain.loop.reports import extract_latest_stage_report


def test_extracts_review_evidence_and_file_references() -> None:
    payload = {
        "atelier_loop_step_report": {
            "outcome": "pass",
            "summary": "Reviewed the implementation.",
            "criteria": [
                {"text": "Handles empty input", "met": True, "note": "Covered"}
            ],
            "findings": [
                {
                    "severity": "low",
                    "text": "Name can be clearer.",
                    "location": "src/example.py:12",
                }
            ],
            "changed_files": [
                {"path": "src/example.py", "additions": 8, "deletions": 2}
            ],
            "changes": "Updated the implementation.",
            "validation_evidence": "pytest passed",
            "divergences": "None.",
            "skipped_scope": "None.",
            "blocker": "None.",
            "artifact_refs": [],
        }
    }

    extracted = extract_latest_stage_report(
        [{"type": "message_complete", "seq": 4, "text": json.dumps(payload)}]
    )

    assert extracted is not None
    report, seq = extracted
    assert seq == 4
    assert report is not None
    assert report.outcome == LoopOutcome.PASS
    assert report.finding_details[0].severity == LoopFindingSeverity.LOW
    assert report.criteria_coverage[0].met is True
    assert report.changed_files[0].path == "src/example.py"


def _report(**extra: object) -> object:
    """Extract a minimal stage report carrying the named extra fields."""
    payload = {
        "atelier_loop_step_report": {
            "outcome": "pass",
            "summary": "Pushed the pass.",
            **extra,
        }
    }
    extracted = extract_latest_stage_report(
        [{"type": "message_complete", "seq": 1, "text": json.dumps(payload)}]
    )
    assert extracted is not None
    report, _ = extracted
    assert report is not None
    return report


def test_a_report_carries_one_reply_per_pull_request_comment() -> None:
    report = _report(
        comment_replies=[
            {"comment_id": "c-1", "reply": "Guarded the empty branch."},
            {"comment_id": "c-2", "reply": "Left as is; the cast is load-bearing."},
        ]
    )

    assert [(item.comment_id, item.reply) for item in report.comment_replies] == [
        ("c-1", "Guarded the empty branch."),
        ("c-2", "Left as is; the cast is load-bearing."),
    ]


@pytest.mark.parametrize(
    "replies",
    [
        "not a list",
        [{"comment_id": "c-1"}],
        [{"reply": "orphan"}],
        [{"comment_id": "", "reply": "blank id"}],
        ["c-1"],
    ],
)
def test_an_unusable_comment_reply_is_dropped_rather_than_posted(
    replies: object,
) -> None:
    """These become public replies on someone's pull request. A half-formed
    entry has nothing to key to, so it is discarded, not guessed at."""
    report = _report(comment_replies=replies)

    assert report.comment_replies == ()


def test_a_report_without_comment_replies_carries_none() -> None:
    """Only the PR stage is asked for them; every other stage omits the key."""
    assert _report().comment_replies == ()
