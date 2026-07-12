"""Unit tests for Spec.describe() + Spec.build() per provider.

Each Spec is the combined descriptor + validator the route consults.
These tests pin down the wire format invariants:

- describe() returns provider name, primary field, options enum surface
- build() coerces wire strings into typed enums
- build() rejects unknown option keys
- build() rejects bad enum values
"""

from pathlib import Path

import pytest

from src.domain.agents import (
    SPECS,
    AmpAgentConfig,
    AmpMode,
    AmpSpec,
    ClaudeAgentConfig,
    ClaudeEffort,
    ClaudeModel,
    ClaudePermissionMode,
    ClaudeSpec,
    CodexAgentConfig,
    CodexApprovalMode,
    CodexModel,
    CodexReasoningEffort,
    CodexSandbox,
    CodexSpec,
    CommonAgentConfig,
)


def _common() -> CommonAgentConfig:
    return CommonAgentConfig(
        workdir=Path("/tmp/atelier/test"),
        system_prompt="you are a test agent",
    )


# ---------------------------------------------------------------------------
# ClaudeSpec
# ---------------------------------------------------------------------------


def test_claude_describe_lists_models_and_options() -> None:
    desc = ClaudeSpec().describe()
    assert desc.name == "claude-code"
    assert desc.primary_field.label == "Model"
    assert ClaudeModel.FABLE_5.value in desc.primary_field.values
    assert ClaudeModel.OPUS_4_8.value in desc.primary_field.values
    assert ClaudeModel.OPUS_4_7.value in desc.primary_field.values
    assert "thinking_effort" in desc.options
    assert "permission_mode" in desc.options
    assert desc.options["thinking_effort"].default == ClaudeEffort.XHIGH.value


def test_claude_describe_includes_model_meta() -> None:
    desc = ClaudeSpec().describe()
    assert set(desc.model_meta.keys()) == {m.value for m in ClaudeModel}
    fable = desc.model_meta[ClaudeModel.FABLE_5.value]
    assert fable.context_window == 1_000_000
    assert fable.input_per_mtok == 15.0
    assert fable.output_per_mtok == 75.0
    assert fable.cache_read_per_mtok == 1.50
    assert fable.cache_write_per_mtok == 18.75
    assert fable.effort_values == ("low", "medium", "high", "xhigh", "max")
    assert fable.effort_default == "xhigh"
    opus_48 = desc.model_meta[ClaudeModel.OPUS_4_8.value]
    assert opus_48.context_window == 1_000_000
    assert opus_48.input_per_mtok == 5.0
    assert opus_48.output_per_mtok == 25.0
    assert opus_48.cache_read_per_mtok == 0.50
    assert opus_48.cache_write_per_mtok == 6.25
    opus = desc.model_meta[ClaudeModel.OPUS_4_7.value]
    assert opus.context_window == 1_000_000
    assert opus.input_per_mtok == 5.0
    assert opus.output_per_mtok == 25.0
    assert opus.cache_read_per_mtok == 0.50
    assert opus.cache_write_per_mtok == 6.25
    opus_1m = desc.model_meta[ClaudeModel.OPUS_4_7_1M.value]
    assert opus_1m.context_window == 1_000_000
    assert opus_1m.input_per_mtok == 5.0
    assert opus_1m.output_per_mtok == 25.0
    sonnet = desc.model_meta[ClaudeModel.SONNET_4_6.value]
    assert sonnet.input_per_mtok == 3.0
    assert sonnet.output_per_mtok == 15.0
    haiku = desc.model_meta[ClaudeModel.HAIKU_4_5.value]
    assert haiku.input_per_mtok == 1.0
    assert haiku.output_per_mtok == 5.0


def test_claude_describe_defaults_to_latest_model() -> None:
    desc = ClaudeSpec().describe()
    assert desc.primary_field.default == ClaudeModel.FABLE_5.value
    assert ClaudeModel.FABLE_5.value in desc.primary_field.values


