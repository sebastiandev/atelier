"""Provider surface tests for the ACP runtimes (claude-acp / codex-acp).

Spec build validation, factory dispatch through the AcpAgentConfig base,
config-value hooks, summary-only compaction configs, and resume-command
translation back to the native CLIs.
"""

import asyncio
import json
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
    ClaudeAcpFastMode,
    ClaudeAcpModel,
    ClaudeAcpPermissionMode,
    CodexAcpAgentConfig,
    CodexAcpEffort,
    CodexAcpFastMode,
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


def _common(*approved_command_prefixes: str) -> CommonAgentConfig:
    return CommonAgentConfig(
        workdir=WORKDIR,
        system_prompt="prompt",
        approved_command_prefixes=approved_command_prefixes,
    )


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
        {
            "thinking_effort": "xhigh",
            "permission_mode": "acceptEdits",
            "fast-mode": "on",
        },
    )
    assert isinstance(config, ClaudeAcpAgentConfig)
    assert config.model is ClaudeAcpModel.FABLE_5_1M
    assert config.thinking_effort is ClaudeAcpEffort.XHIGH
    assert config.permission_mode is ClaudeAcpPermissionMode.ACCEPT_EDITS
    assert config.fast_mode is ClaudeAcpFastMode.ON


def test_claude_acp_maps_removed_sonnet_1m_alias_to_sonnet() -> None:
    config = ClaudeAcpSpec().build(_common(), "sonnet[1m]", {})

    assert config.model is ClaudeAcpModel.SONNET


def test_claude_acp_spec_rejects_unknown_options() -> None:
    with pytest.raises(ValueError, match="unknown options"):
        ClaudeAcpSpec().build(_common(), "default", {"sandbox": "read-only"})


def test_claude_acp_spec_rejects_unknown_model() -> None:
    with pytest.raises(ValueError):
        ClaudeAcpSpec().build(_common(), "claude-opus-4-7", {})


def test_codex_acp_spec_builds_typed_config() -> None:
    config = CodexAcpSpec().build(
        _common(),
        "gpt-5.6-sol",
        {"reasoning_effort": "ultra", "fast-mode": "on", "mode": "read-only"},
    )
    assert isinstance(config, CodexAcpAgentConfig)
    assert config.model is CodexAcpModel.GPT_5_6_SOL
    assert config.reasoning_effort is CodexAcpEffort.ULTRA
    assert config.fast_mode is CodexAcpFastMode.ON
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


def test_claude_acp_config_values_cover_all_options() -> None:
    config = ClaudeAcpAgentConfig(
        common=_common(),
        model=ClaudeAcpModel.SONNET,
        thinking_effort=ClaudeAcpEffort.HIGH,
        permission_mode=ClaudeAcpPermissionMode.PLAN,
        fast_mode=ClaudeAcpFastMode.ON,
    )
    assert config.acp_config_values() == (
        ("model", "sonnet"),
        ("effort", "high"),
        ("mode", "plan"),
        ("fast", "on"),
    )
    assert config.acp_mode_id() is None


