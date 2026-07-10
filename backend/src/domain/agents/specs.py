"""Provider specs: descriptor for the UI + builder for typed configs.

Each ``Spec`` plays two roles:

  1. ``describe()`` returns a ``ProviderDescriptor`` — JSON-friendly
     metadata the frontend's new-agent dialog renders into form fields
     (primary selector + extra options).
  2. ``build()`` takes the wire-format request (``model`` string, free
     ``options`` dict) and produces a typed ``AgentConfig`` with all
     enum coercion + validation done. Unknown option keys are rejected.

The same ``Spec`` instance is used for public descriptors and
``POST /api/works/.../agents`` validation. ``GET /api/providers``
filters that registry through ``NEW_SESSION_PROVIDERS`` so legacy
providers can keep validating/resuming old agents without being offered
for new sessions.

Wire convention: the top-level ``model: str`` field is each provider's
*primary selector*. Claude interprets it as a model id; Amp interprets
it as a mode (smart/rush/deep). The descriptor's ``primary_field``
labels it appropriately for the UI.
"""

from dataclasses import dataclass, field
from enum import Enum
from typing import Any, ClassVar, Protocol

from src.domain.agents.configs import (
    AMP_DEFAULT_AUTO_ALLOWED_TOOLS,
    OPENCODE_CONFIGURED_MODEL,
    AgentConfig,
    AmpAgentConfig,
    AmpMode,
    AmpPermissionMode,
    ClaudeAcpAgentConfig,
    ClaudeAcpEffort,
    ClaudeAcpModel,
    ClaudeAcpPermissionMode,
    ClaudeAgentConfig,
    ClaudeEffort,
    ClaudeModel,
    ClaudePermissionMode,
    CodexAcpAgentConfig,
    CodexAcpEffort,
    CodexAcpFastMode,
    CodexAcpMode,
    CodexAcpModel,
    CodexAgentConfig,
    CodexApprovalMode,
    CodexModel,
    CodexReasoningEffort,
    CodexSandbox,
    CommonAgentConfig,
    OpenCodeAgentConfig,
    OpenCodeMode,
)
from src.domain.models import Provider


@dataclass(frozen=True, kw_only=True)
class EnumOption:
    """An enum-valued form field. ``values`` is the allowed set,
    ``default`` is one of them. ``value_labels`` is an optional
    human-readable label per value (same length as ``values``); the
    frontend renders them as the dropdown option text and falls back
    to the raw value when missing."""

    label: str
    values: list[str]
    default: str
    value_labels: list[str] | None = None


@dataclass(frozen=True, kw_only=True)
class TextOption:
    """A free-text form field. Renders as a textarea on the frontend.

    ``visible_when`` is an optional ``(option_key, value)`` predicate:
    the dialog only renders this field when the named enum option is
    set to that value. Lets us, for example, only show "Custom allowed
    tools" when ``permission_mode == "custom"``.
    """

    label: str
    default: str
    placeholder: str | None = None
    hint: str | None = None
    visible_when: tuple[str, str] | None = None


@dataclass(frozen=True, kw_only=True)
class ModelMeta:
    """Per-primary-value pricing + context window, for cost / ctx% display.

    Keyed in ``ProviderDescriptor.model_meta`` by the primary-field value
    (model id for Claude, mode for Amp). Any field may be ``None`` when
    the provider doesn't expose a stable mapping — Amp modes route to
    different underlying models without a public price, so their entries
    are blank and the UI shows "—" rather than a guess.
    """

    context_window: int | None = None
    input_per_mtok: float | None = None
    output_per_mtok: float | None = None
    cache_read_per_mtok: float | None = None
    cache_write_per_mtok: float | None = None
    effort_values: tuple[str, ...] | None = None
    effort_default: str | None = None