def test_claude_build_accepts_1m_opus() -> None:
    config = ClaudeSpec().build(
        _common(), ClaudeModel.OPUS_4_7_1M.value, options={}
    )
    assert config.model is ClaudeModel.OPUS_4_7_1M
    # The string the SDK receives keeps the ``[1m]`` suffix — that's the
    # CLI's opt-in for the 1M extended-context tier.
    assert config.model.value == "claude-opus-4-7[1m]"


def test_claude_build_with_defaults() -> None:
    config = ClaudeSpec().build(_common(), ClaudeModel.OPUS_4_7.value, options={})
    assert isinstance(config, ClaudeAgentConfig)
    assert config.model is ClaudeModel.OPUS_4_7
    assert config.thinking_effort is ClaudeEffort.OFF
    assert config.permission_mode is ClaudePermissionMode.DEFAULT


def test_claude_build_fable_defaults_to_supported_effort() -> None:
    config = ClaudeSpec().build(_common(), ClaudeModel.FABLE_5.value, options={})
    assert config.model is ClaudeModel.FABLE_5
    assert config.thinking_effort is ClaudeEffort.XHIGH


def test_claude_build_fable_accepts_supported_effort() -> None:
    config = ClaudeSpec().build(
        _common(),
        ClaudeModel.FABLE_5.value,
        options={"thinking_effort": "max"},
    )
    assert config.thinking_effort is ClaudeEffort.MAX


def test_claude_build_fable_rejects_off_effort() -> None:
    with pytest.raises(ValueError, match="not available"):
        ClaudeSpec().build(
            _common(),
            ClaudeModel.FABLE_5.value,
            options={"thinking_effort": "off"},
        )


def test_claude_build_with_full_options() -> None:
    config = ClaudeSpec().build(
        _common(),
        ClaudeModel.SONNET_4_6.value,
        options={"thinking_effort": "high", "permission_mode": "plan"},
    )
    assert config.model is ClaudeModel.SONNET_4_6
    assert config.thinking_effort is ClaudeEffort.HIGH
    assert config.permission_mode is ClaudePermissionMode.PLAN


def test_claude_build_rejects_unknown_option() -> None:
    with pytest.raises(ValueError, match="unknown options"):
        ClaudeSpec().build(_common(), ClaudeModel.OPUS_4_7.value, options={"bogus": 1})


def test_claude_build_rejects_bad_model() -> None:
    with pytest.raises(ValueError):
        ClaudeSpec().build(_common(), "made-up-model", options={})


def test_claude_build_rejects_bad_effort() -> None:
    with pytest.raises(ValueError):
        ClaudeSpec().build(
            _common(),
            ClaudeModel.OPUS_4_7.value,
            options={"thinking_effort": "ludicrous"},
        )


# ---------------------------------------------------------------------------
# AmpSpec
# ---------------------------------------------------------------------------


def test_amp_describe_lists_modes_and_permission_mode() -> None:
    desc = AmpSpec().describe()
    assert desc.name == "amp"
    assert desc.primary_field.label == "Mode"
    assert AmpMode.SMART.value in desc.primary_field.values
    assert "permission_mode" in desc.options
    assert {"default", "allow_all", "custom"} <= set(
        desc.options["permission_mode"].values
    )
    assert "custom_allowed_tools" in desc.text_options
    assert desc.text_options["custom_allowed_tools"].visible_when == (
        "permission_mode",
        "custom",
    )


def test_amp_describe_exposes_per_mode_context_window() -> None:
    """Each Amp mode publishes a known context window (rush/smart route
    to 200k-window underlying models, deep/large route to 1M ones) so
    the FE can render ctx% for Amp agents the same way the Amp CLI does."""
    desc = AmpSpec().describe()
    assert set(desc.model_meta.keys()) == {m.value for m in AmpMode}
    assert desc.model_meta[AmpMode.SMART.value].context_window == 200_000
    assert desc.model_meta[AmpMode.RUSH.value].context_window == 200_000
    assert desc.model_meta[AmpMode.DEEP.value].context_window == 1_000_000
    assert desc.model_meta[AmpMode.LARGE.value].context_window == 1_000_000


