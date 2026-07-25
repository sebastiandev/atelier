"""Validation and JSON snapshots for Work-owned loop briefs."""

from __future__ import annotations

from typing import Any

from src.domain.loop.dtos import (
    LoopBrief,
    LoopBriefAgent,
    LoopBriefContext,
    LoopBriefContextKind,
    LoopDefinition,
    LoopReviewGate,
    LoopReviewGateMode,
    LoopStageBrief,
    LoopStepDefinition,
    LoopStepKind,
)


def validate_brief(definition: LoopDefinition, brief: LoopBrief) -> None:
    """Validate task input against one selected loop definition.

    Preconditions: ``definition`` is the revision selected for the run.
    Postconditions: every supplied stage exists, is agent-backed, and every
    required note slot is filled; otherwise ``ValueError`` is raised.
    """
    if not brief.goal.strip():
        raise ValueError("loop brief goal is required")
    stages = {stage.step_id: stage for stage in definition.stages}
    supplied: dict[str, LoopStageBrief] = {}
    for item in brief.stages:
        if item.stage_id in supplied:
            raise ValueError(f"loop brief repeats stage: {item.stage_id}")
        stage = stages.get(item.stage_id)
        if stage is None:
            raise ValueError(f"loop brief references unknown stage: {item.stage_id}")
        if stage.kind not in {LoopStepKind.AGENT_TASK, LoopStepKind.AGENT_REVIEW}:
            raise ValueError(f"loop brief stage does not accept input: {item.stage_id}")
        if any(not context.value.strip() for context in item.context):
            raise ValueError(f"loop brief context is empty: {item.stage_id}")
        if item.approved_command_prefixes is not None and any(
            not prefix.strip() or "\n" in prefix or "\r" in prefix
            for prefix in item.approved_command_prefixes
        ):
            raise ValueError(
                f"loop brief command prefix is invalid: {item.stage_id}"
            )
        if item.review_gate is not None:
            if stage.kind != LoopStepKind.AGENT_REVIEW or stage.review_gate is None:
                raise ValueError(f"loop brief stage has no review gate: {item.stage_id}")
            if stage.review_gate.locked and item.review_gate != stage.review_gate.mode:
                raise ValueError(f"loop review gate is locked: {item.stage_id}")
        supplied[item.stage_id] = item
    missing = [
        stage.name
        for stage in definition.stages
        if stage.note_required is True
        and not supplied.get(stage.step_id, LoopStageBrief(stage.step_id)).note.strip()
    ]
    if missing:
        raise ValueError("Required loop brief is missing for: " + ", ".join(missing))


def stage_brief(brief: LoopBrief | None, stage_id: str) -> LoopStageBrief | None:
    """Return one stage's brief without changing the pinned snapshot."""
    if brief is None:
        return None
    return next((item for item in brief.stages if item.stage_id == stage_id), None)


def with_legacy_required_defaults(
    definition: LoopDefinition,
    brief: LoopBrief,
) -> LoopBrief:
    """Fill newly declared builtin slots for clients predating brief support."""
    supplied = {stage.stage_id for stage in brief.stages}
    additions = tuple(
        LoopStageBrief(stage_id=stage.step_id, note=brief.goal)
        for stage in definition.stages
        if stage.note_required is True and stage.step_id not in supplied
    )
    return LoopBrief(goal=brief.goal, stages=(*brief.stages, *additions))


def prompt_values(brief: LoopBrief | None, stage_id: str) -> tuple[str, tuple[str, ...]]:
    """Return provider-facing note and labelled adhoc context for one stage."""
    item = stage_brief(brief, stage_id)
    if item is None:
        return "", ()
    return item.note, tuple(
        f"{context.kind.value}: {context.value}" for context in item.context
    )


def resolved_review_gate(
    stage: LoopStepDefinition,
    brief: LoopBrief | None,
) -> tuple[LoopReviewGate | None, str]:
    """Resolve a review return edge's pinned definition and run override.

    Preconditions: ``stage`` comes from the run's pinned definition.
    Postconditions: locked definition values win; otherwise a supplied mode
    overrides only the mode and the source identifies what the run used.
    """
    gate = stage.review_gate
    if gate is None:
        return None, "template"
    item = stage_brief(brief, stage.step_id)
    if gate.locked or item is None or item.review_gate is None:
        return gate, "template"
    return LoopReviewGate(
        mode=item.review_gate,
        max_passes=gate.max_passes,
        locked=gate.locked,
    ), "run_override"


def resolved_approved_command_prefixes(
    definition: LoopDefinition,
    brief: LoopBrief | None,
    stage: LoopStepDefinition,
) -> tuple[str, ...]:
    """Resolve loop defaults and per-stage command approvals.

    Preconditions: ``stage`` belongs to ``definition`` and is agent-backed.
    Postconditions: the first agent stage supplies the inherited default while
    explicit template or Work values replace it without mutating snapshots.
    """
    first = next((item for item in definition.stages if item.agent is not None), None)
    if first is None or first.agent is None:
        return ()
    first_brief = stage_brief(brief, first.step_id)
    base = (
        first_brief.approved_command_prefixes
        if first_brief is not None
        and first_brief.approved_command_prefixes is not None
        else first.agent.approved_command_prefixes or ()
    )
    if stage.step_id == first.step_id or stage.agent is None:
        return base
    stage_input = stage_brief(brief, stage.step_id)
    if stage_input is not None and stage_input.approved_command_prefixes is not None:
        return stage_input.approved_command_prefixes
    return (
        stage.agent.approved_command_prefixes
        if stage.agent.approved_command_prefixes is not None
        else base
    )


