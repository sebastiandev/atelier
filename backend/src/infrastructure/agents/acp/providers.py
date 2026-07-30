"""Per-provider spawn wiring for the ACP adapter.

This is the entire per-provider surface of the ACP runtime on the
adapter side: which binary to spawn for which config type. Versions are
pinned to the ACP-registry releases the configs were captured against —
bump deliberately, re-capturing the config-option fixtures in
``tests/fixtures/acp/`` when you do.
"""

import json
import os
import shutil
import tempfile
from collections.abc import Callable
from pathlib import Path
from typing import Any

from src.domain.agents import (
    AgentAdapter,
    ClaudeAcpAgentConfig,
    CodexAcpAgentConfig,
)
from src.domain.agents import (
    parse_command_prefix_tokens as _command_prefix_tokens,
)
from src.domain.agents.configs import OpenCodeAgentConfig, OpenCodeMode
from src.infrastructure.agents.acp.adapter import AcpAdapter
from src.infrastructure.agents.factory import build_adapter
from src.settings import Settings

_ACP_RUNTIME_BIN = (
    Path(__file__).resolve().parents[4]
    / "acp-runtime"
    / "node_modules"
    / ".bin"
)

CLAUDE_ACP_ARGV: tuple[str, ...] = (
    str(_ACP_RUNTIME_BIN / "claude-agent-acp"),
)

CODEX_ACP_ARGV: tuple[str, ...] = (
    str(_ACP_RUNTIME_BIN / "codex-acp"),
)

# OpenCode ships its own ACP server; the local binary is the install.
OPENCODE_ARGV: tuple[str, ...] = ("opencode", "acp")


@build_adapter.register
def _build_claude_acp(config: ClaudeAcpAgentConfig, settings: Settings) -> AgentAdapter:
    rules = _glob_command_prefixes(config.common.approved_command_prefixes)
    return AcpAdapter(
        config,
        CLAUDE_ACP_ARGV,
        model_label=config.model.value,
        authentication_recovery_command="claude auth login",
        session_meta=(
            {
                "claudeCode": {
                    "options": {
                        "allowedTools": [f"Bash({rule} *)" for rule in rules]
                    }
                }
            }
            if rules
            else None
        ),
    )


@build_adapter.register
def _build_codex_acp(config: CodexAcpAgentConfig, settings: Settings) -> AgentAdapter:
    environment, cleanup = _codex_environment(
        config.common.approved_command_prefixes
    )
    return AcpAdapter(
        config,
        CODEX_ACP_ARGV,
        model_label=config.model.value,
        environment=environment,
        cleanup=cleanup,
    )


@build_adapter.register
def _build_opencode(config: OpenCodeAgentConfig, settings: Settings) -> AgentAdapter:
    # No model_label: the underlying model is OpenCode's configured
    # default and unknown to Atelier; usage_update supplies runtime data.
    #
    # The config layer is always sent, never only when a stage pins command
    # prefixes: an unpinned write stage would otherwise fall through to the
    # user's own OpenCode permissions and behave unlike a pinned one.
    return AcpAdapter(
        config,
        OPENCODE_ARGV,
        environment={
            "OPENCODE_CONFIG_CONTENT": _opencode_config(
                _glob_command_prefixes(config.common.approved_command_prefixes),
                write=config.mode is OpenCodeMode.BUILD,
                readable_roots=(
                    *config.common.readable_roots,
                    *config.common.writable_roots,
                ),
            )
        },
    )


def _glob_command_prefixes(prefixes: tuple[str, ...]) -> tuple[str, ...]:
    """Return command prefixes safe for Claude/OpenCode glob policy syntax."""
    return tuple(
        " ".join(tokens)
        for tokens in _command_prefix_tokens(prefixes)
        if all(
            not any(char in token for char in "*?[]")
            and not any(char.isspace() for char in token)
            for token in tokens
        )
    )


