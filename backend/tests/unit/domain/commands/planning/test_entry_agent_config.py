"""A story run's entry agent comes from the run, never from Planning."""

import pytest

from src.domain.commands.planning.start_run import (
    StageAgentUnresolved,
    _entry_agent_config,
)
from src.domain.loop.definitions import LoopDefinitionInvalid
from src.domain.loop.dtos import (
    ApprovalStage,
    LoopAgentPolicy,
    LoopBrief,
    LoopBriefAgent,
    LoopPermission,
    LoopStageBrief,
    LoopStepDefinition,
    TaskStage,
)


def _entry(agent: LoopAgentPolicy) -> LoopStepDefinition:
    return TaskStage(
        step_id="implementation",
        name="Implementation",
        agent=agent,
    )


def _brief(agent: LoopBriefAgent | None) -> LoopBrief:
    return LoopBrief(
        goal="Story",
        stages=(
            LoopStageBrief(stage_id="implementation", agent=agent),
        ),
    )


def test_the_brief_supplies_provider_model_and_options() -> None:
    provider, model, options = _entry_agent_config(
        _entry(LoopAgentPolicy()),
        _brief(
            LoopBriefAgent(
                provider="opencode",
                model="anthropic/claude-opus-5",
                options={"reasoning_effort": "medium"},
            )
        ),
    )

    assert provider == "opencode"
    assert model == "anthropic/claude-opus-5"
    assert options["reasoning_effort"] == "medium"


def test_a_provider_only_brief_falls_back_to_that_provider_s_own_default() -> None:
    """Not to any other provider's model -- there is no parent to borrow from."""
    provider, model, _ = _entry_agent_config(
        _entry(LoopAgentPolicy()), _brief(LoopBriefAgent(provider="amp"))
    )

    assert (provider, model) == ("amp", "smart")


def test_the_stage_policy_supplies_the_provider_when_the_brief_does_not() -> None:
    provider, model, _ = _entry_agent_config(
        _entry(LoopAgentPolicy(provider="amp", model="deep")), None
    )

    assert (provider, model) == ("amp", "deep")


def test_the_brief_overrides_the_stage_policy() -> None:
    provider, _, _ = _entry_agent_config(
        _entry(LoopAgentPolicy(provider="amp", model="deep")),
        _brief(LoopBriefAgent(provider="claude-acp")),
    )

    assert provider == "claude-acp"


def test_the_stage_policy_posture_still_applies() -> None:
    """Only the *parent* is gone; the definition's own knobs are not."""
    _, _, options = _entry_agent_config(
        _entry(
            LoopAgentPolicy(
                provider="claude-acp",
                permissions=LoopPermission.READ,
                effort="high",
            )
        ),
        None,
    )

    assert options["permission_mode"] == "plan"
    assert options["thinking_effort"] == "high"


def test_no_provider_anywhere_is_an_error_rather_than_a_guess() -> None:
    with pytest.raises(StageAgentUnresolved, match="does not pin a provider"):
        _entry_agent_config(_entry(LoopAgentPolicy()), _brief(LoopBriefAgent()))


def test_an_entry_stage_that_runs_no_agent_is_an_error() -> None:
    """A stage with no agent policy is no longer constructable -- the field
    lives on the stages that have one. What survives is the rule that a
    planning run must start with a stage that actually launches something."""
    approval = ApprovalStage(step_id="approval", name="Approve")

    with pytest.raises(LoopDefinitionInvalid):
        _entry_agent_config(approval, _brief(LoopBriefAgent(provider="amp")))
