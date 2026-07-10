"""Provider surface tests for the ACP runtimes (claude-acp / codex-acp).

Spec build validation, factory dispatch through the AcpAgentConfig base,
config-value hooks, summary-only compaction configs, and resume-command
translation back to the native CLIs.
"""

from pathlib import Path

import pytest

from src.domain.agents import (
    AcpAgentConfig,
    ClaudeAcpAgentConfig,
    CommonAgentConfig,
)
from src.domain.agents.configs import (
    OPENCODE_CONFIGURED_MODEL,
    ClaudeAcpEffort,
    ClaudeAcpModel,
    ClaudeAcpPermissionMode,
    CodexAcpAgentConfig,
    CodexAcpEffort,
    CodexAcpMode,
    CodexAcpModel,
    OpenCodeAgentConfig,
    OpenCodeMode,
)
from src.domain.agents.specs import (
    SPECS,
    ClaudeAcpSpec,
    CodexAcpSpec,
    OpenCodeSpec,
)
from src.infrastructure.agents import AcpAdapter, build_adapter
from src.infrastructure.agents.acp.providers import (
    CLAUDE_ACP_ARGV,
    CODEX_ACP_ARGV,
)
from src.infrastructure.agents.compaction_sessions import _summary_config
from src.infrastructure.cli_launcher import build_resume_command
from src.settings import Settings

WORKDIR = Path("/tmp/atelier/test")


def _common() -> CommonAgentConfig:
    return CommonAgentConfig(workdir=WORKDIR, system_prompt="prompt")


# -- specs ---------------------------------------------------------------


def test_specs_registry_keeps_legacy_order_then_acp() -> None:
    assert list(SPECS) == [
        "claude-code",
        "amp",
        "codex",
        "claude-acp",
        "codex-acp",
        "opencode",
    ]


def test_claude_acp_spec_builds_typed_config() -> None:
    config = ClaudeAcpSpec().build(
        _common(),
        "claude-fable-5[1m]",
        {"thinking_effort": "xhigh", "permission_mode": "acceptEdits"},
    )
    assert isinstance(config, ClaudeAcpAgentConfig)
    assert config.model is ClaudeAcpModel.FABLE_5_1M
    assert config.thinking_effort is ClaudeAcpEffort.XHIGH
    assert config.permission_mode is ClaudeAcpPermissionMode.ACCEPT_EDITS


def test_claude_acp_spec_rejects_unknown_options() -> None:
    with pytest.raises(ValueError, match="unknown options"):
        ClaudeAcpSpec().build(_common(), "default", {"sandbox": "read-only"})


def test_claude_acp_spec_rejects_unknown_model() -> None:
    with pytest.raises(ValueError):
        ClaudeAcpSpec().build(_common(), "claude-opus-4-7", {})


def test_codex_acp_spec_builds_typed_config() -> None:
    config = CodexAcpSpec().build(
        _common(), "gpt-5.6-terra", {"reasoning_effort": "ultra", "mode": "read-only"}
    )
    assert isinstance(config, CodexAcpAgentConfig)
    assert config.model is CodexAcpModel.GPT_5_6_TERRA
    assert config.reasoning_effort is CodexAcpEffort.ULTRA
    assert config.mode is CodexAcpMode.READ_ONLY


def test_codex_acp_spec_accepts_terra_aliases() -> None:
    for model in ("5.6 terra", "gpt.5.6-terra"):
        config = CodexAcpSpec().build(_common(), model, {})
        assert config.model is CodexAcpModel.GPT_5_6_TERRA


def test_codex_acp_spec_rejects_legacy_codex_options() -> None:
    with pytest.raises(ValueError, match="unknown options"):
        CodexAcpSpec().build(_common(), "gpt-5.5", {"approval_mode": "never"})


def test_opencode_spec_builds_configured_default() -> None:
    config = OpenCodeSpec().build(
        _common(), OPENCODE_CONFIGURED_MODEL, {"mode": "plan"}
    )
    assert isinstance(config, OpenCodeAgentConfig)
    assert config.model == OPENCODE_CONFIGURED_MODEL
    assert config.mode is OpenCodeMode.PLAN
    # Configured-default model never travels as a config option.
    assert config.acp_config_values() == (("mode", "plan"),)


def test_opencode_spec_accepts_explicit_model_for_acp_config() -> None:
    config = OpenCodeSpec().build(_common(), "ollama/llama2", {})

    assert isinstance(config, OpenCodeAgentConfig)
    assert config.model == "ollama/llama2"
    assert config.acp_config_values() == (
        ("model", "ollama/llama2"),
        ("mode", "build"),
    )


def test_opencode_explicit_model_config_value_for_forward_compat() -> None:
    config = OpenCodeAgentConfig(common=_common(), model="ollama/llama2")
    assert config.acp_config_values() == (
        ("model", "ollama/llama2"),
        ("mode", "build"),
    )


def test_acp_descriptors_describe_without_error() -> None:
    for name in ("claude-acp", "codex-acp", "opencode"):
        descriptor = SPECS[name].describe()
        assert descriptor.name == name
        assert descriptor.primary_field.default in descriptor.primary_field.values
        for option in descriptor.options.values():
            assert option.default in option.values


# -- config hooks ------------------------------------------------------------


