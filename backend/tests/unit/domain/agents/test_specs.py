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
    assert ClaudeModel.OPUS_4_7.value in desc.primary_field.values
    assert "thinking_effort" in desc.options
    assert "permission_mode" in desc.options
    assert desc.options["thinking_effort"].default == ClaudeEffort.OFF.value


def test_claude_describe_includes_model_meta() -> None:
    desc = ClaudeSpec().describe()
    assert set(desc.model_meta.keys()) == {m.value for m in ClaudeModel}
    opus = desc.model_meta[ClaudeModel.OPUS_4_7.value]
    assert opus.context_window == 200_000
    assert opus.input_per_mtok == 15.0
    assert opus.output_per_mtok == 75.0
    assert opus.cache_read_per_mtok == 1.50
    assert opus.cache_write_per_mtok == 18.75
    opus_1m = desc.model_meta[ClaudeModel.OPUS_4_7_1M.value]
    assert opus_1m.context_window == 1_000_000
    assert opus_1m.input_per_mtok == 15.0
    assert opus_1m.output_per_mtok == 75.0
    sonnet = desc.model_meta[ClaudeModel.SONNET_4_6.value]
    assert sonnet.input_per_mtok == 3.0
    assert sonnet.output_per_mtok == 15.0
    haiku = desc.model_meta[ClaudeModel.HAIKU_4_5.value]
    assert haiku.input_per_mtok == 1.0
    assert haiku.output_per_mtok == 5.0


def test_claude_describe_defaults_to_opus_1m() -> None:
    desc = ClaudeSpec().describe()
    assert desc.primary_field.default == ClaudeModel.OPUS_4_7_1M.value
    assert ClaudeModel.OPUS_4_7_1M.value in desc.primary_field.values


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


def test_amp_describe_model_meta_is_blank_per_mode() -> None:
    """Amp routes modes to underlying models without public pricing —
    each mode gets a blank ``ModelMeta`` so the FE knows the keys exist
    but won't render numbers. Once Amp publishes a stable mapping, fill
    these in (and the FE will start showing cost/ctx automatically)."""
    desc = AmpSpec().describe()
    assert set(desc.model_meta.keys()) == {m.value for m in AmpMode}
    for meta in desc.model_meta.values():
        assert meta.context_window is None
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
# Registry
# ---------------------------------------------------------------------------


def test_specs_registry_covers_all_providers() -> None:
    assert set(SPECS.keys()) == {"claude-code", "amp"}
