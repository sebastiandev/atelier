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

    The ``[1m]`` suffix is the Claude Code CLI's opt-in for the 1M
    extended-context tier — it is a separate model id from the SDK's
    perspective, with its own context window (1M vs 200k) and (above
    200k input) a pricing surcharge that the flat ``ModelMeta`` doesn't
    fully model.
    """

    OPUS_4_7_1M = "claude-opus-4-7[1m]"
    OPUS_4_7 = "claude-opus-4-7"
    SONNET_4_6 = "claude-sonnet-4-6"
    HAIKU_4_5 = "claude-haiku-4-5"


class ClaudeEffort(str, Enum):
    """Thinking effort levels accepted by the Claude Agent SDK / CLI.

    Mirrors the Claude Code CLI's ``/effort`` ladder so users coming from
    the CLI see the same set of choices in Atelier's new-agent dialog.
    Higher tiers grant the model a larger thinking-token budget at the
    cost of latency + tokens billed.
    """

    OFF = "off"
    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"
    XHIGH = "xhigh"
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

    - ``DEFAULT`` — Bash gated through the bridge → permission UI; a
      conservative list of common tools (Read/Grep/Glob/edit_file/
      create_file/etc.) auto-allowed; everything else auto-allows too,
      because Amp's stream-json mode has no path to surface ``ask`` to
      our UI. The allowlist is documentation, not enforcement.
    - ``ALLOW_ALL`` — ``--dangerously-allow-all`` on the CLI. No prompts,
      no gating, including Bash. Fastest, riskiest.
    - ``CUSTOM`` — the user supplies a list of tool names to auto-allow;
      Bash always stays gated through the bridge regardless of the list
      (otherwise the user could disable shell gating, defeating the
      reason this knob exists). Same caveat as DEFAULT: tools outside
      the list still run — there's no fail-closed mode short of
      ``--dangerously-allow-all``-but-inverse, which the CLI doesn't
      offer.
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
aren't shell. The list is *informational*: under Neo, anything outside
it (and outside Bash, which is bridge-gated) also auto-runs because
the CLI defaults un-matched tools to allow and stream-json mode has no
path to surface ``ask`` to our UI. Keep this list close to the actual
tool surface so the future "delegate everything through a generic
bridge" path (using ``amp tools use``) has the right starting set."""


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


class CodexModel(str, Enum):
    """OpenAI Codex model aliases accepted by the Codex SDK / CLI.

    The Codex CLI/SDK accepts these short-form names. Newer date-stamped
    snapshots can be passed through the runtime config, but pinning here
    keeps the new-agent dialog deterministic.
    """

    GPT_5_4 = "gpt-5.4"
    GPT_5_4_PRO = "gpt-5.4-pro"
    GPT_5_3 = "gpt-5.3"


class CodexReasoningEffort(str, Enum):
    """Codex reasoning effort ladder.

    Mirrors the Codex CLI's ``model_reasoning_effort`` knob. ``high`` grants
    a longer reasoning budget at the cost of latency + tokens billed.
    """

    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"


class CodexSandbox(str, Enum):
    """Filesystem sandbox tier for the Codex process.

    Orthogonal to Atelier's per-tool permission UI: this is the OS-level
    guard rail (what Codex *can* touch on disk), while ``approval_mode``
    governs *when* it has to ask before touching.

    - ``READ_ONLY`` — agent can read but never write.
    - ``WORKSPACE_WRITE`` — writes are confined to the agent's worktree.
      This is the SDK default and the right grain for Atelier worktrees.
    - ``DANGER_FULL_ACCESS`` — no sandbox; agent can write anywhere the
      user can. Use only for trusted workflows + ``approval_mode=never``.
    """

    READ_ONLY = "read-only"
    WORKSPACE_WRITE = "workspace-write"
    DANGER_FULL_ACCESS = "danger-full-access"


class CodexApprovalMode(str, Enum):
    """Approval policy for Codex's typed approval requests.

    Maps directly to Codex's ``--ask-for-approval`` ladder. ``on-request``
    is the value that routes file/command approvals to Atelier's
    ``PermissionRequest`` event path; ``never`` auto-runs everything and
    ``untrusted`` is paranoid (every tool prompts).
    """

    NEVER = "never"
    ON_REQUEST = "on-request"
    ON_FAILURE = "on-failure"
    UNTRUSTED = "untrusted"


@dataclass(frozen=True, kw_only=True)
class CodexAgentConfig:
    """Codex adapter inputs.

    ``sandbox`` is OS-level filesystem gating, ``approval_mode`` is the
    when-to-prompt knob — both layers exist independently. The system
    prompt flows in via ``common.system_prompt`` and is forwarded to
    Codex as ``base_instructions`` by the adapter.
    """

    common: CommonAgentConfig
    model: CodexModel = CodexModel.GPT_5_4
    reasoning_effort: CodexReasoningEffort = CodexReasoningEffort.MEDIUM
    sandbox: CodexSandbox = CodexSandbox.WORKSPACE_WRITE
    approval_mode: CodexApprovalMode = CodexApprovalMode.ON_REQUEST


AgentConfig = ClaudeAgentConfig | AmpAgentConfig | CodexAgentConfig


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
    "CodexAgentConfig",
    "CodexApprovalMode",
    "CodexModel",
    "CodexReasoningEffort",
    "CodexSandbox",
    "CommonAgentConfig",
]
