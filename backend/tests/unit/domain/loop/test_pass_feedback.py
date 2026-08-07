"""Requested-changes feedback survives beyond the stage it was aimed at."""

from __future__ import annotations

import pytest

from src.domain.loop import pass_feedback


def _run(pass_number: int = 2, stored: list | None = None) -> dict:
    loop: dict = {"pass_number": pass_number}
    if stored is not None:
        loop["pass_feedback"] = stored
    return {"loop": loop}


def test_record_stores_the_note_for_its_pass() -> None:
    loop: dict = {"pass_number": 2}

    pass_feedback.record(loop, note="  Drop the SELECT FOR UPDATE.  ", pass_number=2)

    assert loop["pass_feedback"] == [{"pass_number": 2, "note": "Drop the SELECT FOR UPDATE."}]


def test_a_second_request_joins_the_first_instead_of_replacing_it() -> None:
    """Both are in force: the reviewer needs the reason the code changed *and*
    the operational note, not whichever was typed last."""
    loop: dict = {"pass_number": 3}

    pass_feedback.record(loop, note="Don't import kernel tables from app tests.", pass_number=3)
    pass_feedback.record(loop, note="Run against whatever container is up.", pass_number=4)

    assert loop["pass_feedback"] == [
        {"pass_number": 3, "note": "Don't import kernel tables from app tests."},
        {"pass_number": 4, "note": "Run against whatever container is up."},
    ]


def test_the_same_request_recorded_twice_is_stored_once() -> None:
    """A retry replays the note it was launched with; it is still one request."""
    loop: dict = {"pass_number": 2}

    pass_feedback.record(loop, note="Keep the lot update.", pass_number=2)
    pass_feedback.record(loop, note="  Keep the lot update.  ", pass_number=3)

    assert loop["pass_feedback"] == [{"pass_number": 2, "note": "Keep the lot update."}]


@pytest.mark.parametrize("note", ["", "   ", "\n\n"])
def test_a_blank_note_records_nothing_and_leaves_earlier_requests_standing(note: str) -> None:
    loop: dict = {"pass_number": 2, "pass_feedback": [{"pass_number": 1, "note": "old"}]}

    pass_feedback.record(loop, note=note, pass_number=2)

    assert loop["pass_feedback"] == [{"pass_number": 1, "note": "old"}]


def test_context_renders_the_current_pass_note() -> None:
    run = _run(stored=[{"pass_number": 2, "note": "Drop the SELECT FOR UPDATE."}])

    assert "Drop the SELECT FOR UPDATE." in pass_feedback.context(run)


def test_context_renders_every_outstanding_request_as_a_list() -> None:
    run = _run(
        pass_number=4,
        stored=[
            {"pass_number": 3, "note": "Don't import kernel tables from app tests."},
            {"pass_number": 4, "note": "Run against whatever container is up."},
        ],
    )

    rendered = pass_feedback.context(run)

    assert "- Don't import kernel tables from app tests." in rendered
    assert "- Run against whatever container is up." in rendered


def test_context_leaves_a_lone_request_unbulleted() -> None:
    run = _run(stored=[{"pass_number": 2, "note": "Keep the lot update."}])

    assert pass_feedback.context(run).endswith("still in force:\nKeep the lot update.")


@pytest.mark.parametrize("current", [2, 3, 7])
def test_context_survives_the_passes_a_review_return_edge_adds(current: int) -> None:
    """A review returning changes_requested advances the counter; the user's
    request is still the thing being worked on."""
    run = _run(pass_number=current, stored=[{"pass_number": 2, "note": "Keep the lot update."}])

    assert "Keep the lot update." in pass_feedback.context(run)


def test_context_ignores_a_request_recorded_for_a_later_pass() -> None:
    run = _run(pass_number=2, stored=[{"pass_number": 5, "note": "Rewound counter."}])

    assert pass_feedback.context(run) == ""


def test_context_keeps_the_readable_requests_when_one_entry_is_malformed() -> None:
    run = _run(pass_number=2, stored=["nonsense", {"pass_number": 2, "note": "Keep this."}])

    assert "Keep this." in pass_feedback.context(run)


def test_clear_drops_stored_feedback() -> None:
    loop: dict = {"pass_number": 2, "pass_feedback": [{"pass_number": 2, "note": "Done with."}]}

    pass_feedback.clear(loop)

    assert "pass_feedback" not in loop


def test_context_is_empty_without_stored_feedback() -> None:
    assert pass_feedback.context(_run()) == ""