def brief_snapshot(brief: LoopBrief) -> dict[str, Any]:
    """Serialize one validated brief into its stable JSON wire shape."""
    return {
        "goal": brief.goal,
        "stages": [
            {
                "stage_id": stage.stage_id,
                "note": stage.note,
                "context": [
                    {"kind": context.kind.value, "value": context.value}
                    for context in stage.context
                ],
                **(
                    {
                        "agent": {
                            "provider": stage.agent.provider,
                            "model": stage.agent.model,
                            "options": dict(stage.agent.options),
                        }
                    }
                    if stage.agent is not None
                    else {}
                ),
                **(
                    {"review_gate": stage.review_gate.value}
                    if stage.review_gate is not None
                    else {}
                ),
                **(
                    {
                        "approved_command_prefixes": list(
                            stage.approved_command_prefixes
                        )
                    }
                    if stage.approved_command_prefixes is not None
                    else {}
                ),
            }
            for stage in brief.stages
        ],
    }


def brief_from_snapshot(value: object) -> LoopBrief:
    """Restore a Work or run brief from its additive JSON shape."""
    if not isinstance(value, dict):
        raise ValueError("loop brief must be a mapping")
    goal = value.get("goal")
    raw_stages = value.get("stages", [])
    if not isinstance(goal, str) or not isinstance(raw_stages, list):
        raise ValueError("loop brief needs a goal and stage list")
    return LoopBrief(
        goal=goal,
        stages=tuple(_stage_from_snapshot(item) for item in raw_stages),
    )


def optional_brief_from_snapshot(value: object) -> LoopBrief | None:
    """Read an optional legacy-compatible brief, ignoring malformed additions."""
    if value is None:
        return None
    try:
        return brief_from_snapshot(value)
    except (TypeError, ValueError):
        return None


def _stage_from_snapshot(value: object) -> LoopStageBrief:
    if not isinstance(value, dict):
        raise ValueError("loop stage brief must be a mapping")
    stage_id = value.get("stage_id")
    note = value.get("note", "")
    raw_context = value.get("context", [])
    if not isinstance(stage_id, str) or not stage_id:
        raise ValueError("loop stage brief needs stage_id")
    if not isinstance(note, str) or not isinstance(raw_context, list):
        raise ValueError("loop stage brief has invalid note or context")
    return LoopStageBrief(
        stage_id=stage_id,
        note=note,
        context=tuple(_context_from_snapshot(item) for item in raw_context),
        agent=_agent_from_snapshot(value.get("agent")),
        review_gate=_review_gate_mode(value.get("review_gate")),
        approved_command_prefixes=_command_prefixes(
            value.get("approved_command_prefixes")
        ),
    )


def _context_from_snapshot(value: object) -> LoopBriefContext:
    if not isinstance(value, dict):
        raise ValueError("loop brief context must be a mapping")
    raw_value = value.get("value")
    raw_kind = value.get("kind")
    if not isinstance(raw_value, str) or not isinstance(raw_kind, str):
        raise ValueError("loop brief context needs kind and value")
    return LoopBriefContext(kind=LoopBriefContextKind(raw_kind), value=raw_value)


def _agent_from_snapshot(value: object) -> LoopBriefAgent | None:
    if value is None:
        return None
    if not isinstance(value, dict):
        raise ValueError("loop brief agent override must be a mapping")
    provider = value.get("provider")
    model = value.get("model")
    options = value.get("options", {})
    if provider is not None and not isinstance(provider, str):
        raise ValueError("loop brief agent provider must be a string")
    if model is not None and not isinstance(model, str):
        raise ValueError("loop brief agent model must be a string")
    if not isinstance(options, dict) or not all(isinstance(key, str) for key in options):
        raise ValueError("loop brief agent options must be a mapping")
    return LoopBriefAgent(provider=provider, model=model, options=dict(options))


def _review_gate_mode(value: object) -> LoopReviewGateMode | None:
    if value is None:
        return None
    if not isinstance(value, str):
        raise ValueError("loop brief review gate must be a string")
    return LoopReviewGateMode(value)


def _command_prefixes(value: object) -> tuple[str, ...] | None:
    if value is None:
        return None
    if not isinstance(value, list) or not all(isinstance(item, str) for item in value):
        raise ValueError("loop brief command prefixes must be a string list")
    return tuple(value)


__all__ = [
    "brief_from_snapshot",
    "brief_snapshot",
    "optional_brief_from_snapshot",
    "prompt_values",
    "resolved_approved_command_prefixes",
    "resolved_review_gate",
    "stage_brief",
    "validate_brief",
    "with_legacy_required_defaults",
]
