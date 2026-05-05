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
    # Auto-approves every tool — the SDK still calls our ``can_use_tool``
    # callback, but Atelier's Claude adapter short-circuits and returns
    # ``allow`` without emitting a permission event so the dialog stays
    # quiet. Use only for trusted, fast-iterating tasks.
    BYPASS = "bypassPermissions"


class AmpMode(str, Enum):
    SMART = "smart"
    RUSH = "rush"
    DEEP = "deep"
    LARGE = "large"


class AmpPermissionMode(str, Enum):
    """Top-level permissioning policy for an Amp agent.

    Amp's CLI has no async ``can_use_tool`` callback; tool gating works
    through declarative permission rules + a ``delegate`` mechanism that
    lets us run a shim before Bash. These three modes pick which side of
    the friction-vs-safety tradeoff the user wants. **All three still
    allow the agent to ask** — denying or stopping a turn is independent
    of this setting.

    - ``DEFAULT`` — conservative auto-allow list (Read/Grep/Glob/Edit/
      Write/etc.); Bash gated through the bridge → permission UI.
    - ``ALLOW_ALL`` — ``--dangerously-allow-all`` on the CLI. No prompts,
      no gating. Fastest, riskiest. Equivalent to the pre-permission UI
      behaviour.
    - ``CUSTOM`` — the user supplies a list of tool names to auto-allow;
      Bash always stays gated through the bridge regardless of the list
      (otherwise the user could disable shell gating, defeating the
      reason this knob exists).
    """

    DEFAULT = "default"
    ALLOW_ALL = "allow_all"
    CUSTOM = "custom"


AMP_DEFAULT_AUTO_ALLOWED_TOOLS: tuple[str, ...] = (
    "Read",
    "Grep",
    "Glob",
    "edit_file",
    "create_file",
    "WebFetch",
    "Task",
    "TodoWrite",
    "undo_edit",
    "get_diagnostics",
)
"""Tools auto-allowed by ``AmpPermissionMode.DEFAULT``.

Read-only research + the Amp tools the agent uses in normal flow that
aren't shell. Everything not on this list AND not on the user's CUSTOM
list defaults to the CLI's ``ask`` behaviour, which would hang because
the CLI has no TTY — so we ALWAYS pass an explicit rule for every tool
the agent might use. If a brand-new Amp tool appears and isn't here,
the user will see a hang and we add it (failing closed beats silent
auto-allow)."""


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
    permission_mode: AmpPermissionMode = AmpPermissionMode.DEFAULT
    # Only meaningful when ``permission_mode == CUSTOM``. Bash stays gated
    # through the bridge regardless — including ``"Bash"`` here is a no-op.
    custom_allowed_tools: tuple[str, ...] = ()


AgentConfig = ClaudeAgentConfig | AmpAgentConfig


__all__ = [
    "AMP_DEFAULT_AUTO_ALLOWED_TOOLS",
    "DEFAULT_ALLOWED_TOOLS",
    "AgentConfig",
    "AmpAgentConfig",
    "AmpMode",
    "AmpPermissionMode",
    "ClaudeAgentConfig",
    "ClaudeEffort",
    "ClaudeModel",
    "ClaudePermissionMode",
    "CommonAgentConfig",
]
