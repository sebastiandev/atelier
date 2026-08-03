"""Public behavior checks for loop agent-policy inheritance."""

import pytest

from src.domain.agents.launch import InvalidProviderConfig
from src.domain.loop.agent_policy import (
    apply_retry_overrides,
    apply_stage_agent_policy,
    resolve_stage_agent_config,
    resolve_stage_model,
    sanitize_agent_policy,
)
from src.domain.loop.dtos import LoopAgentPolicy, LoopBriefAgent, LoopPermission


def test_retry_overrides_absent_returns_base_unchanged() -> None:
    base = {"mode": "build", "reasoning_effort": "medium"}

    provider, model, options = apply_retry_overrides(
        "opencode",
        "anthropic/claude-opus-5",
        base,
        model_override=None,
        effort_override=None,
    )

    assert (provider, model) == ("opencode", "anthropic/claude-opus-5")
    assert options == base
    assert options is not base  # never mutates the caller's dict


def test_retry_override_changes_model_and_effort_keeping_other_options() -> None:
    provider, model, options = apply_retry_overrides(
        "codex-acp",
        "gpt-5.6-terra",
        {"mode": "build", "reasoning_effort": "medium"},
        model_override="gpt-5.6-luna",
        effort_override="high",
    )

    assert provider == "codex-acp"
    assert model == "gpt-5.6-luna"
    # Effort lands under the provider's own key; unrelated options survive.
    assert options == {"mode": "build", "reasoning_effort": "high"}


def test_retry_override_uses_provider_specific_effort_key() -> None:
    # claude-acp spells effort "thinking_effort", not "reasoning_effort".
    _, _, options = apply_retry_overrides(
        "claude-acp",
        "default",
        {"permission_mode": "plan"},
        model_override=None,
        effort_override="high",
    )

    assert options == {"permission_mode": "plan", "thinking_effort": "high"}


def test_retry_override_rejects_effort_invalid_for_provider() -> None:
    with pytest.raises(InvalidProviderConfig):
        apply_retry_overrides(
            "codex-acp",
            "gpt-5.6-terra",
            {},
            model_override=None,
            effort_override="not-a-level",
        )


def test_retry_override_rejects_model_from_another_provider() -> None:
    with pytest.raises(InvalidProviderConfig):
        apply_retry_overrides(
            "codex-acp",
            "gpt-5.6-terra",
            {},
            model_override="opus[1m]",  # a claude model, not codex
            effort_override=None,
        )


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


def test_unsupported_target_does_not_receive_effort() -> None:
    resolved = apply_stage_agent_policy(
        "amp",
        {"reasoning_effort": "high", "sandbox": "workspace-write"},
        LoopAgentPolicy(provider="amp", permissions=None, effort=None),
        parent_provider="codex",
    )

    assert "thinking_effort" not in resolved
    assert "reasoning_effort" not in resolved


def test_opencode_inherits_effort_as_a_variant() -> None:
    resolved = apply_stage_agent_policy(
        "opencode",
        {"reasoning_effort": "high"},
        LoopAgentPolicy(provider="opencode", permissions=None, effort=None),
        parent_provider="codex",
    )

    assert resolved["reasoning_effort"] == "high"


def test_explicit_effort_requires_provider_support() -> None:
    with pytest.raises(ValueError, match="does not support effort"):
        apply_stage_agent_policy(
            "amp",
            {"permission_mode": "default"},
            LoopAgentPolicy(effort="high"),
        )


def test_codex_fast_mode_is_applied_before_stage_launch() -> None:
    resolved = apply_stage_agent_policy(
        "codex-acp",
        {"reasoning_effort": "high"},
        LoopAgentPolicy(fast=True),
    )

    assert resolved == {"reasoning_effort": "high", "fast-mode": "on"}


def test_fast_mode_requires_provider_support() -> None:
    with pytest.raises(ValueError, match="does not support fast mode"):
        apply_stage_agent_policy(
            "opencode",
            {},
            LoopAgentPolicy(fast=True),
        )


def test_run_provider_override_replaces_template_fast_mode() -> None:
    provider, _, options = resolve_stage_agent_config(
        LoopAgentPolicy(provider="codex-acp", fast=True),
        parent_provider="codex-acp",
        parent_model="gpt-5.5",
        parent_options={"fast-mode": "on"},
        override=LoopBriefAgent(
            provider="claude-acp",
            model="default",
            options={"permission_mode": "default"},
        ),
    )

    assert provider == "claude-acp"
    assert "fast-mode" not in options