def test_amp_describe_pricing_remains_blank() -> None:
    """Amp routes modes to underlying models without exposing per-token
    pricing — keep the pricing fields ``None`` so the FE shows "—" for
    cost rather than guessing."""
    desc = AmpSpec().describe()
    for meta in desc.model_meta.values():
        assert meta.input_per_mtok is None
        assert meta.output_per_mtok is None
        assert meta.cache_read_per_mtok is None
        assert meta.cache_write_per_mtok is None


def test_amp_build_with_default() -> None:
    config = AmpSpec().build(_common(), AmpMode.SMART.value, options={})
    assert isinstance(config, AmpAgentConfig)
    assert config.mode is AmpMode.SMART


def test_amp_build_with_custom_allowed_tools() -> None:
    config = AmpSpec().build(
        _common(),
        AmpMode.SMART.value,
        options={
            "permission_mode": "custom",
            "custom_allowed_tools": "Read, Grep, edit_file",
        },
    )
    assert isinstance(config, AmpAgentConfig)
    assert config.custom_allowed_tools == ("Read", "Grep", "edit_file")


def test_amp_build_rejects_unknown_option() -> None:
    with pytest.raises(ValueError, match="unknown options"):
        AmpSpec().build(_common(), AmpMode.SMART.value, options={"effort": "high"})


def test_amp_build_rejects_bad_mode() -> None:
    with pytest.raises(ValueError):
        AmpSpec().build(_common(), "turbo", options={})


# ---------------------------------------------------------------------------
# CodexSpec
# ---------------------------------------------------------------------------


def test_codex_describe_lists_models_and_options() -> None:
    desc = CodexSpec().describe()
    assert desc.name == "codex"
    assert desc.primary_field.label == "Model"
    assert desc.primary_field.values == [
        CodexModel.GPT_5_6_TERRA.value,
        CodexModel.GPT_5_6_LUNA.value,
        CodexModel.GPT_5_6_SOL.value,
        CodexModel.GPT_5_5.value,
        CodexModel.GPT_5_5_PRO.value,
        CodexModel.GPT_5_4.value,
        CodexModel.GPT_5_4_PRO.value,
    ]
    assert CodexModel.GPT_5_6_TERRA.value in desc.primary_field.values
    assert CodexModel.GPT_5_6_LUNA.value in desc.primary_field.values
    assert CodexModel.GPT_5_6_SOL.value in desc.primary_field.values
    assert CodexModel.GPT_5_4.value in desc.primary_field.values
    assert CodexModel.GPT_5_5.value in desc.primary_field.values
    assert CodexModel.GPT_5_5_PRO.value in desc.primary_field.values
    assert CodexModel.GPT_5_4_PRO.value in desc.primary_field.values
    assert desc.primary_field.default == CodexModel.GPT_5_5.value
    assert "reasoning_effort" in desc.options
    assert desc.options["reasoning_effort"].values == [
        CodexReasoningEffort.MINIMAL.value,
        CodexReasoningEffort.LOW.value,
        CodexReasoningEffort.MEDIUM.value,
        CodexReasoningEffort.HIGH.value,
        CodexReasoningEffort.XHIGH.value,
        CodexReasoningEffort.MAX.value,
        CodexReasoningEffort.EXTRA.value,
        CodexReasoningEffort.ULTRA.value,
    ]
    assert "sandbox" in desc.options
    assert "approval_mode" in desc.options
    assert (
        desc.options["approval_mode"].default
        == CodexApprovalMode.ON_REQUEST.value
    )
    assert (
        desc.options["sandbox"].default == CodexSandbox.WORKSPACE_WRITE.value
    )


