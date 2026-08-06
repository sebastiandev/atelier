"""Requested-changes feedback survives beyond the stage it was aimed at."""

from __future__ import annotations

import pytest

from src.domain.loop import pass_feedback


def _run(pass_number: int = 2, stored: dict | None = None) -> dict:
    loop: dict = {"pass_number": pass_number}
    if stored is not None:
        loop["pass_feedback"] = stored
    return {"loop": loop}


def test_record_stores_the_note_for_its_pass() -> None:
    loop: dict = {"pass_number": 2}

    pass_feedback.record(loop, note="  Drop the SELECT FOR UPDATE.  ", pass_number=2)

    assert loop["pass_feedback"] == {
        "pass_number": 2,
        "note": "Drop the SELECT FOR UPDATE.",
    }


@pytest.mark.parametrize("note", ["", "   ", "\n\n"])
def test_record_clears_stored_feedback_for_a_blank_note(note: str) -> None:
    loop: dict = {"pass_number": 2, "pass_feedback": {"pass_number": 1, "note": "old"}}

    pass_feedback.record(loop, note=note, pass_number=2)

    assert "pass_feedback" not in loop


def test_context_renders_the_current_pass_note() -> None:
    run = _run(stored={"pass_number": 2, "note": "Drop the SELECT FOR UPDATE."})

    assert "Drop the SELECT FOR UPDATE." in pass_feedback.context(run)


@pytest.mark.parametrize("current", [2, 3, 7])
def test_context_survives_the_passes_a_review_return_edge_adds(current: int) -> None:
    """A review returning changes_requested advances the counter; the user's
    request is still the thing being worked on."""
    run = _run(pass_number=current, stored={"pass_number": 2, "note": "Keep the lot update."})

    assert "Keep the lot update." in pass_feedback.context(run)


def test_context_ignores_a_request_recorded_for_a_later_pass() -> None:
    run = _run(pass_number=2, stored={"pass_number": 5, "note": "Rewound counter."})

    assert pass_feedback.context(run) == ""


def test_clear_drops_stored_feedback() -> None:
    loop: dict = {"pass_number": 2, "pass_feedback": {"pass_number": 2, "note": "Done with."}}

    pass_feedback.clear(loop)

    assert "pass_feedback" not in loop


def test_context_is_empty_without_stored_feedback() -> None:
    assert pass_feedback.context(_run()) == ""
