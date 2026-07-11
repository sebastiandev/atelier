"""State-independent transition rules for ordered loop stages."""

from __future__ import annotations

from src.domain.loop.dtos import LoopDefinition, LoopOutcome, LoopStepDefinition


class LoopTransitionInvalid(ValueError):
    """A stage report cannot follow the configured transition."""


def stage_by_id(definition: LoopDefinition, step_id: str) -> LoopStepDefinition:
    """Return one stage from a snapshotted definition.

    Preconditions: ``step_id`` belongs to the selected run definition.
    Postconditions: no definition state is changed.
    """
    stage = next((item for item in definition.stages if item.step_id == step_id), None)
    if stage is None:
        raise LoopTransitionInvalid(f"loop stage not found: {step_id}")
    return stage


def transition_destination(
    definition: LoopDefinition,
    current_step_id: str,
    outcome: LoopOutcome,
) -> str:
    """Resolve one configured stage outcome.

    Preconditions: current stage and outcome are legal for the snapshot.
    Postconditions: returns a stage id or supported terminal destination.
    """
    stage = stage_by_id(definition, current_step_id)
    destination = stage.transitions.get(outcome)
    if not destination:
        raise LoopTransitionInvalid(
            f"stage {current_step_id!r} has no {outcome.value!r} transition"
        )
    return destination


__all__ = ["LoopTransitionInvalid", "stage_by_id", "transition_destination"]
