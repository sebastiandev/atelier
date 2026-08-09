"""Tests for the unified loop feedback record store."""

from typing import Any

import pytest

from src.domain.loop import feedback


def _run(loop: dict[str, Any] | None = None) -> dict[str, Any]:
    return {"loop": {"pass_number": 2, **(loop or {})}}


def test_a_request_is_stored_open_against_its_pass() -> None:
    loop: dict[str, Any] = {}

    entry = feedback.record(
        loop,
        source="approval",
        note="  Drop the SELECT FOR UPDATE.  ",
        pass_number=2,
        answered_by="approve",
    )

    assert entry is not None
    assert loop["feedback"] == [entry]
    assert entry["note"] == "Drop the SELECT FOR UPDATE."
    assert entry["state"] == "open"
    assert entry["pass_number"] == 2
    assert entry["answered_by"] == "approve"


def test_requests_accumulate_rather_than_replacing_each_other() -> None:
    loop: dict[str, Any] = {}

    feedback.record(loop, source="approval", note="Don't import kernel tables.", pass_number=3)
    feedback.record(loop, source="review", note="Run against whatever is up.", pass_number=4)

    assert [entry["note"] for entry in feedback.records(loop)] == [
        "Don't import kernel tables.",
        "Run against whatever is up.",
    ]


def test_replaying_the_same_request_within_a_pass_stores_it_once() -> None:
    loop: dict[str, Any] = {}

    first = feedback.record(loop, source="approval", note="Keep the lot update.", pass_number=2)
    again = feedback.record(loop, source="approval", note="  Keep the lot update.  ", pass_number=2)

    assert again is first
    assert len(feedback.records(loop)) == 1


def test_the_same_request_in_a_later_pass_is_a_new_one() -> None:
    """Re-sending an identical request is the user saying the fix did not land.
    Collapsing it into the earlier record loses it: that record has already
    been carried by a push, and no later push will pick it up again."""
    loop: dict[str, Any] = {}

    feedback.record(loop, source="pr_comment", note="Drop the lock.", pass_number=2)
    feedback.record(loop, source="pr_comment", note="Drop the lock.", pass_number=3)

    assert [entry["pass_number"] for entry in feedback.records(loop)] == [2, 3]


def test_reading_a_run_without_feedback_leaves_its_snapshot_untouched() -> None:
    """A query that writes would add `feedback: []` to every legacy run the
    first time a prompt is built, so the snapshot stops round-tripping."""
    loop: dict[str, Any] = {"pass_number": 1}

    feedback.context({"loop": loop})
    feedback.records(loop)
    feedback.dismissed(loop)

    assert loop == {"pass_number": 1}


@pytest.mark.parametrize("note", ["", "   "])
def test_a_request_with_nothing_to_render_is_not_stored(note: str) -> None:
    loop: dict[str, Any] = {}

    assert feedback.record(loop, source="approval", note=note, pass_number=2) is None
    assert feedback.records(loop) == []


def test_an_unknown_source_is_a_programming_error() -> None:
    with pytest.raises(ValueError, match="unknown feedback source"):
        feedback.record({}, source="hunch", note="Something.", pass_number=1)


def test_only_the_named_stage_answers_its_own_requests() -> None:
    loop: dict[str, Any] = {}
    feedback.record(loop, source="review", note="Fix parity.", pass_number=2, answered_by="review")
    feedback.record(
        loop, source="approval", note="Rename it.", pass_number=2, answered_by="approve"
    )

    settled = feedback.answer(loop, "review")

    assert [entry["note"] for entry in settled] == ["Fix parity."]
    assert [entry["state"] for entry in feedback.records(loop)] == ["answered", "open"]


def test_answered_requests_stay_as_history() -> None:
    loop: dict[str, Any] = {}
    feedback.record(loop, source="review", note="Fix parity.", pass_number=2, answered_by="review")

    feedback.answer(loop, "review")
    feedback.answer(loop, "review")

    assert len(feedback.records(loop)) == 1
    assert feedback.records(loop)[0]["answered_at"]


def test_accepting_answers_whatever_stage_would_have_verified_it() -> None:
    loop: dict[str, Any] = {}
    feedback.record(
        loop, source="pr_comment", note="Cover retries.", pass_number=2, answered_by="review"
    )
    feedback.record(
        loop, source="approval", note="Rename it.", pass_number=2, answered_by="approve"
    )

    feedback.answer_all(loop)

    assert {entry["state"] for entry in feedback.records(loop)} == {"answered"}