@dataclass(frozen=True, kw_only=True)
class ProviderDescriptor:
    name: Provider
    label: str
    primary_field: EnumOption
    options: dict[str, EnumOption]
    # Free-text fields for advanced settings that don't fit a fixed enum
    # (e.g. an Amp custom tool allowlist). Empty for providers that
    # don't need any.
    text_options: dict[str, TextOption] = field(default_factory=dict)
    # Plain-text explainer rendered above the Advanced section. The
    # Amp permission picker uses this to clarify that the agent can
    # still ask for permission regardless of the auto-allow list.
    advanced_intro: str | None = None
    # Optional pricing + context-window metadata, keyed by the primary
    # field's value. Empty dict is a valid response — providers may
    # legitimately have nothing to say (e.g. Amp's mode → underlying
    # model mapping isn't public).
    model_meta: dict[str, ModelMeta] = field(default_factory=dict)


class Spec(Protocol):
    name: ClassVar[Provider]
    label: ClassVar[str]

    def describe(self) -> ProviderDescriptor: ...

    def build(
        self, common: CommonAgentConfig, model: str, options: dict[str, Any]
    ) -> AgentConfig: ...


def _enum_values(enum_cls: type[Enum]) -> list[str]:
    return [member.value for member in enum_cls]


def _reject_unknown(provider: Provider, options: dict[str, Any], allowed: set[str]) -> None:
    unknown = set(options) - allowed
    if unknown:
        raise ValueError(
            f"unknown options for provider {provider!r}: {sorted(unknown)}"
        )


_MISSING = object()


def _claude_effort_for_model(
    model: ClaudeModel, raw_effort: Any = _MISSING
) -> ClaudeEffort:
    meta = _CLAUDE_MODEL_META.get(model.value)
    allowed_values = (
        meta.effort_values
        if meta is not None and meta.effort_values is not None
        else tuple(_enum_values(ClaudeEffort))
    )
    default = (
        meta.effort_default
        if meta is not None and meta.effort_default is not None
        else ClaudeEffort.OFF.value
    )
    effort = ClaudeEffort(default if raw_effort is _MISSING else raw_effort)
    if effort.value not in allowed_values:
        raise ValueError(
            f"thinking_effort {effort.value!r} is not available for model "
            f"{model.value!r}; choose one of {list(allowed_values)!r}"
        )
    return effort


_CLAUDE_MODEL_META: dict[str, ModelMeta] = {
    # Anthropic public list pricing per million tokens (USD). Cache write
    # ~1.25x input, cache read ~0.10x input - encoded explicitly here
    # so the FE doesn't have to derive it.
    #
    ClaudeModel.FABLE_5.value: ModelMeta(
        context_window=1_000_000,
        input_per_mtok=15.0,
        output_per_mtok=75.0,
        cache_read_per_mtok=1.50,
        cache_write_per_mtok=18.75,
        effort_values=(
            ClaudeEffort.LOW.value,
            ClaudeEffort.MEDIUM.value,
            ClaudeEffort.HIGH.value,
            ClaudeEffort.XHIGH.value,
            ClaudeEffort.MAX.value,
        ),
        effort_default=ClaudeEffort.XHIGH.value,
    ),
    ClaudeModel.OPUS_4_8.value: ModelMeta(
        context_window=1_000_000,
        input_per_mtok=5.0,
        output_per_mtok=25.0,
        cache_read_per_mtok=0.50,
        cache_write_per_mtok=6.25,
    ),
    ClaudeModel.OPUS_4_7_1M.value: ModelMeta(
        context_window=1_000_000,
        input_per_mtok=5.0,
        output_per_mtok=25.0,
        cache_read_per_mtok=0.50,
        cache_write_per_mtok=6.25,
    ),
    ClaudeModel.OPUS_4_7.value: ModelMeta(
        context_window=1_000_000,
        input_per_mtok=5.0,
        output_per_mtok=25.0,
        cache_read_per_mtok=0.50,
        cache_write_per_mtok=6.25,
    ),
    ClaudeModel.SONNET_4_6.value: ModelMeta(
        context_window=200_000,
        input_per_mtok=3.0,
        output_per_mtok=15.0,
        cache_read_per_mtok=0.30,
        cache_write_per_mtok=3.75,
    ),
    ClaudeModel.HAIKU_4_5.value: ModelMeta(
        context_window=200_000,
        input_per_mtok=1.0,
        output_per_mtok=5.0,
        cache_read_per_mtok=0.10,
        cache_write_per_mtok=1.25,
    ),
}

