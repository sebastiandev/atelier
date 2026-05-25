"""Unit tests for ``build_resume_command`` flag dispatch.

The detach flow shells out to the user's terminal with a ``claude
--resume`` / ``amp threads continue`` invocation. The flags it emits
must mirror the agent's stored selector + options so the CLI session
keeps the user's choice instead of falling back to local defaults.

These tests exercise every supported flag plus the legacy "no
options at all" path that older agents (whose ``options`` column is
NULL) take.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from src.infrastructure.cli_launcher import build_resume_command

_WORKDIR = Path("/tmp/agent-1")
_SID = "sess-abc"


# ---------------------------------------------------------------------------
# Claude
# ---------------------------------------------------------------------------


def test_claude_legacy_call_emits_bare_command() -> None:
    """Callers that don't pass ``model``/``options`` (legacy agents
    whose ``options`` column is NULL) get the original bare invocation —
    no ``--model``, no ``--effort``, no ``--permission-mode``."""
    cmd = build_resume_command("claude-code", _SID, _WORKDIR)
    assert cmd == "cd '/tmp/agent-1' && claude --resume 'sess-abc'"


def test_claude_includes_model_flag() -> None:
    cmd = build_resume_command(
        "claude-code", _SID, _WORKDIR, model="claude-opus-4-7-1m"
    )
    assert "--model 'claude-opus-4-7-1m'" in cmd
    assert cmd.endswith("--resume 'sess-abc'")


def test_claude_emits_permission_mode_when_non_default() -> None:
    cmd = build_resume_command(
        "claude-code",
        _SID,
        _WORKDIR,
        model="claude-sonnet-4-6",
        options={"permission_mode": "acceptEdits"},
    )
    assert "--permission-mode 'acceptEdits'" in cmd


def test_claude_skips_default_permission_mode() -> None:
    """``default`` is what the CLI applies when ``--permission-mode`` is
    absent — forwarding it would just add noise."""
    cmd = build_resume_command(
        "claude-code",
        _SID,
        _WORKDIR,
        model="claude-sonnet-4-6",
        options={"permission_mode": "default"},
    )
    assert "--permission-mode" not in cmd


def test_claude_emits_effort_when_set() -> None:
    cmd = build_resume_command(
        "claude-code",
        _SID,
        _WORKDIR,
        model="claude-opus-4-7",
        options={"thinking_effort": "xhigh"},
    )
    assert "--effort 'xhigh'" in cmd


def test_claude_skips_off_effort() -> None:
    """``off`` has no CLI counterpart — Claude's ``--effort`` accepts
    low/medium/high/xhigh/max but not ``off``, so the flag is omitted."""
    cmd = build_resume_command(
        "claude-code",
        _SID,
        _WORKDIR,
        model="claude-opus-4-7",
        options={"thinking_effort": "off"},
    )
    assert "--effort" not in cmd


def test_claude_combines_all_flags_in_stable_order() -> None:
    """Flag order: model first, then effort, then permission-mode, then
    --resume. Stable order keeps the command string deterministic and
    the test diff readable."""
    cmd = build_resume_command(
        "claude-code",
        _SID,
        _WORKDIR,
        model="claude-opus-4-7",
        options={"thinking_effort": "high", "permission_mode": "bypassPermissions"},
    )
    assert cmd == (
        "cd '/tmp/agent-1' && claude "
        "--model 'claude-opus-4-7' "
        "--effort 'high' "
        "--permission-mode 'bypassPermissions' "
        "--resume 'sess-abc'"
    )


# ---------------------------------------------------------------------------
# Amp
# ---------------------------------------------------------------------------


def test_amp_legacy_call_emits_bare_command() -> None:
    cmd = build_resume_command("amp", _SID, _WORKDIR)
    assert cmd == "cd '/tmp/agent-1' && amp threads continue 'sess-abc'"


def test_amp_includes_mode_flag() -> None:
    cmd = build_resume_command("amp", _SID, _WORKDIR, model="deep")
    assert cmd == "cd '/tmp/agent-1' && amp --mode 'deep' threads continue 'sess-abc'"


def test_amp_emits_dangerously_allow_all_for_allow_all_permission_mode() -> None:
    """Only ``allow_all`` translates to a CLI flag — Amp's other modes
    (``default`` / ``custom``) rely on Atelier's permission bridge that
    doesn't exist outside Atelier."""
    cmd = build_resume_command(
        "amp",
        _SID,
        _WORKDIR,
        model="smart",
        options={"permission_mode": "allow_all"},
    )
    assert "--dangerously-allow-all" in cmd
    assert cmd == (
        "cd '/tmp/agent-1' && amp "
        "--dangerously-allow-all --mode 'smart' "
        "threads continue 'sess-abc'"
    )