def test_codex_acp_config_values_cover_all_options() -> None:
    config = CodexAcpAgentConfig(common=_common())
    assert config.acp_config_values() == (
        ("model", "gpt-5.5"),
        ("reasoning_effort", "medium"),
        ("fast-mode", "off"),
        ("mode", "agent"),
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


def test_claude_acp_passes_stage_prefixes_as_session_metadata() -> None:
    adapter = build_adapter(
        ClaudeAcpAgentConfig(common=_common("dt pytest", "dt pytest && rm -rf /")),
        Settings(),
    )

    assert adapter._session_meta == {
        "claudeCode": {
            "options": {
                "allowedTools": ["Bash(dt pytest *)"],
            }
        }
    }


def test_opencode_write_stage_allows_commands_and_keeps_user_denials(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """`build` alone does not carry write posture, so the config must.

    Stage prefixes are additive: they widen what is allowed, never narrow
    it. The user's own explicit denials survive alongside the catch-all.
    """
    monkeypatch.setenv(
        "OPENCODE_CONFIG_CONTENT",
        '{"theme":"system","permission":{"bash":{"rm *":"deny"}}}',
    )
    adapter = build_adapter(
        OpenCodeAgentConfig(common=_common("dt ty")),
        Settings(),
    )

    config = json.loads(adapter._environment["OPENCODE_CONFIG_CONTENT"])
    assert config["theme"] == "system"
    assert config["permission"]["bash"] == {
        "*": "allow",
        "rm *": "deny",
        "dt ty": "allow",
        "dt ty *": "allow",
    }
    assert config["permission"]["edit"] == "allow"


def test_opencode_read_stage_keeps_ask_and_treats_prefixes_as_exceptions() -> None:
    adapter = build_adapter(
        OpenCodeAgentConfig(common=_common("dt ty"), mode=OpenCodeMode.PLAN),
        Settings(),
    )

    config = json.loads(adapter._environment["OPENCODE_CONFIG_CONTENT"])
    assert config["permission"]["bash"] == {
        "*": "ask",
        "dt ty": "allow",
        "dt ty *": "allow",
    }
    assert "edit" not in config["permission"]


def test_opencode_reads_the_roots_atelier_points_the_run_at() -> None:
    """A run's plan artifact lives in the source repo, not the worktree."""
    common = CommonAgentConfig(
        workdir=WORKDIR,
        system_prompt="prompt",
        readable_roots=(Path("/src/repo"),),
        writable_roots=(Path("/shares/design"),),
    )

    adapter = build_adapter(OpenCodeAgentConfig(common=common), Settings())

    external = json.loads(
        adapter._environment["OPENCODE_CONFIG_CONTENT"]
    )["permission"]["external_directory"]
    assert external["/src/repo"] == "allow"
    assert external["/src/repo/**"] == "allow"
    assert external["/shares/design/**"] == "allow"
    assert "*" not in external


def test_opencode_config_is_sent_even_without_stage_prefixes() -> None:
    """Otherwise an unpinned write stage silently inherits user defaults."""
    adapter = build_adapter(OpenCodeAgentConfig(common=_common()), Settings())

    config = json.loads(adapter._environment["OPENCODE_CONFIG_CONTENT"])
    assert config["permission"]["bash"]["*"] == "allow"


def test_codex_acp_uses_temporary_config_layer_for_stage_rules(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    codex_home = tmp_path / "codex-home"
    source_rules = codex_home / "rules"
    source_rules.mkdir(parents=True)
    (codex_home / "auth.json").write_text("{}", encoding="utf-8")
    (source_rules / "default.rules").write_text(
        'prefix_rule(pattern = ["git", "status"], decision = "allow")\n',
        encoding="utf-8",
    )
    monkeypatch.setenv("CODEX_HOME", str(codex_home))
    monkeypatch.delenv("CODEX_SQLITE_HOME", raising=False)
    adapter = build_adapter(
        CodexAcpAgentConfig(
            common=_common("dt pytest", "dt pytest && rm -rf /")
        ),
        Settings(),
    )
    overlay = Path(adapter._environment["CODEX_HOME"])

    generated = (overlay / "rules" / "atelier-stage.rules").read_text(
        encoding="utf-8"
    )
    assert overlay != codex_home
    assert adapter._environment["CODEX_SQLITE_HOME"] == str(codex_home)
    assert (overlay / "auth.json").exists()
    assert (overlay / "rules" / "default.rules").exists()
    assert 'pattern = ["dt", "pytest"]' in generated
    assert "rm" not in generated

    asyncio.run(adapter.close())
    assert not overlay.exists()


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
        options={"reasoning_effort": "xhigh", "mode": "agent-full-access"},
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
        options={"reasoning_effort": "medium", "mode": "agent"},
    )
    assert "--sandbox" not in cmd
    assert "--ask-for-approval" not in cmd
    assert "-c" not in cmd


def test_codex_acp_accepts_legacy_mode_values() -> None:
    config = SPECS["codex-acp"].build(
        _common(), "gpt-5.5", {"mode": "full-access"}
    )

    assert config.mode is CodexAcpMode.FULL_ACCESS


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
