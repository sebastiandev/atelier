"""Per-provider AgentConfig dataclasses + the AgentConfig union.

Each adapter consumes one of these typed configs at construction time.
The Spec layer (`specs.py`) builds them from wire-format requests;
`build_adapter` (`infrastructure/agents/factory.py`) singledispatches
on the union to instantiate the matching adapter.

`CommonAgentConfig` is the shared bottom: every adapter needs workdir
+ system prompt + context regardless of provider. Per-provider configs
hold their own typed knobs (Claude's thinking effort, Amp's mode).

Composition over inheritance: provider configs hold a ``common`` field
rather than subclassing CommonAgentConfig. Frozen dataclass + ABC +
field defaults play badly together; composition sidesteps it.
"""

from dataclasses import dataclass
from enum import Enum
from pathlib import Path


class ClaudeModel(str, Enum):
    """Claude model aliases accepted by the Claude Agent SDK / CLI.

    These are the canonical short-form names (the CLI also accepts
    versioned snapshots like ``claude-haiku-4-5-20251001`` if you need
    to pin a specific date).
    """

    OPUS_4_7 = "claude-opus-4-7"
    SONNET_4_6 = "claude-sonnet-4-6"
    HAIKU_4_5 = "claude-haiku-4-5"


class ClaudeEffort(str, Enum):
    OFF = "off"
    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"
    MAX = "max"


class ClaudePermissionMode(str, Enum):
    DEFAULT = "default"
    ACCEPT_EDITS = "acceptEdits"
    PLAN = "plan"


class AmpMode(str, Enum):
    SMART = "smart"
    RUSH = "rush"
    DEEP = "deep"
    LARGE = "large"


@dataclass(frozen=True, kw_only=True)
class CommonAgentConfig:
    """Inputs every adapter needs regardless of provider."""

    workdir: Path
    system_prompt: str


DEFAULT_ALLOWED_TOOLS: tuple[str, ...] = ("Read", "Grep", "Glob")
"""Tools the Claude SDK auto-allows without invoking the permission callback.

Read-only research tools — no side effects, no network mutation. Anything
that mutates state (Edit, Write, Bash, WebFetch, ...) flows through
``can_use_tool`` so the user reviews each invocation. The user sees the
exact ``tool_input`` (e.g. the literal Bash command, the file path + new
content for Edit) and can approve, allow-always for that tool name, or
deny."""


@dataclass(frozen=True, kw_only=True)
class ClaudeAgentConfig:
    common: CommonAgentConfig
    model: ClaudeModel
    thinking_effort: ClaudeEffort = ClaudeEffort.OFF
    permission_mode: ClaudePermissionMode = ClaudePermissionMode.DEFAULT
    allowed_tools: tuple[str, ...] = DEFAULT_ALLOWED_TOOLS


@dataclass(frozen=True, kw_only=True)
class AmpAgentConfig:
    common: CommonAgentConfig
    mode: AmpMode = AmpMode.SMART


AgentConfig = ClaudeAgentConfig | AmpAgentConfig


__all__ = [
    "DEFAULT_ALLOWED_TOOLS",
    "AgentConfig",
    "AmpAgentConfig",
    "AmpMode",
    "ClaudeAgentConfig",
    "ClaudeEffort",
    "ClaudeModel",
    "ClaudePermissionMode",
    "CommonAgentConfig",
]