def test_amp_skips_dangerously_allow_all_for_default_permission_mode() -> None:
    cmd = build_resume_command(
        "amp",
        _SID,
        _WORKDIR,
        model="smart",
        options={"permission_mode": "default"},
    )
    assert "--dangerously-allow-all" not in cmd


def test_amp_skips_dangerously_allow_all_for_custom_permission_mode() -> None:
    """``custom`` is an Atelier-side allowlist driven by the bridge —
    the Amp CLI has its own permission-rules system (``amp permissions
    add ...``) that we'd have to translate into. Out of scope for v1
    of CLI handover; the CLI session falls through to its own defaults."""
    cmd = build_resume_command(
        "amp",
        _SID,
        _WORKDIR,
        model="smart",
        options={
            "permission_mode": "custom",
            "custom_allowed_tools": ["Read", "Grep"],
        },
    )
    assert "--dangerously-allow-all" not in cmd


def test_amp_globals_precede_threads_subcommand() -> None:
    """``--mode`` and ``--dangerously-allow-all`` are root-level Amp
    flags — the CLI parses them before the ``threads`` subcommand, so
    they must sit between ``amp`` and ``threads``."""
    cmd = build_resume_command(
        "amp",
        _SID,
        _WORKDIR,
        model="large",
        options={"permission_mode": "allow_all"},
    )
    amp_idx = cmd.index(" amp ")
    threads_idx = cmd.index(" threads ")
    flag_segment = cmd[amp_idx + len(" amp ") : threads_idx]
    assert "--mode" in flag_segment
    assert "--dangerously-allow-all" in flag_segment


# ---------------------------------------------------------------------------
# Codex
# ---------------------------------------------------------------------------


def test_codex_legacy_call_emits_bare_command() -> None:
    """Older Codex agents whose ``options`` column is NULL get the
    plain ``codex resume <sid>`` invocation — no model flag, no
    sandbox flag, no approval flag, no reasoning effort override."""
    cmd = build_resume_command("codex", _SID, _WORKDIR)
    assert cmd == "cd '/tmp/agent-1' && codex resume 'sess-abc'"


def test_codex_includes_model_flag() -> None:
    cmd = build_resume_command("codex", _SID, _WORKDIR, model="gpt-5.4-pro")
    assert cmd == (
        "cd '/tmp/agent-1' && codex resume --model 'gpt-5.4-pro' 'sess-abc'"
    )


def test_codex_includes_additional_directories() -> None:
    cmd = build_resume_command(
        "codex",
        _SID,
        _WORKDIR,
        model="gpt-5.5",
        additional_directories=(
            Path("/Users/me/Atelier/projects/PRJ-001/shared/bmad"),
            Path("/Volumes/shared/specs"),
        ),
    )
    assert cmd == (
        "cd '/tmp/agent-1' && codex resume "
        "--model 'gpt-5.5' "
        "--add-dir '/Users/me/Atelier/projects/PRJ-001/shared/bmad' "
        "--add-dir '/Volumes/shared/specs' "
        "'sess-abc'"
    )


