"""Durable user feedback for the stages of one loop run.

When a user requests changes from an approval stage or sends a review back, the
note reaches the destination write stage through that stage's own prompt. Every
later stage -- checks, corrective task stages, and above all the review -- is
built fresh from the brief and the preceding stage report, so without a durable
copy the note is gone by the time anything verifies the work.

Requests accumulate. One run commonly collects several: a substantive one about
the code, then an operational one about how to run the tests. Keeping only the
newest made the reviewer read an implementation shaped by an instruction it was
never shown. They all stay in force until the run is accepted.

This mirrors ``pr_lifecycle``'s ``pending_pr_feedback``: the notes are stored on
the loop snapshot, scoped to the pass that carried each one, and rendered into
later prompts.
"""

from __future__ import annotations

from typing import Any

from src.domain.loop import actions


def record(loop: dict[str, Any], *, note: str, pass_number: int) -> None:
    """Add requested-changes feedback recorded during ``pass_number``.

    Preconditions: ``loop`` is a mutable loop-run snapshot and ``pass_number``
    is the pass the feedback applies to, already advanced by the caller.
    Postconditions: a blank note stores nothing and leaves earlier requests
    alone -- only accepting the run answers those. A note already stored is not
    stored twice, so a retry that replays the same request renders it once.
    """
    text = note.strip()
    if not text:
        return
    stored = _stored(loop)
    if any(item["note"] == text for item in stored):
        return
    loop["pass_feedback"] = [*stored, {"pass_number": pass_number, "note": text}]


def clear(loop: dict[str, Any]) -> None:
    """Drop stored feedback once the run no longer owes an answer to it.

    Preconditions: ``loop`` is a mutable loop-run snapshot.
    Postconditions: no feedback is stored.
    """
    loop.pop("pass_feedback", None)


def context(run: dict[str, Any]) -> str:
    """Return the outstanding requested changes for a stage prompt.

    A request stays in force from the pass that carried it until the run is
    accepted. Scoping it to one exact pass was wrong: a review returning
    changes_requested advances the counter, so the pass the user wrote against
    is not the pass still working on the answer.

    Preconditions: ``run`` is a loop-run snapshot.
    Postconditions: empty when nothing is stored; otherwise every request in the
    order it was made, skipping any belonging to a pass later than the current
    one, which only happens if a counter was rewound.
    """
    loop = actions.dict_or_empty(run.get("loop"))
    current = max(1, actions.int_or_default(loop.get("pass_number"), 1))
    notes = [
        item["note"]
        for item in _stored(loop)
        if actions.int_or_default(item.get("pass_number"), 0) <= current
    ]
    if not notes:
        return ""
    # One request reads as a sentence; several read as a list, so the stage can
    # tell where one ends and the next begins.
    body = notes[0] if len(notes) == 1 else "\n\n".join(f"- {note}" for note in notes)
    return f"Changes requested by the user, still in force:\n{body}"


def _stored(loop: dict[str, Any]) -> list[dict[str, Any]]:
    """Return the recorded requests, oldest first, normalised.

    Malformed entries are dropped rather than raised on: the snapshot is data
    the run has to keep moving through, and a request nobody can read is not
    worth failing a stage over.
    """
    value = loop.get("pass_feedback")
    if not isinstance(value, list):
        return []
    rows = []
    for item in value:
        if not isinstance(item, dict):
            continue
        note = actions.str_or_empty(item.get("note")).strip()
        if note:
            rows.append(
                {"pass_number": actions.int_or_default(item.get("pass_number"), 0), "note": note}
            )
    return rows


__all__ = ["clear", "context", "record"]