_CODEX_MODEL_META: dict[str, ModelMeta] = {
    # Pricing comes from OpenAI's public API model pages. Context windows
    # are only filled when OpenAI has documented the Codex-side limit or
    # the default non-experimental window for Codex specifically; API
    # windows can be larger than what Codex uses in practice.
    # 5.6 ChatGPT-login Codex aliases do not have published stable
    # pricing/window metadata yet, so keep their entries blank.
    CodexModel.GPT_5_6_TERRA.value: ModelMeta(),
    CodexModel.GPT_5_6_LUNA.value: ModelMeta(),
    CodexModel.GPT_5_6_SOL.value: ModelMeta(),
    CodexModel.GPT_5_5.value: ModelMeta(
        context_window=400_000,
        input_per_mtok=5.0,
        output_per_mtok=30.0,
        cache_read_per_mtok=0.50,
    ),
    CodexModel.GPT_5_5_PRO.value: ModelMeta(
        input_per_mtok=30.0,
        output_per_mtok=180.0,
        # GPT-5.5 Pro has no cached-input discount.
        cache_read_per_mtok=30.0,
        cache_write_per_mtok=30.0,
    ),
    CodexModel.GPT_5_4.value: ModelMeta(
        context_window=272_000,
        input_per_mtok=2.50,
        output_per_mtok=15.0,
        cache_read_per_mtok=0.25,
    ),
    CodexModel.GPT_5_4_PRO.value: ModelMeta(
        input_per_mtok=30.0,
        output_per_mtok=180.0,
        # GPT-5.4 Pro has no cached-input discount.
        cache_read_per_mtok=30.0,
        cache_write_per_mtok=30.0,
    ),
}


_CODEX_MODEL_ALIASES = {
    "5.6 terra": CodexModel.GPT_5_6_TERRA.value,
    "gpt 5.6 terra": CodexModel.GPT_5_6_TERRA.value,
    "gpt.5.6-terra": CodexModel.GPT_5_6_TERRA.value,
    "gpt-5.6 terra": CodexModel.GPT_5_6_TERRA.value,
    "5.6 luna": CodexModel.GPT_5_6_LUNA.value,
    "gpt 5.6 luna": CodexModel.GPT_5_6_LUNA.value,
    "5.6 sol": CodexModel.GPT_5_6_SOL.value,
    "gpt 5.6 sol": CodexModel.GPT_5_6_SOL.value,
}


def _normalize_codex_model(model: str) -> str:
    normalized = model.strip().lower()
    return _CODEX_MODEL_ALIASES.get(normalized, normalized)


def _normalize_codex_acp_fast_mode(value: Any) -> str:
    if isinstance(value, bool):
        return CodexAcpFastMode.ON.value if value else CodexAcpFastMode.OFF.value
    return value


class ClaudeSpec:
    name: ClassVar[Provider] = "claude-code"
    label: ClassVar[str] = "Claude Code (Anthropic)"

    _allowed_options: ClassVar[set[str]] = {"thinking_effort", "permission_mode"}

    def describe(self) -> ProviderDescriptor:
        return ProviderDescriptor(
            name=self.name,
            label=self.label,
            primary_field=EnumOption(
                label="Model",
                values=_enum_values(ClaudeModel),
                default=ClaudeModel.FABLE_5.value,
            ),
            options={
                "thinking_effort": EnumOption(
                    label="Thinking effort",
                    values=_enum_values(ClaudeEffort),
                    default=ClaudeEffort.XHIGH.value,
                ),
                "permission_mode": EnumOption(
                    label="Permission mode",
                    values=_enum_values(ClaudePermissionMode),
                    default=ClaudePermissionMode.DEFAULT.value,
                    value_labels=[
                        "Ask per tool",
                        "Auto-accept edits",
                        "Plan only (no execution)",
                        "Bypass all permissions (risky)",
                    ],
                ),
            },
            model_meta=dict(_CLAUDE_MODEL_META),
        )

    def build(
        self, common: CommonAgentConfig, model: str, options: dict[str, Any]
    ) -> ClaudeAgentConfig:
        _reject_unknown(self.name, options, self._allowed_options)
        model_value = ClaudeModel(model)
        raw_effort = (
            options["thinking_effort"] if "thinking_effort" in options else _MISSING
        )
        effort = _claude_effort_for_model(model_value, raw_effort)
        return ClaudeAgentConfig(
            common=common,
            model=model_value,
            thinking_effort=effort,
            permission_mode=ClaudePermissionMode(
                options.get("permission_mode", ClaudePermissionMode.DEFAULT.value)
            ),
        )


