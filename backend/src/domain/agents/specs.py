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

from dataclasses import dataclass
from typing import Any, ClassVar, Protocol

from src.domain.agents.configs import (
    AgentConfig,
    AmpAgentConfig,
    AmpMode,
    ClaudeAgentConfig,
    ClaudeEffort,
    ClaudeModel,
    ClaudePermissionMode,
    CommonAgentConfig,
)
from src.domain.models import Provider


@dataclass(frozen=True, kw_only=True)
class EnumOption:
    """An enum-valued form field. ``values`` is the allowed set, ``default`` is one of them."""

    label: str
    values: list[str]
    default: str


@dataclass(frozen=True, kw_only=True)
class ProviderDescriptor:
    name: Provider
    label: str
    primary_field: EnumOption
    options: dict[str, EnumOption]


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

    # Real Amp integration is deferred — for now this spec wires up the
    # descriptor + config flow end-to-end and the adapter is stub-backed.
    _allowed_options: ClassVar[set[str]] = set()

    def describe(self) -> ProviderDescriptor:
        return ProviderDescriptor(
            name=self.name,
            label=self.label,
            primary_field=EnumOption(
                label="Mode",
                values=_enum_values(AmpMode),
                default=AmpMode.SMART.value,
            ),
            options={},
        )

    def build(
        self, common: CommonAgentConfig, model: str, options: dict[str, Any]
    ) -> AmpAgentConfig:
        _reject_unknown(self.name, options, self._allowed_options)
        return AmpAgentConfig(common=common, mode=AmpMode(model))


SPECS: dict[Provider, Spec] = {
    "claude-code": ClaudeSpec(),
    "amp": AmpSpec(),
}


__all__ = [
    "AmpSpec",
    "ClaudeSpec",
    "EnumOption",
    "ProviderDescriptor",
    "SPECS",
    "Spec",
]
