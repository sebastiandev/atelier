"""Map generic loop permissions onto provider runtime options."""

from __future__ import annotations

from src.domain.agents.configs import (
    AmpPermissionMode,
    ClaudeAcpPermissionMode,
    ClaudePermissionMode,
    CodexAcpMode,
    CodexApprovalMode,
    CodexSandbox,
    OpenCodeMode,
)
from src.domain.agents.specs import SPECS, EnumOption
from src.domain.loop.dtos import LoopAgentPolicy, LoopPermission
from src.domain.models import Provider


def apply_stage_agent_policy(
    provider: Provider,
    parent_options: dict[str, object],
    policy: LoopAgentPolicy,
    *,
    parent_provider: Provider | None = None,
    parent_model: str | None = None,
) -> dict[str, object]:
    """Apply one stage's generic effort and permission posture.

    Preconditions: ``parent_options`` belongs to ``parent_provider``.
    Postconditions: returns new provider-valid options without mutating input.
    """
    source = parent_provider or provider
    options = dict(parent_options) if provider == source else {}
    if policy.effort is not None or provider != source:
        _apply_effort(
            options,
            provider,
            (
                policy.effort
                if policy.effort is not None
                else _effective_effort(source, parent_options, parent_model)
            ),
            strict=policy.effort is not None,
        )
    if policy.permissions is not None or provider != source:
        _apply_permission(
            options,
            provider,
            (
                policy.permissions
                if policy.permissions is not None
                else _effective_permission(source, parent_options)
            ),
        )
    return options


def _apply_effort(
    options: dict[str, object],
    provider: Provider,
    effort: str | None,
    *,
    strict: bool,
) -> None:
    target = _option(provider, ("thinking_effort", "reasoning_effort"))
    if target is None:
        if strict:
            raise ValueError(f"provider {provider!r} does not support effort")
        return
    options.pop(target[0], None)
    if effort in target[1].values:
        options[target[0]] = effort
    elif strict:
        raise ValueError(
            f"effort {effort!r} is not supported by provider {provider!r}"
        )


def _effective_effort(
    provider: Provider,
    options: dict[str, object],
    model: str | None,
) -> str | None:
    source = _option(provider, ("thinking_effort", "reasoning_effort"))
    if source is None:
        return None
    descriptor = SPECS[provider].describe()
    model_meta = descriptor.model_meta.get(model) if model is not None else None
    model_default = model_meta.effort_default if model_meta is not None else None
    default = model_default if model_default in source[1].values else source[1].default
    value = options.get(source[0], default)
    return value if isinstance(value, str) else default


def _effective_permission(
    provider: Provider,
    options: dict[str, object],
) -> LoopPermission:
    if provider == "codex":
        value = _option_value(provider, options, "sandbox")
        return (
            LoopPermission.READ
            if value == CodexSandbox.READ_ONLY.value
            else LoopPermission.WRITE
        )
    if provider == "codex-acp":
        value = _option_value(provider, options, "mode")
        return (
            LoopPermission.READ
            if value == CodexAcpMode.READ_ONLY.value
            else LoopPermission.WRITE
        )
    if provider in {"claude-code", "claude-acp"}:
        value = _option_value(provider, options, "permission_mode")
        return (
            LoopPermission.READ
            if value == ClaudePermissionMode.PLAN.value
            else LoopPermission.WRITE
        )
    if provider == "amp":
        return (
            LoopPermission.READ
            if options.get("read_only") == "true"
            else LoopPermission.WRITE
        )
    if provider == "opencode":
        value = _option_value(provider, options, "mode")
        return (
            LoopPermission.READ
            if value == OpenCodeMode.PLAN.value
            else LoopPermission.WRITE
        )
    return LoopPermission.WRITE


def _apply_permission(
    options: dict[str, object],
    provider: Provider,
    permission: LoopPermission,
) -> None:
    write = permission == LoopPermission.WRITE
    if provider == "codex":
        options.update(
            sandbox=(
                CodexSandbox.WORKSPACE_WRITE.value
                if write
                else CodexSandbox.READ_ONLY.value
            ),
            approval_mode=CodexApprovalMode.NEVER.value,
        )
    elif provider == "codex-acp":
        options["mode"] = (
            CodexAcpMode.FULL_ACCESS.value if write else CodexAcpMode.READ_ONLY.value
        )
    elif provider == "claude-code":
        options["permission_mode"] = (
            ClaudePermissionMode.BYPASS.value
            if write
            else ClaudePermissionMode.PLAN.value
        )
    elif provider == "claude-acp":
        options["permission_mode"] = (
            ClaudeAcpPermissionMode.BYPASS.value
            if write
            else ClaudeAcpPermissionMode.PLAN.value
        )
    elif provider == "amp":
        options["permission_mode"] = AmpPermissionMode.DEFAULT.value
        if write:
            options.pop("read_only", None)
        else:
            options["read_only"] = "true"
    elif provider == "opencode":
        options["mode"] = OpenCodeMode.BUILD.value if write else OpenCodeMode.PLAN.value


def _option(
    provider: Provider,
    keys: tuple[str, ...],
) -> tuple[str, EnumOption] | None:
    fields = SPECS[provider].describe().options
    return next(((key, fields[key]) for key in keys if key in fields), None)


def _option_value(
    provider: Provider,
    options: dict[str, object],
    key: str,
) -> str:
    field = SPECS[provider].describe().options[key]
    value = options.get(key, field.default)
    return value if isinstance(value, str) else field.default


def resolve_stage_model(
    provider: Provider,
    parent_provider: Provider,
    parent_model: str,
    policy: LoopAgentPolicy,
) -> str:
    """Resolve a stage model against its effective provider.

    Preconditions: ``provider`` and ``parent_provider`` are registered providers.
    Postconditions: explicit models win; inheritance never crosses providers.
    """
    if policy.model:
        return policy.model
    if provider == parent_provider:
        return parent_model
    return SPECS[provider].describe().primary_field.default


__all__ = ["apply_stage_agent_policy", "resolve_stage_model"]