class AmpSpec:
    name: ClassVar[Provider] = "amp"
    label: ClassVar[str] = "Amp (Sourcegraph)"

    _allowed_options: ClassVar[set[str]] = {"permission_mode", "custom_allowed_tools"}

    _AMP_ADVANCED_INTRO: ClassVar[str] = (
        "Permissions decide which tools auto-run vs. trigger an inline "
        "Allow / Allow always / Deny prompt. Amp doesn't expose a real-time "
        "permission callback, so Atelier gates Bash through a delegate "
        "shim — the agent can still request approval for shell commands "
        "regardless of which mode you pick. Heads up: every other tool "
        "(Edit / Write / Read / web fetches / MCP / anything new Amp "
        "ships) auto-runs without a prompt — the Amp CLI's stream mode "
        "has no path to surface a permission request to the UI today. "
        "If you need finer-grained gating across all tools, the "
        "Claude Code provider offers it; on Amp, the only knob is "
        "ALLOW_ALL (which also skips Bash gating)."
    )

    def describe(self) -> ProviderDescriptor:
        default_csv = ", ".join(AMP_DEFAULT_AUTO_ALLOWED_TOOLS)
        # Each Amp mode has a known context window (rush/smart route to
        # 200k-window models, deep/large route to 1M-window ones). The
        # underlying model and its per-token pricing aren't part of Amp's
        # public surface, so pricing fields stay ``None`` — the FE shows
        # "—" for cost on Amp but renders ctx% from the window below.
        amp_meta = {
            AmpMode.SMART.value: ModelMeta(context_window=200_000),
            AmpMode.RUSH.value: ModelMeta(context_window=200_000),
            AmpMode.DEEP.value: ModelMeta(context_window=1_000_000),
            AmpMode.LARGE.value: ModelMeta(context_window=1_000_000),
        }
        return ProviderDescriptor(
            name=self.name,
            label=self.label,
            primary_field=EnumOption(
                label="Mode",
                values=_enum_values(AmpMode),
                default=AmpMode.SMART.value,
            ),
            options={
                "permission_mode": EnumOption(
                    label="Permissions",
                    values=_enum_values(AmpPermissionMode),
                    default=AmpPermissionMode.DEFAULT.value,
                    value_labels=[
                        "Default (Bash gated, common tools allowed)",
                        "Bypass all permissions (risky)",
                        "Custom allowlist (Bash always gated)",
                    ],
                ),
            },
            text_options={
                "custom_allowed_tools": TextOption(
                    label="Custom auto-allowed tools",
                    default=default_csv,
                    placeholder="Read, Grep, Glob, edit_file, …",
                    hint=(
                        "Comma-separated tool names to skip the prompt for. "
                        "Bash always stays gated regardless of this list."
                    ),
                    visible_when=("permission_mode", AmpPermissionMode.CUSTOM.value),
                ),
            },
            advanced_intro=self._AMP_ADVANCED_INTRO,
            model_meta=amp_meta,
        )

    def build(
        self, common: CommonAgentConfig, model: str, options: dict[str, Any]
    ) -> AmpAgentConfig:
        _reject_unknown(self.name, options, self._allowed_options)
        permission_mode = AmpPermissionMode(
            options.get("permission_mode", AmpPermissionMode.DEFAULT.value)
        )
        custom_raw = options.get("custom_allowed_tools", "")
        if isinstance(custom_raw, list):
            custom_tools = tuple(str(t).strip() for t in custom_raw if str(t).strip())
        elif isinstance(custom_raw, str):
            custom_tools = tuple(t.strip() for t in custom_raw.split(",") if t.strip())
        else:
            raise ValueError(
                "custom_allowed_tools must be a string or list of tool names"
            )
        return AmpAgentConfig(
            common=common,
            mode=AmpMode(model),
            permission_mode=permission_mode,
            custom_allowed_tools=custom_tools,
        )


