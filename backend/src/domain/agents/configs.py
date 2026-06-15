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

# Keep ``str, Enum`` rather than ``StrEnum`` for backward-compatible
# stringification in persisted/config-adjacent paths.
# ruff: noqa: UP042

from dataclasses import dataclass
from enum import Enum
from pathlib import Path


class ClaudeModel(str, Enum):
    """Claude model aliases accepted by the Claude Agent SDK / CLI.

    These are the canonical short-form names (the CLI also accepts
    versioned snapshots like ``claude-haiku-4-5-20251001`` if you need
    to pin a specific date).

    The ``[1m]`` suffix is the Claude Code CLI's historical opt-in for
    the 1M extended-context tier. Opus 4.8 supports 1M by default on the
    first-party Claude API, so it uses the plain model id here.
    """

    FABLE_5 = "claude-fable-5"
    OPUS_4_8 = "claude-opus-4-8"
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
    # Extra directories an adapter may add to its sandbox. Today this is
    # used by Codex so workspace-write agents can write project shared
    # folders whose symlink targets live outside the per-agent worktree.
    writable_roots: tuple[Path, ...] = ()


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
    summary_only: bool = False


@dataclass(frozen=True, kw_only=True)
class AmpAgentConfig:
    common: CommonAgentConfig
    mode: AmpMode = AmpMode.SMART
    permission_mode: AmpPermissionMode = AmpPermissionMode.DEFAULT
    # Only meaningful when ``permission_mode == CUSTOM``. Bash stays gated
    # through the bridge regardless — including ``"Bash"`` here is a no-op.
    custom_allowed_tools: tuple[str, ...] = ()
    summary_only: bool = False


class CodexModel(str, Enum):
    """OpenAI Codex model aliases accepted by the Codex SDK / CLI.

    The Codex CLI/SDK accepts these short-form names. Newer date-stamped
    snapshots can be passed through the runtime config, but pinning here
    keeps the new-agent dialog deterministic.
    """

    GPT_5_5 = "gpt-5.5"
    GPT_5_5_PRO = "gpt-5.5-pro"
    GPT_5_4 = "gpt-5.4"
    GPT_5_4_PRO = "gpt-5.4-pro"


class CodexReasoningEffort(str, Enum):
    """Codex reasoning effort ladder.

    Mirrors the Codex CLI's ``model_reasoning_effort`` knob. Higher values
    grant a longer reasoning budget at the cost of latency + tokens billed.
    """

    MINIMAL = "minimal"
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
    """Approval policy forwarded to Codex.

    Maps directly to Codex's ``--ask-for-approval`` ladder. With the
    current ``openai-codex-sdk`` path, approval prompts are handled by
    Codex's own runtime rather than Atelier's ``PermissionRequest`` UI;
    ``never`` auto-runs everything and ``untrusted`` is paranoid (every
    tool prompts).
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
    model: CodexModel = CodexModel.GPT_5_5
    reasoning_effort: CodexReasoningEffort = CodexReasoningEffort.MEDIUM
    sandbox: CodexSandbox = CodexSandbox.WORKSPACE_WRITE
    approval_mode: CodexApprovalMode = CodexApprovalMode.ON_REQUEST
    summary_only: bool = False


class ClaudeAcpModel(str, Enum):
    """Model choices exposed by the official ``claude-agent-acp`` wrapper.

    These are the wrapper's session-config-option *values* (captured live
    2026-06-11, wrapper 0.44.0) — aliases resolved by the Claude Code
    runtime, not API model ids. ``DEFAULT`` defers to the user's Claude
    CLI configuration (currently resolves to Opus 4.8 with 1M context).
    """

    DEFAULT = "default"
    FABLE_5_1M = "claude-fable-5[1m]"
    SONNET = "sonnet"
    SONNET_1M = "sonnet[1m]"
    HAIKU = "haiku"


class ClaudeAcpEffort(str, Enum):
    """Thinking-effort ladder of the claude-agent-acp ``effort`` option.

    Same ladder as ``ClaudeEffort`` except the wrapper models "no
    override" as ``default`` (defer to CLI config) rather than ``off``.
    """

    DEFAULT = "default"
    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"
    XHIGH = "xhigh"
    MAX = "max"


class ClaudeAcpPermissionMode(str, Enum):
    """Permission modes of the claude-agent-acp ``mode`` option.

    Superset of ``ClaudePermissionMode``: ``AUTO`` uses a model
    classifier to approve/deny prompts, ``DONT_ASK`` denies anything not
    pre-approved. Prompts that do fire round-trip through Atelier's
    Allow / Deny UI via ACP ``session/request_permission``.
    """

    AUTO = "auto"
    DEFAULT = "default"
    ACCEPT_EDITS = "acceptEdits"
    PLAN = "plan"
    DONT_ASK = "dontAsk"
    BYPASS = "bypassPermissions"


@dataclass(frozen=True, kw_only=True)
class AcpAgentConfig:
    """Shared base for providers driven through the ACP client adapter.

    One ``AcpAdapter`` serves every ACP provider; per-provider configs
    subclass this base (``functools.singledispatch`` resolves subclasses
    to the base registration via the MRO) and express their knobs as
    protocol-level values through the two hooks below. The adapter
    applies them **tolerantly** after ``session/new``: ids/values the
    agent doesn't advertise are skipped with a debug log, never a start
    failure — adapter wrappers evolve their option surface faster than
    we ship.

    Subclassing (vs. the ``common`` composition used by the bespoke
    configs) is deliberate here: the adapter dispatches on the base type
    and only ever reads the shared surface, so the inheritance is the
    point. ``kw_only=True`` keeps frozen-dataclass field ordering sane.
    """

    common: CommonAgentConfig
    summary_only: bool = False

    def acp_config_values(self) -> tuple[tuple[str, str], ...]:
        """``(config_id, value)`` pairs to apply via session/set_config_option."""
        return ()

    def acp_mode_id(self) -> str | None:
        """Session mode to apply via session/set_mode, or None to leave as-is."""
        return None


@dataclass(frozen=True, kw_only=True)
class ClaudeAcpAgentConfig(AcpAgentConfig):
    """Claude via the official claude-agent-acp wrapper.

    All three knobs travel as ACP session config options (the wrapper
    exposes no session modes). Values are always sent explicitly — the
    wrapper otherwise inherits whatever the user's CLI config says,
    which would make Atelier agents non-deterministic across machines.
    """

    model: ClaudeAcpModel = ClaudeAcpModel.DEFAULT
    thinking_effort: ClaudeAcpEffort = ClaudeAcpEffort.DEFAULT
    permission_mode: ClaudeAcpPermissionMode = ClaudeAcpPermissionMode.DEFAULT

    def acp_config_values(self) -> tuple[tuple[str, str], ...]:
        return (
            ("model", self.model.value),
            ("effort", self.thinking_effort.value),
            ("mode", self.permission_mode.value),
        )


class CodexAcpModel(str, Enum):
    """Model values exposed by Zed's codex-acp wrapper (0.16.0, captured
    live 2026-06-11). No Pro tiers — the wrapper surfaces the standard
    Codex runtime models only."""

    GPT_5_5 = "gpt-5.5"
    GPT_5_4 = "gpt-5.4"
    GPT_5_4_MINI = "gpt-5.4-mini"


class CodexAcpEffort(str, Enum):
    """codex-acp ``reasoning_effort`` ladder — one tier above the bespoke
    adapter's (``xhigh`` is ACP-only today)."""

    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"
    XHIGH = "xhigh"