def test_confirming_the_same_provider_keeps_the_stage_model_and_fast_flag() -> None:
    """Re-picking a stage's own provider is not a switch.

    Run setup sends whatever the picker shows, so a brief routinely names the
    provider the definition already pinned. Treating that as a switch dropped
    the definition's model and fast flag on the floor.
    """
    provider, model, options = resolve_stage_agent_config(
        LoopAgentPolicy(provider="codex-acp", model="gpt-5.4", fast=True),
        parent_provider="amp",
        parent_model="smart",
        parent_options={},
        override=LoopBriefAgent(provider="codex-acp"),
    )

    assert (provider, model) == ("codex-acp", "gpt-5.4")
    assert options["fast-mode"] == "on"


def test_switching_provider_still_drops_the_stage_model_and_fast_flag() -> None:
    """They describe a provider that is no longer in play."""
    provider, model, options = resolve_stage_agent_config(
        LoopAgentPolicy(provider="codex-acp", model="gpt-5.4", fast=True),
        parent_provider="amp",
        parent_model="smart",
        parent_options={},
        override=LoopBriefAgent(provider="claude-acp"),
    )

    assert provider == "claude-acp"
    assert model != "gpt-5.4"
    assert "fast-mode" not in options


def test_a_brief_model_wins_over_the_stage_model() -> None:
    _, model, _ = resolve_stage_agent_config(
        LoopAgentPolicy(provider="codex-acp", model="gpt-5.4"),
        parent_provider="amp",
        parent_model="smart",
        parent_options={},
        override=LoopBriefAgent(provider="codex-acp", model="gpt-5.5"),
    )

    assert model == "gpt-5.5"


def test_an_unpinned_stage_confirming_the_inherited_provider_keeps_its_model() -> None:
    """The stage's model belongs to the provider it inherits when it pins none."""
    _, model, _ = resolve_stage_agent_config(
        LoopAgentPolicy(model="gpt-5.4"),
        parent_provider="codex-acp",
        parent_model="gpt-5.5",
        parent_options={},
        override=LoopBriefAgent(provider="codex-acp"),
    )

    assert model == "gpt-5.4"


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


# ---------------------------------------------------------------------------
# sanitize_agent_policy — one answer to "does this apply to this model?"
# ---------------------------------------------------------------------------


def test_fast_is_dropped_for_a_provider_without_the_option() -> None:
    """The Create PR dialog sends `fast` whether or not anyone chose it, and
    OpenCode has no fast-mode option. Launching used to raise on it."""
    policy = LoopAgentPolicy(provider="opencode", model="anthropic/x", fast=False)

    assert sanitize_agent_policy(policy, provider="opencode").fast is None


def test_fast_survives_where_the_provider_supports_it() -> None:
    policy = LoopAgentPolicy(provider="codex-acp", fast=True)

    assert sanitize_agent_policy(policy, provider="codex-acp").fast is True


def test_effort_outside_the_provider_ladder_is_dropped() -> None:
    policy = LoopAgentPolicy(provider="claude-acp", effort="nonsense")

    assert sanitize_agent_policy(policy, provider="claude-acp").effort is None


def test_effort_is_dropped_for_a_provider_with_no_dial() -> None:
    policy = LoopAgentPolicy(provider="amp", effort="high")

    assert sanitize_agent_policy(policy, provider="amp").effort is None


def test_a_supported_policy_is_returned_untouched() -> None:
    policy = LoopAgentPolicy(provider="claude-acp", effort="high")

    assert sanitize_agent_policy(policy, provider="claude-acp") is policy


def test_a_pr_stage_agent_no_longer_breaks_the_launch() -> None:
    """End to end: the config that raised `provider 'opencode' does not
    support fast mode` now resolves and simply omits fast."""
    provider, _model, options = resolve_stage_agent_config(
        LoopAgentPolicy(
            provider="opencode",
            model="anthropic/claude-sonnet-5",
            effort="medium",
            fast=False,
        ),
        parent_provider="opencode",
        parent_model="anthropic/claude-sonnet-5",
        parent_options={"mode": "build"},
    )

    assert provider == "opencode"
    assert "fast-mode" not in options