class CodexSpec:
    name: ClassVar[Provider] = "codex"
    label: ClassVar[str] = "Codex (OpenAI)"

    _allowed_options: ClassVar[set[str]] = {
        "reasoning_effort",
        "sandbox",
        "approval_mode",
    }

    _CODEX_ADVANCED_INTRO: ClassVar[str] = (
        "Codex has two independent permission layers. Sandbox restricts "
        "what the agent can touch on disk regardless of model intent — "
        "``workspace-write`` (the default) confines writes to the agent's "
        "worktree plus any mounted project shared folders. Approval picks "
        "Codex's own ask policy before running commands or applying "
        "patches; the current Codex SDK does not surface those prompts "
        "through Atelier's Allow / Deny UI."
    )

    def describe(self) -> ProviderDescriptor:
        return ProviderDescriptor(
            name=self.name,
            label=self.label,
            primary_field=EnumOption(
                label="Model",
                values=_enum_values(CodexModel),
                default=CodexModel.GPT_5_5.value,
            ),
            options={
                "reasoning_effort": EnumOption(
                    label="Reasoning effort",
                    values=_enum_values(CodexReasoningEffort),
                    default=CodexReasoningEffort.MEDIUM.value,
                ),
                "sandbox": EnumOption(
                    label="Sandbox",
                    values=_enum_values(CodexSandbox),
                    default=CodexSandbox.WORKSPACE_WRITE.value,
                    value_labels=[
                        "Read-only",
                        "Workspace write (default)",
                        "Full access (risky)",
                    ],
                ),
                "approval_mode": EnumOption(
                    label="Approval mode",
                    values=_enum_values(CodexApprovalMode),
                    default=CodexApprovalMode.ON_REQUEST.value,
                    value_labels=[
                        "Never (auto-run)",
                        "On request (Atelier prompts)",
                        "On failure",
                        "Untrusted (prompt every tool)",
                    ],
                ),
            },
            advanced_intro=self._CODEX_ADVANCED_INTRO,
            model_meta=dict(_CODEX_MODEL_META),
        )

    def build(
        self, common: CommonAgentConfig, model: str, options: dict[str, Any]
    ) -> CodexAgentConfig:
        _reject_unknown(self.name, options, self._allowed_options)
        return CodexAgentConfig(
            common=common,
            model=CodexModel(_normalize_codex_model(model)),
            reasoning_effort=CodexReasoningEffort(
                options.get(
                    "reasoning_effort", CodexReasoningEffort.MEDIUM.value
                )
            ),
            sandbox=CodexSandbox(
                options.get("sandbox", CodexSandbox.WORKSPACE_WRITE.value)
            ),
            approval_mode=CodexApprovalMode(
                options.get(
                    "approval_mode", CodexApprovalMode.ON_REQUEST.value
                )
            ),
        )


