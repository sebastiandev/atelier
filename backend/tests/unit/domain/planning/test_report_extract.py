"""Tests for planning loop report extraction."""

from __future__ import annotations

import json

from src.domain.planning.report_extract import extract_latest_report


def test_extracts_latest_loop_json_report() -> None:
    report = {
        "atelier_loop_report": {
            "summary": "Done.",
            "divergences": "None.",
            "skipped_scope": "None.",
            "blockers": "None.",
            "decisions": "Kept the plan.",
            "changes": "Implemented it.",
            "validation_evidence": "pytest passed",
            "proposed_source": "# Updated story\n",
        }
    }

    extracted = extract_latest_report(
        [
            {"type": "message_complete", "seq": 1, "text": "Earlier message"},
            {"type": "message_complete", "seq": 2, "text": json.dumps(report)},
        ]
    )

    assert extracted is not None
    fields, seq = extracted
    assert seq == 2
    assert fields["summary"] == "Done."
    assert fields["validation_evidence"] == "pytest passed"
    assert fields["proposed_source"] == "# Updated story"


def test_missing_loop_json_report_counts_as_incomplete_attempt() -> None:
    extracted = extract_latest_report(
        [{"type": "message_complete", "seq": 3, "text": "I am done."}]
    )

    assert extracted is not None
    fields, seq = extracted
    assert seq == 3
    assert fields["summary"] == ""
    assert fields["validation_evidence"] == ""
