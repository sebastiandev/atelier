"""Public behavior checks for loop agent-policy inheritance."""

import pytest

from src.domain.loop.agent_policy import apply_stage_agent_policy, resolve_stage_model
from src.domain.loop.dtos import LoopAgentPolicy, LoopPermission
from src.domain.models import Provider


def test_inherited_permission_preserves_parent_options() -> None:
    options = {"permission_mode": "default", "thinking_effort": "high"}

    resolved = apply_stage_agent_policy(
        "amp",
        options,
        LoopAgentPolicy(permissions=None),
    )

    assert resolved == options


def test_model_inheritance_does_not_cross_providers() -> None:
    resolved = resolve_stage_model(
        "codex",
        "amp",
        "rush",
        LoopAgentPolicy(provider="codex", model=None),
    )

    assert resolved == "gpt-5.5"


def test_cross_provider_inheritance_maps_permission_and_effort() -> None:
    resolved = apply_stage_agent_policy(
        "codex",
        {"permission_mode": "plan", "thinking_effort": "high"},
        LoopAgentPolicy(provider="codex", permissions=None, effort=None),
        parent_provider="claude-code",
    )

    assert resolved == {
        "reasoning_effort": "high",
        "sandbox": "read-only",
        "approval_mode": "never",
    }


def test_cross_provider_inheritance_uses_parent_descriptor_defaults() -> None:
    resolved = apply_stage_agent_policy(
        "claude-code",
        {},
        LoopAgentPolicy(provider="claude-code", permissions=None, effort=None),
        parent_provider="codex",
    )

    assert resolved == {
        "thinking_effort": "medium",
        "permission_mode": "bypassPermissions",
    }


def test_cross_provider_inheritance_uses_parent_model_effort_default() -> None:
    resolved = apply_stage_agent_policy(
        "codex-acp",
        {},
        LoopAgentPolicy(provider="codex-acp", permissions=None, effort=None),
        parent_provider="claude-code",
        parent_model="claude-fable-5",
    )

    assert resolved["reasoning_effort"] == "xhigh"


@pytest.mark.parametrize("provider", ["amp", "opencode"])
def test_unsupported_target_does_not_receive_effort(provider: Provider) -> None:
    resolved = apply_stage_agent_policy(
        provider,
        {"reasoning_effort": "high", "sandbox": "workspace-write"},
        LoopAgentPolicy(provider=provider, permissions=None, effort=None),
        parent_provider="codex",
    )

    assert "thinking_effort" not in resolved
    assert "reasoning_effort" not in resolved


def test_explicit_effort_requires_provider_support() -> None:
    with pytest.raises(ValueError, match="does not support effort"):
        apply_stage_agent_policy(
            "amp",
            {"permission_mode": "default"},
            LoopAgentPolicy(effort="high"),
        )


def test_amp_read_permission_rejects_mutating_tools() -> None:
    resolved = apply_stage_agent_policy(
        "amp",
        {"permission_mode": "allow_all"},
        LoopAgentPolicy(permissions=LoopPermission.READ),
    )

    assert resolved == {"permission_mode": "default", "read_only": "true"}


def test_amp_write_permission_keeps_normal_gating() -> None:
    resolved = apply_stage_agent_policy(
        "amp",
        {"permission_mode": "allow_all", "read_only": "true"},
        LoopAgentPolicy(permissions=LoopPermission.WRITE),
    )

    assert resolved == {"permission_mode": "default"}