def test_claude_acp_config_values_cover_all_three_options() -> None:
    config = ClaudeAcpAgentConfig(
        common=_common(),
        model=ClaudeAcpModel.SONNET,
        thinking_effort=ClaudeAcpEffort.HIGH,
        permission_mode=ClaudeAcpPermissionMode.PLAN,
    )
    assert config.acp_config_values() == (
        ("model", "sonnet"),
        ("effort", "high"),
        ("mode", "plan"),
    )
    assert config.acp_mode_id() is None


def test_codex_acp_config_values_cover_all_three_options() -> None:
    config = CodexAcpAgentConfig(common=_common())
    assert config.acp_config_values() == (
        ("model", "gpt-5.5"),
        ("reasoning_effort", "medium"),
        ("mode", "auto"),
    )


# -- factory -----------------------------------------------------------------


def test_factory_builds_acp_adapter_for_all_acp_providers() -> None:
    for config in (
        ClaudeAcpAgentConfig(common=_common()),
        CodexAcpAgentConfig(common=_common()),
        OpenCodeAgentConfig(common=_common()),
    ):
        adapter = build_adapter(config, Settings())
        assert isinstance(adapter, AcpAdapter)


def test_acp_wrappers_spawn_from_locked_backend_runtime() -> None:
    runtime_bin = (
        Path(__file__).resolve().parents[4]
        / "acp-runtime"
        / "node_modules"
        / ".bin"
    )
    assert CLAUDE_ACP_ARGV == (str(runtime_bin / "claude-agent-acp"),)
    assert CODEX_ACP_ARGV == (str(runtime_bin / "codex-acp"),)
    assert "npx" not in {*CLAUDE_ACP_ARGV, *CODEX_ACP_ARGV}


# -- compaction summary configs ----------------------------------------------


def test_summary_config_claude_acp_uses_plan_mode() -> None:
    config = ClaudeAcpAgentConfig(common=_common())
    summary = _summary_config(config)
    assert isinstance(summary, ClaudeAcpAgentConfig)
    assert summary.summary_only is True
    assert summary.permission_mode is ClaudeAcpPermissionMode.PLAN


def test_summary_config_codex_acp_uses_read_only() -> None:
    config = CodexAcpAgentConfig(common=_common())
    summary = _summary_config(config)
    assert summary.summary_only is True
    assert summary.mode is CodexAcpMode.READ_ONLY


def test_summary_config_generic_acp_sets_summary_only() -> None:
    config = AcpAgentConfig(common=_common())
    summary = _summary_config(config)
    assert isinstance(summary, AcpAgentConfig)
    assert summary.summary_only is True


def test_summary_config_opencode_uses_plan_mode() -> None:
    config = OpenCodeAgentConfig(common=_common())
    summary = _summary_config(config)
    assert isinstance(summary, OpenCodeAgentConfig)
    assert summary.summary_only is True
    assert summary.mode is OpenCodeMode.PLAN


# -- resume commands -----------------------------------------------------------


def test_claude_acp_resume_translates_default_sentinels() -> None:
    cmd = build_resume_command(
        "claude-acp",
        "sess-1",
        WORKDIR,
        model="default",
        options={"thinking_effort": "default", "permission_mode": "default"},
    )
    assert cmd == f"cd '{WORKDIR}' && claude --resume 'sess-1'"


def test_claude_acp_resume_passes_explicit_values() -> None:
    cmd = build_resume_command(
        "claude-acp",
        "sess-1",
        WORKDIR,
        model="claude-fable-5[1m]",
        options={"thinking_effort": "xhigh", "permission_mode": "acceptEdits"},
    )
    assert "--model 'claude-fable-5[1m]'" in cmd
    assert "--effort 'xhigh'" in cmd
    assert "--permission-mode 'acceptEdits'" in cmd
    assert cmd.endswith("--resume 'sess-1'")


def test_codex_acp_resume_unfolds_mode_to_cli_flags() -> None:
    cmd = build_resume_command(
        "codex-acp",
        "sess-2",
        WORKDIR,
        model="gpt-5.5",
        options={"reasoning_effort": "xhigh", "mode": "full-access"},
    )
    assert "--sandbox 'danger-full-access'" in cmd
    assert "--ask-for-approval 'never'" in cmd
    assert "model_reasoning_effort" in cmd
    assert cmd.startswith(f"cd '{WORKDIR}' && codex resume")


def test_codex_acp_resume_auto_mode_omits_default_flags() -> None:
    cmd = build_resume_command(
        "codex-acp",
        "sess-2",
        WORKDIR,
        model="gpt-5.5",
        options={"reasoning_effort": "medium", "mode": "auto"},
    )
    assert "--sandbox" not in cmd
    assert "--ask-for-approval" not in cmd
    assert "-c" not in cmd


def test_opencode_resume_is_bare_session_for_configured_default() -> None:
    cmd = build_resume_command(
        "opencode",
        "ses_abc",
        WORKDIR,
        model=OPENCODE_CONFIGURED_MODEL,
        options={"mode": "build"},
    )
    assert cmd == f"cd '{WORKDIR}' && opencode --session 'ses_abc'"


def test_opencode_resume_passes_explicit_model_for_forward_compat() -> None:
    cmd = build_resume_command(
        "opencode", "ses_abc", WORKDIR, model="ollama/llama2", options={}
    )
    assert "--model 'ollama/llama2'" in cmd
