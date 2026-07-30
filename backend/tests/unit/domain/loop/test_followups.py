"""Follow-up seeding is target-agnostic: it depends on the loop, not the trigger."""

import pytest

from src.domain.loop import followups
from src.domain.loop.dtos import (
    LoopBrief,
    LoopDefinition,
    LoopReportSchema,
    LoopRunKind,
    LoopStageBrief,
    LoopStatus,
    LoopStepDefinition,
    LoopStepKind,
)


def _definition(*kinds: str) -> LoopDefinition:
    return LoopDefinition(
        definition_id="d",
        name="D",
        trigger="artifact_or_objective",
        report_schema=LoopReportSchema(schema_id="generic", fields=()),
        stages=tuple(
            LoopStepDefinition(step_id=f"s{i}", name=f"S{i}", kind=LoopStepKind(kind))
            for i, kind in enumerate(kinds)
        ),
    )


@pytest.mark.parametrize(
    "status", [LoopStatus.ACCEPTED, LoopStatus.CANCELLED, LoopStatus.FAILED]
)
def test_a_finished_run_can_be_reused(status: LoopStatus) -> None:
    followups.require_reusable(status, "run-001")


@pytest.mark.parametrize("status", [LoopStatus.RUNNING, LoopStatus.AWAITING_APPROVAL])
def test_an_unfinished_run_cannot(status: LoopStatus) -> None:
    with pytest.raises(followups.FollowUpNotAvailable, match="run-001"):
        followups.require_reusable(status, "run-001")


def test_verify_enters_at_the_first_check() -> None:
    definition = _definition("agent_task", "deterministic_check", "agent_review")

    assert followups.entry_stage_id(definition, LoopRunKind.VERIFY) == "s1"


def test_amend_enters_through_the_brief_not_by_skipping() -> None:
    """Amend re-runs the task stage, so the loop starts where it always does."""
    definition = _definition("agent_task", "agent_review")

    assert followups.entry_stage_id(definition, LoopRunKind.AMEND) is None


def test_verify_needs_something_to_verify_with() -> None:
    definition = _definition("agent_task")

    with pytest.raises(followups.FollowUpNotAvailable, match="review or check"):
        followups.entry_stage_id(definition, LoopRunKind.VERIFY)


def test_amend_needs_an_implementation_stage() -> None:
    definition = _definition("agent_review")

    with pytest.raises(followups.FollowUpNotAvailable, match="implementation"):
        followups.seeded_brief(LoopBrief(goal="g"), definition, LoopRunKind.AMEND, "fix")


def test_amend_requires_a_note() -> None:
    definition = _definition("agent_task")

    with pytest.raises(followups.FollowUpNotAvailable, match="note"):
        followups.seeded_brief(LoopBrief(goal="g"), definition, LoopRunKind.AMEND, "   ")


def test_amend_appends_to_an_existing_note_rather_than_replacing_it() -> None:
    definition = _definition("agent_task")
    brief = LoopBrief(
        goal="g", stages=(LoopStageBrief(stage_id="s0", note="original scope"),)
    )

    seeded = followups.seeded_brief(brief, definition, LoopRunKind.AMEND, "also fix Y")

    assert seeded.stages[0].note == "original scope\n\nalso fix Y"


def test_amend_adds_a_note_when_the_stage_had_none() -> None:
    definition = _definition("agent_task")

    seeded = followups.seeded_brief(
        LoopBrief(goal="g"), definition, LoopRunKind.AMEND, "fix Y"
    )

    assert [(s.stage_id, s.note) for s in seeded.stages] == [("s0", "fix Y")]


def test_verify_leaves_the_brief_untouched() -> None:
    definition = _definition("agent_task", "agent_review")
    brief = LoopBrief(goal="g", stages=(LoopStageBrief(stage_id="s0", note="scope"),))

    assert followups.seeded_brief(brief, definition, LoopRunKind.VERIFY, "") == brief


@pytest.mark.parametrize(
    ("kind", "label"),
    [
        (LoopRunKind.AMEND, "feedback"),
        (LoopRunKind.VERIFY, "manual edits"),
        (LoopRunKind.INITIAL, ""),
    ],
)
def test_seed_labels(kind: LoopRunKind, label: str) -> None:
    assert followups.seed_label(kind) == label