def test_an_attempt_scoped_request_ends_with_the_attempt() -> None:
    loop: dict[str, Any] = {}
    feedback.record(
        loop,
        source="retry",
        note="Use the running container.",
        pass_number=2,
        answered_by="implement",
        scope=feedback.SCOPE_ATTEMPT,
    )

    feedback.close_attempt(loop, "implement")

    assert feedback.records(loop)[0]["state"] == "answered"


def test_only_open_run_scoped_requests_reach_the_prompt() -> None:
    loop: dict[str, Any] = {"pass_number": 2}
    feedback.record(loop, source="approval", note="Still in force.", pass_number=1)
    feedback.record(
        loop,
        source="retry",
        note="Only for that attempt.",
        pass_number=2,
        scope=feedback.SCOPE_ATTEMPT,
    )
    answered = feedback.record(
        loop, source="review", note="Already settled.", pass_number=2, answered_by="review"
    )
    assert answered is not None
    feedback.answer(loop, "review")

    rendered = feedback.context({"loop": loop})

    assert "Still in force." in rendered
    assert "Only for that attempt." not in rendered
    assert "Already settled." not in rendered


def test_a_request_from_an_earlier_pass_stays_in_force() -> None:
    loop: dict[str, Any] = {"pass_number": 5}
    feedback.record(loop, source="approval", note="Written against pass 2.", pass_number=2)

    assert "Written against pass 2." in feedback.context({"loop": loop})


def test_a_request_from_a_later_pass_is_skipped() -> None:
    loop: dict[str, Any] = {"pass_number": 2}
    feedback.record(loop, source="approval", note="From a rewound counter.", pass_number=6)

    assert feedback.context({"loop": loop}) == ""


def test_the_newest_request_is_rendered_first() -> None:
    loop: dict[str, Any] = {"pass_number": 4}
    feedback.record(loop, source="approval", note="Older.", pass_number=2)
    feedback.record(loop, source="approval", note="Newer.", pass_number=4)

    rendered = feedback.context({"loop": loop})

    assert rendered.index("Newer.") < rendered.index("Older.")


def test_comment_items_render_with_their_author_location_and_thread() -> None:
    loop: dict[str, Any] = {"pass_number": 1}
    feedback.record(
        loop,
        source="pr_comment",
        pass_number=1,
        items=(
            {
                "ref": "comment-1",
                "author": "reviewer",
                "location": "src/app.py:4",
                "body": "Reduce the number of queries.",
                "instruction": "Keep the public API unchanged.",
                "thread": [
                    {"author": "reviewer", "body": "Reduce the number of queries."},
                    {"author": "seba", "body": "Intentional."},
                ],
            },
        ),
        note="Add focused validation.",
    )

    rendered = feedback.context({"loop": loop})

    assert "reviewer at src/app.py:4: Reduce the number of queries." in rendered
    assert "Keep the public API unchanged." in rendered
    assert "- seba: Intentional." in rendered
    assert "- User instruction: Add focused validation." in rendered


def test_a_run_without_feedback_renders_nothing() -> None:
    assert feedback.context(_run()) == ""
    assert feedback.records({}) == []


def test_unreadable_records_are_dropped_rather_than_raised_on() -> None:
    loop: dict[str, Any] = {"feedback": ["nonsense", {"source": "unknown"}, {"note": "no source"}]}

    assert feedback.records(loop) == []


def test_source_specific_bookkeeping_rides_along_on_the_record() -> None:
    loop: dict[str, Any] = {}
    entry = feedback.record(loop, source="pr_comment", note="Cover retries.", pass_number=2)
    assert entry is not None

    feedback.update(loop, entry["id"], pushed_in_pass=3)

    assert feedback.records(loop)[0]["pushed_in_pass"] == 3
    assert feedback.from_sources(loop, frozenset({"pr_comment"}))[0]["pushed_in_pass"] == 3


def test_dismissed_findings_are_recorded_once_in_the_order_first_waived() -> None:
    loop: dict[str, Any] = {}

    feedback.waive(loop, ["Kernel unnest writes UNNEST types.", "  "])
    feedback.waive(loop, ["Kernel unnest writes UNNEST types.", "Timeline parity."])

    assert feedback.dismissed(loop) == [
        "Kernel unnest writes UNNEST types.",
        "Timeline parity.",
    ]