def test_codex_skips_default_sandbox() -> None:
    """``workspace-write`` is the SDK + CLI default — forwarding it would
    just add noise to the command string."""
    cmd = build_resume_command(
        "codex",
        _SID,
        _WORKDIR,
        model="gpt-5.4",
        options={"sandbox": "workspace-write"},
    )
    assert "--sandbox" not in cmd


def test_codex_emits_sandbox_when_non_default() -> None:
    cmd = build_resume_command(
        "codex",
        _SID,
        _WORKDIR,
        model="gpt-5.4",
        options={"sandbox": "read-only"},
    )
    assert "--sandbox 'read-only'" in cmd


def test_codex_skips_additional_directories_outside_workspace_write() -> None:
    cmd = build_resume_command(
        "codex",
        _SID,
        _WORKDIR,
        model="gpt-5.5",
        options={"sandbox": "read-only"},
        additional_directories=(Path("/tmp/shared"),),
    )
    assert "--add-dir" not in cmd


def test_codex_skips_default_approval_mode() -> None:
    cmd = build_resume_command(
        "codex",
        _SID,
        _WORKDIR,
        model="gpt-5.4",
        options={"approval_mode": "on-request"},
    )
    assert "--ask-for-approval" not in cmd


def test_codex_emits_approval_mode_when_non_default() -> None:
    cmd = build_resume_command(
        "codex",
        _SID,
        _WORKDIR,
        model="gpt-5.4",
        options={"approval_mode": "never"},
    )
    assert "--ask-for-approval 'never'" in cmd


def test_codex_skips_default_reasoning_effort() -> None:
    """``medium`` is the CLI default — forwarding it would just add noise."""
    cmd = build_resume_command(
        "codex",
        _SID,
        _WORKDIR,
        model="gpt-5.4",
        options={"reasoning_effort": "medium"},
    )
    assert "model_reasoning_effort" not in cmd


def test_codex_emits_reasoning_effort_via_config_override() -> None:
    """Codex's CLI has no dedicated reasoning-effort flag — it accepts
    arbitrary config overrides via ``-c key=value``. Forward through that."""
    cmd = build_resume_command(
        "codex",
        _SID,
        _WORKDIR,
        model="gpt-5.4",
        options={"reasoning_effort": "minimal"},
    )
    assert "-c 'model_reasoning_effort=\"minimal\"'" in cmd


def test_codex_combines_all_flags_in_stable_order() -> None:
    cmd = build_resume_command(
        "codex",
        _SID,
        _WORKDIR,
        model="gpt-5.4-pro",
        options={
            "sandbox": "read-only",
            "approval_mode": "untrusted",
            "reasoning_effort": "minimal",
        },
    )
    assert cmd == (
        "cd '/tmp/agent-1' && codex resume "
        "--model 'gpt-5.4-pro' "
        "--sandbox 'read-only' "
        "--ask-for-approval 'untrusted' "
        "-c 'model_reasoning_effort=\"minimal\"' "
        "'sess-abc'"
    )


# ---------------------------------------------------------------------------
# Misc
# ---------------------------------------------------------------------------


def test_unknown_provider_raises() -> None:
    with pytest.raises(ValueError, match="unknown provider"):
        build_resume_command("openai", _SID, _WORKDIR)  # type: ignore[arg-type]


def test_workdir_with_spaces_is_quoted() -> None:
    cmd = build_resume_command(
        "claude-code", _SID, Path("/Users/me/My Code/repo")
    )
    assert "cd '/Users/me/My Code/repo' &&" in cmd


def test_session_id_with_quotes_is_safely_escaped() -> None:
    """Defensive: nothing produces single-quoted session IDs today, but
    the shell-quote helper must handle them so an exotic ID can't break
    out of the resume command."""
    cmd = build_resume_command("amp", "sess'abc", _WORKDIR)
    assert "'sess'\\''abc'" in cmd
