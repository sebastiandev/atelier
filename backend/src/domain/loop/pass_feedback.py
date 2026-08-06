"""Durable user feedback for the stages of one loop pass.

When a user requests changes from the approval stage, the note reaches the
destination write stage through that stage's own prompt. Every later stage in
the same pass -- checks, corrective task stages, and above all the review --
is built fresh from the brief and the preceding stage report, so without a
durable copy the note is gone by the time anything verifies the work.

This mirrors ``pr_lifecycle``'s ``pending_pr_feedback``: the note is stored on
the loop snapshot, scoped to the pass it belongs to, and rendered into later
prompts until the pass advances.
"""

from __future__ import annotations

from typing import Any

from src.domain.loop import actions


def record(loop: dict[str, Any], *, note: str, pass_number: int) -> None:
    """Persist requested-changes feedback for ``pass_number``.

    Preconditions: ``loop`` is a mutable loop-run snapshot and ``pass_number``
    is the pass the feedback applies to, already advanced by the caller.
    Postconditions: a blank note clears any stored feedback rather than
    persisting an empty block; otherwise the note replaces it.
    """
    if not note.strip():
        loop.pop("pass_feedback", None)
        return
    loop["pass_feedback"] = {"pass_number": pass_number, "note": note.strip()}


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
    changes_requested advances the counter, so the pass the user wrote
    against is not the pass still working on the answer.

    Preconditions: ``run`` is a loop-run snapshot.
    Postconditions: empty when nothing is stored, or when the stored request
    belongs to a pass later than the current one, which only happens if a
    counter was rewound.
    """
    loop = actions.dict_or_empty(run.get("loop"))
    stored = actions.dict_or_empty(loop.get("pass_feedback"))
    note = actions.str_or_empty(stored.get("note"))
    if not note:
        return ""
    recorded = actions.int_or_default(stored.get("pass_number"), 0)
    if recorded > max(1, actions.int_or_default(loop.get("pass_number"), 1)):
        return ""
    return f"Changes requested by the user, still in force:\n{note}"


__all__ = ["clear", "context", "record"]