_CLAUDE_ACP_MODEL_META: dict[str, ModelMeta] = {
    # Wrapper option values are runtime aliases; pricing/window mirror the
    # models they resolve to (per the wrapper's own option descriptions,
    # captured 2026-06-11): default→Opus 4.8 (1M), sonnet[1m] keeps
    # standard Sonnet pricing here — long-context surcharges are not
    # modelled. ACP agents report authoritative cost via usage_update,
    # so these numbers only back the dialog's price hints and the FE's
    # fallback estimate.
    ClaudeAcpModel.DEFAULT.value: ModelMeta(
        context_window=1_000_000,
        input_per_mtok=5.0,
        output_per_mtok=25.0,
        cache_read_per_mtok=0.50,
        cache_write_per_mtok=6.25,
    ),
    ClaudeAcpModel.FABLE_5_1M.value: ModelMeta(
        context_window=1_000_000,
        input_per_mtok=15.0,
        output_per_mtok=75.0,
        cache_read_per_mtok=1.50,
        cache_write_per_mtok=18.75,
        effort_default=ClaudeAcpEffort.XHIGH.value,
    ),
    ClaudeAcpModel.SONNET.value: ModelMeta(
        context_window=200_000,
        input_per_mtok=3.0,
        output_per_mtok=15.0,
        cache_read_per_mtok=0.30,
        cache_write_per_mtok=3.75,
    ),
    ClaudeAcpModel.SONNET_1M.value: ModelMeta(
        context_window=1_000_000,
        input_per_mtok=3.0,
        output_per_mtok=15.0,
        cache_read_per_mtok=0.30,
        cache_write_per_mtok=3.75,
    ),
    ClaudeAcpModel.HAIKU.value: ModelMeta(
        context_window=200_000,
        input_per_mtok=1.0,
        output_per_mtok=5.0,
        cache_read_per_mtok=0.10,
        cache_write_per_mtok=1.25,
    ),
}


class ClaudeAcpSpec:
    """Claude through the official claude-agent-acp wrapper (ACP runtime).

    Coexists with ``ClaudeSpec`` (the bespoke claude-agent-sdk adapter)
    while the ACP path is validated. Model values are the wrapper's own
    config-option aliases, not API model ids.
    """

    name: ClassVar[Provider] = "claude-acp"
    label: ClassVar[str] = "Claude Code (Anthropic)"

    _allowed_options: ClassVar[set[str]] = {"thinking_effort", "permission_mode"}

    _ADVANCED_INTRO: ClassVar[str] = (
        "Runs Claude Code through the Agent Client Protocol (the same "
        "wrapper Zed and JetBrains embed). Permission prompts round-trip "
        "through Atelier's Allow / Deny UI. 'Auto' uses a model "
        "classifier to settle prompts; 'Don't ask' denies anything not "
        "pre-approved. Settings are applied per-session and override "
        "your Claude CLI defaults."
    )

    def describe(self) -> ProviderDescriptor:
        return ProviderDescriptor(
            name=self.name,
            label=self.label,
            primary_field=EnumOption(
                label="Model",
                values=_enum_values(ClaudeAcpModel),
                default=ClaudeAcpModel.DEFAULT.value,
                value_labels=[
                    "CLI default — Opus 4.8 1M (recommended)",
                    "Fable 5 (1M)",
                    "Sonnet 4.6",
                    "Sonnet 4.6 (1M)",
                    "Haiku 4.5",
                ],
            ),
            options={
                "thinking_effort": EnumOption(
                    label="Thinking effort",
                    values=_enum_values(ClaudeAcpEffort),
                    default=ClaudeAcpEffort.DEFAULT.value,
                ),
                "permission_mode": EnumOption(
                    label="Permission mode",
                    values=_enum_values(ClaudeAcpPermissionMode),
                    default=ClaudeAcpPermissionMode.DEFAULT.value,
                    value_labels=[
                        "Auto (classifier decides)",
                        "Ask per tool",
                        "Auto-accept edits",
                        "Plan only (no execution)",
                        "Don't ask (deny unapproved)",
                        "Bypass all permissions (risky)",
                    ],
                ),
            },
            advanced_intro=self._ADVANCED_INTRO,
            model_meta=dict(_CLAUDE_ACP_MODEL_META),
        )

    def build(
        self, common: CommonAgentConfig, model: str, options: dict[str, Any]
    ) -> ClaudeAcpAgentConfig:
        _reject_unknown(self.name, options, self._allowed_options)
        return ClaudeAcpAgentConfig(
            common=common,
            model=ClaudeAcpModel(model),
            thinking_effort=ClaudeAcpEffort(
                options.get("thinking_effort", ClaudeAcpEffort.DEFAULT.value)
            ),
            permission_mode=ClaudeAcpPermissionMode(
                options.get("permission_mode", ClaudeAcpPermissionMode.DEFAULT.value)
            ),
        )


