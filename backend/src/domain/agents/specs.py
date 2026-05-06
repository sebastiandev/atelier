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


class Spec(Protocol):
    name: ClassVar[Provider]
    label: ClassVar[str]

    def describe(self) -> ProviderDescriptor: ...

    def build(
        self, common: CommonAgentConfig, model: str, options: dict[str, Any]
    ) -> AgentConfig: ...


def _enum_values(enum_cls: type) -> list[str]:
    return [member.value for member in enum_cls]


def _reject_unknown(provider: Provider, options: dict[str, Any], allowed: set[str]) -> None:
    unknown = set(options) - allowed
    if unknown:
        raise ValueError(
            f"unknown options for provider {provider!r}: {sorted(unknown)}"
        )


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
                default=ClaudeModel.OPUS_4_7.value,
            ),
            options={
                "thinking_effort": EnumOption(
                    label="Thinking effort",
                    values=_enum_values(ClaudeEffort),
                    default=ClaudeEffort.OFF.value,
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
        "regardless of which mode you pick. Edit/Write/Read aren't gateable "
        "on Amp today; the safest mode auto-allows them along with the "
        "other read tools."
    )

    def describe(self) -> ProviderDescriptor:
        default_csv = ", ".join(AMP_DEFAULT_AUTO_ALLOWED_TOOLS)
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


SPECS: dict[Provider, Spec] = {
    "claude-code": ClaudeSpec(),
    "amp": AmpSpec(),
}


__all__ = [
    "SPECS",
    "AmpSpec",
    "ClaudeSpec",
    "EnumOption",
    "ProviderDescriptor",
    "Spec",
    "TextOption",
]
