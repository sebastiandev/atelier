"""Tests for the run account a stage is given."""

from typing import Any

import pytest

from src.domain.loop import feedback, history
from src.domain.loop.dtos import LoopHistoryLevel


def _loop(*reports: dict[str, Any], pass_number: int = 1) -> dict[str, Any]:
    """Build a run snapshot whose stages carry the given report occurrences."""
    rows: dict[str, dict[str, Any]] = {}
    for index, report in enumerate(reports):
        row = rows.setdefault(
            report["stage"], {"id": report["stage"], "name": report["stage"].title(), "reports": []}
        )
        row["reports"].append(
            {"seq": index, **{key: value for key, value in report.items() if key != "stage"}}
        )
    return {"pass_number": pass_number, "stages": list(rows.values())}


def test_no_history_renders_nothing() -> None:
    loop = _loop({"stage": "implementation", "outcome": "pass", "summary": "Did it."})

    assert history.lines(loop, LoopHistoryLevel.NONE) == ()


def test_a_summary_line_carries_the_stage_summary() -> None:
    loop = _loop(
        {
            "stage": "implementation",
            "pass_number": 4,
            "outcome": "pass",
            "summary": "Restored the N:M order validation and its command tests.",
        },
        pass_number=4,
    )

    assert history.lines(loop, LoopHistoryLevel.SUMMARIES) == (
        "pass 4 -- Implementation: Restored the N:M order validation and its command tests.",
    )


def test_changes_requested_carries_the_findings_not_a_count() -> None:
    """`changes requested (2 findings)` tells an agent nothing it can act on."""
    loop = _loop(
        {
            "stage": "review",
            "pass_number": 4,
            "outcome": "changes_requested",
            "summary": "Two problems.",
            "findings": ["Timeline op-type parity.", "Error-equivalence asserted only the class."],
        },
        pass_number=4,
    )

    line = history.lines(loop, LoopHistoryLevel.SUMMARIES)[0]

    assert "Timeline op-type parity" in line
    assert "Error-equivalence asserted only the class" in line
    assert "2 findings" not in line


@pytest.mark.parametrize("summary", ["", "   "])
def test_a_missing_summary_says_so_rather_than_rendering_a_bare_outcome(summary: str) -> None:
    loop = _loop(
        {"stage": "implementation", "pass_number": 1, "outcome": "pass", "summary": summary}
    )

    assert history.lines(loop, LoopHistoryLevel.SUMMARIES) == (
        "pass 1 -- Implementation: no summary reported",
    )


def test_full_history_keeps_the_findings_and_evidence() -> None:
    loop = _loop(
        {
            "stage": "review",
            "pass_number": 1,
            "outcome": "changes_requested",
            "summary": "Two problems.",
            "findings": ["Timeline parity."],
            "validation_evidence": "42 tests passed.",
        }
    )

    rendered = "\n".join(history.lines(loop, LoopHistoryLevel.FULL))

    assert "Two problems." in rendered
    assert "- Timeline parity." in rendered
    assert "evidence: 42 tests passed." in rendered


def test_only_answered_requests_appear_as_history() -> None:
    """An open request is an instruction. Rendering it here as well is what let
    a settled request keep reading as a live order."""
    loop = _loop(
        {"stage": "implementation", "pass_number": 1, "outcome": "pass", "summary": "Did it."}
    )
    feedback.record(loop, source="approval", note="Don't import kernel tables.", pass_number=1)
    feedback.record(
        loop, source="review", note="Still outstanding.", pass_number=1, answered_by="review"
    )
    loop["feedback"][0]["state"] = "answered"

    rendered = "\n".join(history.lines(loop, LoopHistoryLevel.SUMMARIES))

    assert "you asked: Don't import kernel tables.  (answered)" in rendered
    assert "Still outstanding." not in rendered


def test_passes_beyond_the_cap_collapse_to_one_line_each() -> None:
    """`full` grows without bound, and an agent handed forty passes verbatim
    reads none of them."""
    reports = [
        {
            "stage": "implementation",
            "pass_number": number,
            "outcome": "pass",
            "summary": f"Pass {number} work.",
        }
        for number in range(1, 7)
    ]
    loop = _loop(*reports, pass_number=6)

    lines = history.lines(loop, LoopHistoryLevel.FULL)

    # Passes 1-2 roll up; 3-6 stay, which is the cap of four.
    assert lines[0] == "pass 1 -- Implementation: pass"
    assert lines[1] == "pass 2 -- Implementation: pass"
    assert "Pass 1 work." not in "\n".join(lines)
    assert "Pass 6 work." in "\n".join(lines)


def test_events_render_oldest_first() -> None:
    loop = _loop(
        {"stage": "implementation", "pass_number": 1, "outcome": "pass", "summary": "First."},
        {"stage": "review", "pass_number": 1, "outcome": "pass", "summary": "Second."},
        pass_number=1,
    )

    lines = history.lines(loop, LoopHistoryLevel.SUMMARIES)

    assert [line.split(": ")[-1] for line in lines] == ["First.", "Second."]


def test_a_run_with_nothing_recorded_renders_nothing() -> None:
    assert history.lines({}, LoopHistoryLevel.SUMMARIES) == ()
    assert history.lines({"stages": "nonsense"}, LoopHistoryLevel.FULL) == ()


def test_an_occurrence_already_rendered_as_a_report_is_not_repeated() -> None:
    """One source per fact. A stage that declares the review's report should
    not also read the same occurrence as a history line."""
    loop = _loop(
        {"stage": "review", "pass_number": 1, "outcome": "pass", "summary": "Older pass."},
        {"stage": "review", "pass_number": 2, "outcome": "pass", "summary": "Newest pass."},
        pass_number=2,
    )

    lines = history.lines(loop, LoopHistoryLevel.SUMMARIES, frozenset({"review"}))

    assert "Older pass." in "\n".join(lines)
    assert "Newest pass." not in "\n".join(lines)