def test_codex_describe_exposes_model_meta() -> None:
    desc = CodexSpec().describe()
    assert desc.model_meta[CodexModel.GPT_5_6_TERRA.value].input_per_mtok is None
    assert desc.model_meta[CodexModel.GPT_5_6_LUNA.value].input_per_mtok is None
    assert desc.model_meta[CodexModel.GPT_5_6_SOL.value].input_per_mtok is None
    assert desc.model_meta[CodexModel.GPT_5_5.value].context_window == 400_000
    assert desc.model_meta[CodexModel.GPT_5_5.value].input_per_mtok == 5.0
    assert desc.model_meta[CodexModel.GPT_5_5.value].output_per_mtok == 30.0
    assert desc.model_meta[CodexModel.GPT_5_5.value].cache_read_per_mtok == 0.50
    assert desc.model_meta[CodexModel.GPT_5_5_PRO.value].context_window is None
    assert desc.model_meta[CodexModel.GPT_5_5_PRO.value].input_per_mtok == 30.0
    assert desc.model_meta[CodexModel.GPT_5_5_PRO.value].output_per_mtok == 180.0
    assert desc.model_meta[CodexModel.GPT_5_4.value].context_window == 272_000
    assert desc.model_meta[CodexModel.GPT_5_4.value].cache_read_per_mtok == 0.25
    assert desc.model_meta[CodexModel.GPT_5_4_PRO.value].input_per_mtok == 30.0
    assert desc.model_meta[CodexModel.GPT_5_4_PRO.value].output_per_mtok == 180.0


def test_codex_describe_explains_dual_permission_layers() -> None:
    desc = CodexSpec().describe()
    assert desc.advanced_intro is not None
    assert "Sandbox" in desc.advanced_intro
    assert "Approval" in desc.advanced_intro


def test_codex_build_with_defaults() -> None:
    config = CodexSpec().build(_common(), CodexModel.GPT_5_5.value, options={})
    assert isinstance(config, CodexAgentConfig)
    assert config.model is CodexModel.GPT_5_5
    assert config.reasoning_effort is CodexReasoningEffort.MEDIUM
    assert config.sandbox is CodexSandbox.WORKSPACE_WRITE
    assert config.approval_mode is CodexApprovalMode.ON_REQUEST


def test_codex_build_with_full_options() -> None:
    config = CodexSpec().build(
        _common(),
        CodexModel.GPT_5_5_PRO.value,
        options={
            "reasoning_effort": "ultra",
            "sandbox": "read-only",
            "approval_mode": "untrusted",
        },
    )
    assert config.model is CodexModel.GPT_5_5_PRO
    assert config.reasoning_effort is CodexReasoningEffort.ULTRA
    assert config.sandbox is CodexSandbox.READ_ONLY
    assert config.approval_mode is CodexApprovalMode.UNTRUSTED


def test_codex_build_accepts_max_reasoning_effort() -> None:
    config = CodexSpec().build(
        _common(),
        CodexModel.GPT_5_5.value,
        options={"reasoning_effort": "max"},
    )

    assert config.reasoning_effort is CodexReasoningEffort.MAX


def test_codex_build_accepts_terra_aliases() -> None:
    for model in ("5.6 terra", "gpt.5.6-terra"):
        config = CodexSpec().build(_common(), model, options={})
        assert config.model is CodexModel.GPT_5_6_TERRA


def test_codex_build_rejects_unknown_option() -> None:
    with pytest.raises(ValueError, match="unknown options"):
        CodexSpec().build(
            _common(), CodexModel.GPT_5_4.value, options={"bogus": True}
        )


def test_codex_build_rejects_bad_model() -> None:
    with pytest.raises(ValueError):
        CodexSpec().build(_common(), "gpt-9000", options={})


def test_codex_build_rejects_bad_sandbox() -> None:
    with pytest.raises(ValueError):
        CodexSpec().build(
            _common(),
            CodexModel.GPT_5_4.value,
            options={"sandbox": "moon-base"},
        )


# ---------------------------------------------------------------------------
# Registry
# ---------------------------------------------------------------------------


def test_specs_registry_covers_all_providers() -> None:
    assert set(SPECS.keys()) == {
        "claude-code",
        "amp",
        "codex",
        "claude-acp",
        "codex-acp",
        "opencode",
    }


def test_specs_registry_lists_legacy_providers_first() -> None:
    """Wire-compat: older frontends index providers by order; the legacy
    trio must stay in front of the ACP additions."""
    assert list(SPECS)[:3] == ["claude-code", "amp", "codex"]
