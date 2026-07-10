"""Per-provider spawn wiring for the ACP adapter.

This is the entire per-provider surface of the ACP runtime on the
adapter side: which binary to spawn for which config type. Versions are
pinned to the ACP-registry releases the configs were captured against —
bump deliberately, re-capturing the config-option fixtures in
``tests/fixtures/acp/`` when you do.
"""

from pathlib import Path

from src.domain.agents import (
    AgentAdapter,
    ClaudeAcpAgentConfig,
    CodexAcpAgentConfig,
)
from src.domain.agents.configs import OpenCodeAgentConfig
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
    return AcpAdapter(config, CLAUDE_ACP_ARGV, model_label=config.model.value)


@build_adapter.register
def _build_codex_acp(config: CodexAcpAgentConfig, settings: Settings) -> AgentAdapter:
    return AcpAdapter(config, CODEX_ACP_ARGV, model_label=config.model.value)


@build_adapter.register
def _build_opencode(config: OpenCodeAgentConfig, settings: Settings) -> AgentAdapter:
    # No model_label: the underlying model is OpenCode's configured
    # default and unknown to Atelier; usage_update supplies runtime data.
    return AcpAdapter(config, OPENCODE_ARGV)


__all__ = ["CLAUDE_ACP_ARGV", "CODEX_ACP_ARGV", "OPENCODE_ARGV"]