_CODEX_ACP_MODEL_META: dict[str, ModelMeta] = {
    # gpt-5.5 / gpt-5.4 mirror the bespoke Codex meta; gpt-5.4-mini and
    # 5.6 ChatGPT-login aliases have no published Codex-side
    # pricing/window, so their meta stays blank (the FE shows "—" and
    # relies on usage_update for real numbers).
    CodexAcpModel.GPT_5_6_TERRA.value: ModelMeta(),
    CodexAcpModel.GPT_5_6_LUNA.value: ModelMeta(),
    CodexAcpModel.GPT_5_6_SOL.value: ModelMeta(),
    CodexAcpModel.GPT_5_5.value: ModelMeta(
        context_window=400_000,
        input_per_mtok=5.0,
        output_per_mtok=30.0,
        cache_read_per_mtok=0.50,
    ),
    CodexAcpModel.GPT_5_4.value: ModelMeta(
        context_window=272_000,
        input_per_mtok=2.50,
        output_per_mtok=15.0,
        cache_read_per_mtok=0.25,
    ),
    CodexAcpModel.GPT_5_4_MINI.value: ModelMeta(),
}


class CodexAcpSpec:
    """Codex through the Agent Client Protocol codex-acp wrapper (ACP runtime).

    Coexists with ``CodexSpec`` (openai-codex-sdk app-server adapter)
    while the ACP path is validated. Unlike the bespoke adapter's
    independent sandbox/approval knobs, codex-acp exposes Codex's own
    three-tier mode — and its approval prompts round-trip through
    Atelier's permission UI, which the bespoke SDK path can't do in
    legacy mode.
    """

    name: ClassVar[Provider] = "codex-acp"
    label: ClassVar[str] = "Codex (OpenAI)"

    _allowed_options: ClassVar[set[str]] = {"reasoning_effort", "mode", "fast-mode"}

    _ADVANCED_INTRO: ClassVar[str] = (
        "Runs Codex through the Agent Client Protocol. Mode is Codex's "
        "own access policy: Read Only (approval to edit/run), Auto "
        "(work freely in the workspace, approval for network or outside "
        "edits — the default), or Full Access (no approvals — risky). "
        "Approval prompts appear in Atelier's Allow / Deny UI."
    )

    def describe(self) -> ProviderDescriptor:
        return ProviderDescriptor(
            name=self.name,
            label=self.label,
            primary_field=EnumOption(
                label="Model",
                values=_enum_values(CodexAcpModel),
                default=CodexAcpModel.GPT_5_5.value,
            ),
            options={
                "reasoning_effort": EnumOption(
                    label="Reasoning effort",
                    values=_enum_values(CodexAcpEffort),
                    default=CodexAcpEffort.MEDIUM.value,
                ),
                "mode": EnumOption(
                    label="Mode",
                    values=_enum_values(CodexAcpMode),
                    default=CodexAcpMode.AUTO.value,
                    value_labels=[
                        "Read only (ask to edit/run)",
                        "Auto — workspace access (default)",
                        "Full access (risky)",
                    ],
                ),
                "fast-mode": EnumOption(
                    label="Fast mode",
                    values=_enum_values(CodexAcpFastMode),
                    default=CodexAcpFastMode.OFF.value,
                    value_labels=[
                        "Off",
                        "On - 1.5x speed, increased usage",
                    ],
                ),
            },
            advanced_intro=self._ADVANCED_INTRO,
            model_meta=dict(_CODEX_ACP_MODEL_META),
        )

    def build(
        self, common: CommonAgentConfig, model: str, options: dict[str, Any]
    ) -> CodexAcpAgentConfig:
        _reject_unknown(self.name, options, self._allowed_options)
        return CodexAcpAgentConfig(
            common=common,
            model=CodexAcpModel(_normalize_codex_model(model)),
            reasoning_effort=CodexAcpEffort(
                options.get("reasoning_effort", CodexAcpEffort.MEDIUM.value)
            ),
            mode=CodexAcpMode(options.get("mode", CodexAcpMode.AUTO.value)),
            fast_mode=CodexAcpFastMode(
                _normalize_codex_acp_fast_mode(
                    options.get("fast-mode", CodexAcpFastMode.OFF.value)
                )
            ),
        )


