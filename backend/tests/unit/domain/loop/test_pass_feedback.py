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


def test_context_ignores_feedback_from_an_earlier_pass() -> None:
    run = _run(pass_number=3, stored={"pass_number": 2, "note": "Stale request."})

    assert pass_feedback.context(run) == ""


def test_context_is_empty_without_stored_feedback() -> None:
    assert pass_feedback.context(_run()) == ""
