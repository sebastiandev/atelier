"""Seeded re-entry into a finished loop run.

A follow-up is a fresh run that starts partway through the loop and keeps
the previous run's branch and worktree:

- ``AMEND`` re-enters the first task stage carrying a required note, for
  "the reviewer asked for X".
- ``VERIFY`` starts at the first review or check and skips the task
  stages, for "I edited files by hand, re-run the checks over them".

None of this depends on what triggered the run, so it lives here rather
than in either command module.
"""

from __future__ import annotations

from dataclasses import replace

from src.domain.loop.dtos import (
    LoopBrief,
    LoopDefinition,
    LoopRunKind,
    LoopStageBrief,
    LoopStatus,
    LoopStepKind,
)

REUSABLE_STATUSES = frozenset(
    {LoopStatus.ACCEPTED, LoopStatus.CANCELLED, LoopStatus.FAILED}
)

_SEED_LABELS = {LoopRunKind.AMEND: "feedback", LoopRunKind.VERIFY: "manual edits"}


class FollowUpNotAvailable(ValueError):
    """The run or its loop cannot support the requested follow-up."""


def require_reusable(status: LoopStatus, run_id: str) -> None:
    """Reject a follow-up from a run that has not finished.

    Preconditions: ``status`` is the source run's loop status.
    Postconditions: raises unless the run has reached a terminal state.
    """
    if status not in REUSABLE_STATUSES:
        raise FollowUpNotAvailable(f"loop run cannot be reused: {run_id}")


def task_stage_id(definition: LoopDefinition) -> str:
    """Return the stage an ``AMEND`` follow-up re-enters."""
    stage = next(
        (item for item in definition.stages if item.kind == LoopStepKind.AGENT_TASK),
        None,
    )
    if stage is None:
        raise FollowUpNotAvailable("amend follow-up requires an implementation stage")
    return stage.step_id


def review_stage_id(definition: LoopDefinition) -> str:
    """Return the stage a ``VERIFY`` follow-up starts from."""
    stage = next(
        (
            item
            for item in definition.stages
            if item.kind in {LoopStepKind.AGENT_REVIEW, LoopStepKind.DETERMINISTIC_CHECK}
        ),
        None,
    )
    if stage is None:
        raise FollowUpNotAvailable("verify follow-up requires a review or check stage")
    return stage.step_id


def entry_stage_id(definition: LoopDefinition, kind: LoopRunKind) -> str | None:
    """Return where a follow-up enters the loop.

    Preconditions: ``kind`` describes the requested follow-up.
    Postconditions: ``None`` for an initial run, meaning the loop's own
    first stage; otherwise the id of the stage to start at. ``AMEND``
    returns ``None`` because it re-enters the task stage through the
    brief, not by skipping ahead.
    """
    if kind is LoopRunKind.VERIFY:
        return review_stage_id(definition)
    return None


def seed_label(kind: LoopRunKind) -> str:
    """Return the run-history label for a follow-up kind."""
    return _SEED_LABELS.get(kind, "")


def amend_brief(brief: LoopBrief, note: str, stage_id: str) -> LoopBrief:
    """Append required follow-up feedback to the task stage's note.

    Preconditions: ``note`` is the user's non-blank instruction.
    Postconditions: the returned brief carries the feedback on
    ``stage_id``, appended after any existing note rather than replacing
    it -- the original brief still describes the work.
    """
    feedback = note.strip()
    if not feedback:
        raise FollowUpNotAvailable("amend follow-up runs require a brief note")
    existing = next((item for item in brief.stages if item.stage_id == stage_id), None)
    if existing is None:
        return replace(
            brief,
            stages=(*brief.stages, LoopStageBrief(stage_id=stage_id, note=feedback)),
        )
    updated = replace(
        existing,
        note="\n\n".join(part for part in (existing.note.strip(), feedback) if part),
    )
    return replace(
        brief,
        stages=tuple(
            updated if item.stage_id == stage_id else item for item in brief.stages
        ),
    )


def seeded_brief(
    brief: LoopBrief, definition: LoopDefinition, kind: LoopRunKind, note: str
) -> LoopBrief:
    """Return the brief a follow-up run should start from."""
    if kind is LoopRunKind.AMEND:
        return amend_brief(brief, note, task_stage_id(definition))
    return brief


__all__ = [
    "REUSABLE_STATUSES",
    "FollowUpNotAvailable",
    "amend_brief",
    "entry_stage_id",
    "require_reusable",
    "review_stage_id",
    "seed_label",
    "seeded_brief",
    "task_stage_id",
]
