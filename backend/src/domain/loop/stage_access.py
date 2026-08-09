"""Questions any stage can be asked, answered by the kind of stage it is.

Only some kinds of stage carry instructions, an agent policy, or a review
gate, so a caller that wants one cannot simply read the attribute. Before
stages were types this was ``if stage.kind == ...``; afterwards it became
``stage.x if isinstance(stage, T) else default`` repeated at a dozen call
sites, each free to pick a different default.

Each question is asked once here and dispatched on the stage type, so the
answer for a kind lives in one place and callers just ask.
"""

from __future__ import annotations

from functools import singledispatch

from src.domain.loop.dtos import (
    AgentStage,
    CheckStage,
    LoopAgentPolicy,
    LoopReviewGate,
    LoopStepDefinition,
    ReviewStage,
)


@singledispatch
def stage_instructions(stage: LoopStepDefinition) -> str:
    """The Markdown brief handed to the agent, empty when nothing runs one."""
    return ""


@stage_instructions.register
def _agent_instructions(stage: AgentStage) -> str:
    return stage.instructions


@singledispatch
def stage_agent_policy(stage: LoopStepDefinition) -> LoopAgentPolicy | None:
    """How to launch the agent, or ``None`` when the stage launches none."""
    return None


@stage_agent_policy.register
def _agent_policy(stage: AgentStage) -> LoopAgentPolicy | None:
    return stage.agent


@singledispatch
def stage_review_gate(stage: LoopStepDefinition) -> LoopReviewGate | None:
    """The bounded send-back rule, or ``None`` when the stage cannot gate."""
    return None


@stage_review_gate.register
def _review_gate(stage: ReviewStage) -> LoopReviewGate | None:
    return stage.review_gate


@singledispatch
def stage_check_command(stage: LoopStepDefinition) -> tuple[str, ...]:
    """The command a deterministic check runs; empty for every other kind."""
    return ()


@stage_check_command.register
def _check_command(stage: CheckStage) -> tuple[str, ...]:
    return stage.check_command


@singledispatch
def stage_check_adapter(stage: LoopStepDefinition) -> str | None:
    """How a deterministic check is executed; ``None`` when it is not one."""
    return None


@stage_check_adapter.register
def _check_adapter(stage: CheckStage) -> str | None:
    return stage.check_adapter


@singledispatch
def stage_note_required(stage: LoopStepDefinition) -> bool | None:
    """Whether the agent must leave a note, ``None`` to follow the default."""
    return None


@stage_note_required.register
def _note_required(stage: AgentStage) -> bool | None:
    return stage.note_required


__all__ = [
    "stage_agent_policy",
    "stage_check_adapter",
    "stage_check_command",
    "stage_instructions",
    "stage_note_required",
    "stage_review_gate",
]