class CodexAcpMode(str, Enum):
    """codex-acp session modes (also mirrored as the ``mode`` config
    option). Collapses the bespoke adapter's independent sandbox +
    approval knobs into Codex's own three-tier policy:

    - ``READ_ONLY`` — read files only; approval required to edit or run.
    - ``AUTO`` — read/edit/run inside the workspace; approval for
      network access or out-of-workspace edits. Matches the bespoke
      default (workspace-write + on-request) and is Atelier's default.
    - ``FULL_ACCESS`` — no approvals; use only for trusted runs.
    """

    READ_ONLY = "read-only"
    AUTO = "auto"
    FULL_ACCESS = "full-access"


@dataclass(frozen=True, kw_only=True)
class CodexAcpAgentConfig(AcpAgentConfig):
    """Codex via Zed's codex-acp wrapper. All knobs travel as ACP
    session config options; sent explicitly so Atelier agents don't
    inherit per-machine Codex config."""

    model: CodexAcpModel = CodexAcpModel.GPT_5_5
    reasoning_effort: CodexAcpEffort = CodexAcpEffort.MEDIUM
    mode: CodexAcpMode = CodexAcpMode.AUTO

    def acp_config_values(self) -> tuple[tuple[str, str], ...]:
        return (
            ("model", self.model.value),
            ("reasoning_effort", self.reasoning_effort.value),
            ("mode", self.mode.value),
        )


OPENCODE_CONFIGURED_MODEL = "configured-default"
"""Sentinel primary-field value for OpenCode: don't send a model config
option — the session runs whatever the user's OpenCode config selects
(``opencode models`` to inspect). OpenCode's model list is per-user and
dynamic, so a static enum can't enumerate it; a descriptor-driven picker
fed by ACP configOptions is a named follow-up story."""


class OpenCodeMode(str, Enum):
    """OpenCode session modes (= OpenCode agents). ``build`` is the
    stock do-work mode; ``plan`` designs without executing."""

    BUILD = "build"
    PLAN = "plan"


@dataclass(frozen=True, kw_only=True)
class OpenCodeAgentConfig(AcpAgentConfig):
    """OpenCode via its native ``opencode acp`` server.

    ``configured-default`` suppresses the model config option so OpenCode
    uses its own default. Any explicit ``provider/model`` value travels
    as ACP ``model`` when the session advertises it.
    """

    model: str = OPENCODE_CONFIGURED_MODEL
    mode: OpenCodeMode = OpenCodeMode.BUILD

    def acp_config_values(self) -> tuple[tuple[str, str], ...]:
        values: list[tuple[str, str]] = [("mode", self.mode.value)]
        if self.model != OPENCODE_CONFIGURED_MODEL:
            values.insert(0, ("model", self.model))
        return tuple(values)


AgentConfig = ClaudeAgentConfig | AmpAgentConfig | CodexAgentConfig | AcpAgentConfig


__all__ = [
    "AMP_DEFAULT_AUTO_ALLOWED_TOOLS",
    "DEFAULT_ALLOWED_TOOLS",
    "OPENCODE_CONFIGURED_MODEL",
    "AcpAgentConfig",
    "AgentConfig",
    "AmpAgentConfig",
    "AmpMode",
    "AmpPermissionMode",
    "ClaudeAcpAgentConfig",
    "ClaudeAcpEffort",
    "ClaudeAcpModel",
    "ClaudeAcpPermissionMode",
    "ClaudeAgentConfig",
    "ClaudeEffort",
    "ClaudeModel",
    "ClaudePermissionMode",
    "CodexAcpAgentConfig",
    "CodexAcpEffort",
    "CodexAcpMode",
    "CodexAcpModel",
    "CodexAgentConfig",
    "CodexApprovalMode",
    "CodexModel",
    "CodexReasoningEffort",
    "CodexSandbox",
    "CommonAgentConfig",
    "OpenCodeAgentConfig",
    "OpenCodeMode",
]