class OpenCodeSpec:
    """OpenCode via its native ACP server.

    The model is configured-default by design: OpenCode's model list is
    per-user (Ollama / LM Studio / zen models / any provider the user
    wired up), so Atelier passes through whatever the user's OpenCode
    config selects rather than pretending to enumerate it at descriptor
    time. Once a session is running, ACP configOptions drive the live
    model picker.
    """

    name: ClassVar[Provider] = "opencode"
    label: ClassVar[str] = "OpenCode"

    _allowed_options: ClassVar[set[str]] = {"mode"}

    _ADVANCED_INTRO: ClassVar[str] = (
        "Runs OpenCode through the Agent Client Protocol. The model is "
        "whatever your OpenCode config selects (run `opencode models` "
        "to inspect, `opencode providers` to add providers — including "
        "local Ollama / LM Studio endpoints). Atelier does not manage "
        "or validate local model servers. Permission prompts round-trip "
        "through Atelier's Allow / Deny UI."
    )

    def describe(self) -> ProviderDescriptor:
        return ProviderDescriptor(
            name=self.name,
            label=self.label,
            primary_field=EnumOption(
                label="Model",
                values=[OPENCODE_CONFIGURED_MODEL],
                default=OPENCODE_CONFIGURED_MODEL,
                value_labels=["OpenCode default (set in OpenCode config)"],
            ),
            options={
                "mode": EnumOption(
                    label="Mode",
                    values=_enum_values(OpenCodeMode),
                    default=OpenCodeMode.BUILD.value,
                    value_labels=[
                        "Build (full agent)",
                        "Plan (design only, no execution)",
                    ],
                ),
            },
            advanced_intro=self._ADVANCED_INTRO,
            # No pricing/window meta: the underlying model is unknown to
            # Atelier. Context % and cost come from ACP usage_update at
            # runtime when OpenCode reports them.
        )

    def build(
        self, common: CommonAgentConfig, model: str, options: dict[str, Any]
    ) -> OpenCodeAgentConfig:
        _reject_unknown(self.name, options, self._allowed_options)
        return OpenCodeAgentConfig(
            common=common,
            model=model,
            mode=OpenCodeMode(options.get("mode", OpenCodeMode.BUILD.value)),
        )


SPECS: dict[Provider, Spec] = {
    "claude-code": ClaudeSpec(),
    "amp": AmpSpec(),
    "codex": CodexSpec(),
    "claude-acp": ClaudeAcpSpec(),
    "codex-acp": CodexAcpSpec(),
    "opencode": OpenCodeSpec(),
}

NEW_SESSION_PROVIDERS: tuple[Provider, ...] = (
    "claude-acp",
    "amp",
    "codex-acp",
    "opencode",
)
"""Provider ids exposed for new agents/chats.

Legacy ``claude-code`` / ``codex`` stay registered in ``SPECS`` so
existing agents can resume and old records still validate, but new
sessions should use the ACP-backed runtimes.
"""


__all__ = [
    "NEW_SESSION_PROVIDERS",
    "SPECS",
    "AmpSpec",
    "ClaudeAcpSpec",
    "ClaudeSpec",
    "CodexAcpSpec",
    "CodexSpec",
    "EnumOption",
    "ModelMeta",
    "OpenCodeSpec",
    "ProviderDescriptor",
    "Spec",
    "TextOption",
]
