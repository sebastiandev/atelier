"""Provider specs: descriptor for the UI + builder for typed configs.

Each ``Spec`` plays two roles:

  1. ``describe()`` returns a ``ProviderDescriptor`` — JSON-friendly
     metadata the frontend's new-agent dialog renders into form fields
     (primary selector + extra options).
  2. ``build()`` takes the wire-format request (``model`` string, free
     ``options`` dict) and produces a typed ``AgentConfig`` with all
     enum coercion + validation done. Unknown option keys are rejected.

The same ``Spec`` instance is consulted by both ``GET /api/providers``
and ``POST /api/works/.../agents`` so the descriptor and the validator
cannot drift.

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
    AgentConfig,
    AmpAgentConfig,
    AmpMode,
    AmpPermissionMode,
    ClaudeAgentConfig,
    ClaudeEffort,
    ClaudeModel,
    ClaudePermissionMode,
    CodexAgentConfig,
    CodexApprovalMode,
    CodexModel,
    CodexReasoningEffort,
    CodexSandbox,
    CommonAgentConfig,
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


_CLAUDE_MODEL_META: dict[str, ModelMeta] = {
    # Anthropic public list pricing per million tokens (USD). Cache write
    # ~1.25x input, cache read ~0.10x input - encoded explicitly here
    # so the FE doesn't have to derive it.
    #
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
                default=ClaudeModel.OPUS_4_8.value,
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
        return ClaudeAgentConfig(
            common=common,
            model=ClaudeModel(model),
            thinking_effort=ClaudeEffort(options.get("thinking_effort", ClaudeEffort.OFF.value)),
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
            model=CodexModel(model),
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


SPECS: dict[Provider, Spec] = {
    "claude-code": ClaudeSpec(),
    "amp": AmpSpec(),
    "codex": CodexSpec(),
}


__all__ = [
    "SPECS",
    "AmpSpec",
    "ClaudeSpec",
    "CodexSpec",
    "EnumOption",
    "ModelMeta",
    "ProviderDescriptor",
    "Spec",
    "TextOption",
]
