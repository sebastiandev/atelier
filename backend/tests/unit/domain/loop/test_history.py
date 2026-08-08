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

    # Passes 1-2 roll up; 3-6 stay, which is the cap of four. A rolled-up pass
    # is still meant to be readable, so it keeps what the stage said.
    assert lines[0] == "pass 1 -- Implementation: pass -- Pass 1 work."
    assert lines[1] == "pass 2 -- Implementation: pass -- Pass 2 work."
    assert "\n".join(lines).count("Pass 6 work.") == 1


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


def test_changes_requested_keeps_the_reason_as_well_as_the_findings() -> None:
    """The findings say what is wrong; the summary says why. Trading one for
    the other is the loss this level exists to avoid."""
    loop = _loop(
        {
            "stage": "review",
            "pass_number": 1,
            "outcome": "changes_requested",
            "summary": "Two problems.",
            "findings": ["Timeline parity."],
        }
    )

    line = history.lines(loop, LoopHistoryLevel.SUMMARIES)[0]

    assert "changes requested" in line
    assert "Two problems." in line
    assert "Timeline parity" in line


def test_a_changes_requested_report_without_findings_still_says_so() -> None:
    """Otherwise it is indistinguishable from a stage that passed."""
    loop = _loop(
        {
            "stage": "review",
            "pass_number": 1,
            "outcome": "changes_requested",
            "summary": "See the inline notes.",
            "findings": [],
        }
    )

    assert history.lines(loop, LoopHistoryLevel.SUMMARIES) == (
        "pass 1 -- Review: changes requested -- See the inline notes.",
    )


def test_one_event_stays_one_line() -> None:
    loop = _loop(
        {
            "stage": "implementation",
            "pass_number": 1,
            "outcome": "pass",
            "summary": "Did it.\nThen did more.",
        }
    )

    assert history.lines(loop, LoopHistoryLevel.SUMMARIES) == (
        "pass 1 -- Implementation: Did it. Then did more.",
    )


def test_blank_findings_do_not_leave_a_dangling_clause() -> None:
    loop = _loop(
        {
            "stage": "review",
            "pass_number": 1,
            "outcome": "changes_requested",
            "summary": "Something.",
            "findings": ["  ", ""],
        }
    )

    assert history.lines(loop, LoopHistoryLevel.SUMMARIES) == (
        "pass 1 -- Review: changes requested -- Something.",
    )


def test_stage_order_follows_when_they_reported_not_their_transcripts() -> None:
    """`seq` counts one agent's transcript, so it cannot order two stages
    against each other -- the review below has the lower seq of the two."""
    loop = {
        "pass_number": 1,
        "stages": [
            {
                "id": "review",
                "name": "Review",
                "reports": [
                    {
                        "seq": 2,
                        "pass_number": 1,
                        "outcome": "pass",
                        "summary": "Second.",
                        "recorded_at": "2026-08-08T10:05:00+00:00",
                    }
                ],
            },
            {
                "id": "implementation",
                "name": "Implementation",
                "reports": [
                    {
                        "seq": 97,
                        "pass_number": 1,
                        "outcome": "pass",
                        "summary": "First.",
                        "recorded_at": "2026-08-08T10:00:00+00:00",
                    }
                ],
            },
        ],
    }

    lines = history.lines(loop, LoopHistoryLevel.SUMMARIES)

    assert [line.split(": ")[-1] for line in lines] == ["First.", "Second."]


def test_the_cap_still_applies_when_the_run_lost_its_pass_counter() -> None:
    """It has to fail closed: deriving the current pass only from the run
    would hand a long run every pass in full."""
    reports = [
        {"stage": "impl", "pass_number": number, "outcome": "pass", "summary": f"Pass {number}."}
        for number in range(1, 8)
    ]
    loop = _loop(*reports)
    loop.pop("pass_number")

    lines = history.lines(loop, LoopHistoryLevel.FULL)

    # Passes 1-3 rolled to one line each; 4-7 rendered in full.
    assert lines[0] == "pass 1 -- Impl: pass -- Pass 1."
    assert len(lines) == 7


def test_history_does_not_replay_findings_the_user_dismissed() -> None:
    """The report ledger keeps them as the record of what the reviewer said.
    Quoting them back as live account is the loop dismissal exists to break."""
    loop = _loop(
        {
            "stage": "review",
            "pass_number": 1,
            "outcome": "changes_requested",
            "summary": "Two problems.",
            "findings": ["Fix the race.", "Rename the fixture."],
        }
    )
    feedback.waive(loop, ["Rename the fixture."])

    line = history.lines(loop, LoopHistoryLevel.SUMMARIES)[0]

    assert "Fix the race" in line
    assert "Rename the fixture" not in line


def test_an_attempt_scoped_note_is_not_replayed_as_a_standing_request() -> None:
    loop = _loop(
        {"stage": "impl", "pass_number": 1, "outcome": "pass", "summary": "Did it."}
    )
    feedback.record(
        loop,
        source="retry",
        note="Use the running container.",
        pass_number=1,
        scope=feedback.SCOPE_ATTEMPT,
    )
    loop["feedback"][0]["state"] = "answered"

    assert "Use the running container." not in "\n".join(
        history.lines(loop, LoopHistoryLevel.SUMMARIES)
    )


def test_full_history_says_what_the_outcome_was() -> None:
    """Otherwise the higher level is the less informative of the two."""
    loop = _loop(
        {
            "stage": "review",
            "pass_number": 1,
            "outcome": "changes_requested",
            "summary": "Two problems.",
            "findings": ["Timeline parity."],
        }
    )

    assert "changes requested" in "\n".join(history.lines(loop, LoopHistoryLevel.FULL))
