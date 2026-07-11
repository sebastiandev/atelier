"""Public-interface tests for structured loop stage reports."""

import json

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