def _opencode_config(
    rules: tuple[str, ...],
    *,
    write: bool,
    readable_roots: tuple[Path, ...] = (),
) -> str:
    """Express the stage's permission posture in OpenCode's config layer.

    OpenCode is the one provider whose session mode does not carry the
    posture: ``build`` only means "not plan", so a write stage still gates
    every command and every read outside its cwd, while claude-acp gets
    ``bypassPermissions`` and codex-acp ``agent-full-access``. The same loop
    stage would be silently far more interruptive here. So write says allow.

    Stage command prefixes are *additive*. On a write stage everything is
    allowed already and they only document intent; on a read stage they are
    the exceptions to an otherwise-``ask`` default. Either way, naming a
    prefix widens what the agent may do -- it never narrows it.

    ``readable_roots`` are the directories outside the worktree that Atelier
    itself points the run at. Write stays bounded to the workspace; reads
    have to reach the source repository, because that is where the plan
    artifact a run is briefed against lives.
    """
    try:
        current = json.loads(os.environ.get("OPENCODE_CONFIG_CONTENT", "{}"))
    except json.JSONDecodeError:
        current = {}
    config: dict[str, Any] = current if isinstance(current, dict) else {}
    permission = config.get("permission")
    if isinstance(permission, str):
        permission = {"*": permission}
    elif not isinstance(permission, dict):
        permission = {}
    bash = permission.get("bash")
    if isinstance(bash, str):
        bash = {"*": bash}
    elif not isinstance(bash, dict):
        bash = {}
    bash["*"] = "allow" if write else bash.get("*", "ask")
    for rule in rules:
        bash[rule] = "allow"
        bash[f"{rule} *"] = "allow"
    permission["bash"] = bash
    if write:
        permission["edit"] = "allow"
    external = permission.get("external_directory")
    if isinstance(external, str):
        external = {"*": external}
    elif not isinstance(external, dict):
        external = {}
    for root in dict.fromkeys(readable_roots):
        external[str(root)] = "allow"
        external[f"{root}/*"] = "allow"
        external[f"{root}/**"] = "allow"
    if external:
        permission["external_directory"] = external
    config["permission"] = permission
    return json.dumps(config, separators=(",", ":"))


def _codex_environment(
    prefixes: tuple[str, ...],
) -> tuple[dict[str, str] | None, Callable[[], None] | None]:
    """Create an isolated Codex ACP config layer with stage prefix rules."""
    rules = _command_prefix_tokens(prefixes)
    if not rules:
        return None, None
    source = Path(os.environ.get("CODEX_HOME", Path.home() / ".codex")).expanduser().resolve()
    overlay = tempfile.TemporaryDirectory(prefix="atelier-codex-acp-")
    root = Path(overlay.name)
    if source.is_dir():
        for entry in source.iterdir():
            if entry.name == "rules":
                continue
            target = root / entry.name
            try:
                target.symlink_to(entry, target_is_directory=entry.is_dir())
            except OSError:
                if entry.is_file():
                    shutil.copy2(entry, target)
    rules_dir = root / "rules"
    rules_dir.mkdir()
    source_rules = source / "rules"
    if source_rules.is_dir():
        for entry in source_rules.iterdir():
            if entry.name == "atelier-stage.rules":
                continue
            target = rules_dir / entry.name
            try:
                target.symlink_to(entry, target_is_directory=entry.is_dir())
            except OSError:
                if entry.is_file():
                    shutil.copy2(entry, target)
    rendered = "\n\n".join(
        "prefix_rule(\n"
        f"    pattern = [{', '.join(json.dumps(token) for token in tokens)}],\n"
        '    decision = "allow",\n'
        '    justification = "Approved for this Atelier loop stage",\n'
        ")"
        for tokens in rules
    )
    (rules_dir / "atelier-stage.rules").write_text(rendered + "\n", encoding="utf-8")
    return {
        "CODEX_HOME": str(root),
        "CODEX_SQLITE_HOME": os.environ.get("CODEX_SQLITE_HOME", str(source)),
    }, overlay.cleanup


__all__ = ["CLAUDE_ACP_ARGV", "CODEX_ACP_ARGV", "OPENCODE_ARGV"]
