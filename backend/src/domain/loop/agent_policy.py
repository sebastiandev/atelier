"""Map generic loop permissions onto provider runtime options."""

from __future__ import annotations

from dataclasses import replace
from pathlib import Path
from typing import cast

from src.domain.agents.configs import (
    AmpPermissionMode,
    ClaudeAcpPermissionMode,
    ClaudePermissionMode,
    CodexAcpMode,
    CodexApprovalMode,
    CodexSandbox,
    CommonAgentConfig,
    OpenCodeMode,
)
from src.domain.agents.effort import allowed_efforts, effort_option, effort_option_key
from src.domain.agents.launch import InvalidProviderConfig
from src.domain.agents.specs import SPECS, EnumOption
from src.domain.loop.definitions import validate_agent_policy
from src.domain.loop.dtos import (
    LoopAgentPolicy,
    LoopBriefAgent,
    LoopDefinition,
    LoopPermission,
    LoopSessionPolicy,
)
from src.domain.loop.stage_access import stage_agent_policy
from src.domain.models import Provider

StageAgentConfig = tuple[Provider, str, dict[str, object]]


def supports_fast(provider: Provider) -> bool:
    """Whether ``provider`` publishes a fast-mode option at all."""
    return "fast-mode" in SPECS[provider].describe().options


def sanitize_agent_policy(
    policy: LoopAgentPolicy,
    *,
    provider: Provider,
    model: str | None = None,
) -> LoopAgentPolicy:
    """Drop policy values ``provider``/``model`` cannot honour.

    One answer to "does this apply here?", for every policy Atelier derives
    rather than a person authoring it: a synthesised PR stage, a brief
    override, a retry that changes model. Those inherit whatever the
    previous config happened to carry, and a value that made sense for the
    old model is not an instruction about the new one — a Create PR dialog
    sending ``fast: false`` to OpenCode, or an effort carried onto a model
    whose ladder does not list it. Ignoring them is the point: an inherited
    default is not a choice, so refusing to launch over one would be
    strictly worse than proceeding without it.

    Authored definitions keep going through ``validate_agent_policy``,
    which reports the same conditions as errors — someone who typed a value
    should be told it will not apply.

    Preconditions: ``provider`` is registered.
    Postconditions: returns a policy whose remaining values are all
    supported; ``provider``/``model`` themselves are left untouched.
    """
    effort = policy.effort
    if effort and effort not in allowed_efforts(provider, model or policy.model):
        effort = None
    fast = policy.fast
    if fast is not None and not supports_fast(provider):
        fast = None
    if effort == policy.effort and fast == policy.fast:
        return policy
    return replace(policy, effort=effort, fast=fast)


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
    if policy.fast is not None:
        fast = _option(provider, ("fast-mode",))
        if fast is None:
            raise ValueError(f"provider {provider!r} does not support fast mode")
        options[fast[0]] = "on" if policy.fast else "off"
    return options


def _apply_effort(
    options: dict[str, object],
    provider: Provider,
    effort: str | None,
    *,
    strict: bool,
) -> None:
    target = effort_option(provider)
    if target is None:
        if strict:
            raise ValueError(f"provider {provider!r} does not support effort")
        return
    options.pop(target[0], None)
    if effort in target[1].values:
        options[target[0]] = effort
    elif strict:
        raise ValueError(f"effort {effort!r} is not supported by provider {provider!r}")


def _effective_effort(
    provider: Provider,
    options: dict[str, object],
    model: str | None,
) -> str | None:
    source = effort_option(provider)
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
            LoopPermission.READ if value == CodexSandbox.READ_ONLY.value else LoopPermission.WRITE
        )
    if provider == "codex-acp":
        value = _option_value(provider, options, "mode")
        return (
            LoopPermission.READ if value == CodexAcpMode.READ_ONLY.value else LoopPermission.WRITE
        )
    if provider in {"claude-code", "claude-acp"}:
        value = _option_value(provider, options, "permission_mode")
        return (
            LoopPermission.READ
            if value == ClaudePermissionMode.PLAN.value
            else LoopPermission.WRITE
        )
    if provider == "amp":
        return LoopPermission.READ if options.get("read_only") == "true" else LoopPermission.WRITE
    if provider == "opencode":
        value = _option_value(provider, options, "mode")
        return LoopPermission.READ if value == OpenCodeMode.PLAN.value else LoopPermission.WRITE
    return LoopPermission.WRITE


def _apply_permission(
    options: dict[str, object],
    provider: Provider,
    permission: LoopPermission,
) -> None:
    write = permission == LoopPermission.WRITE
    if provider == "codex":
        options.update(
            sandbox=(CodexSandbox.WORKSPACE_WRITE.value if write else CodexSandbox.READ_ONLY.value),
            approval_mode=CodexApprovalMode.NEVER.value,
        )
    elif provider == "codex-acp":
        options["mode"] = CodexAcpMode.FULL_ACCESS.value if write else CodexAcpMode.READ_ONLY.value
    elif provider == "claude-code":
        options["permission_mode"] = (
            ClaudePermissionMode.BYPASS.value if write else ClaudePermissionMode.PLAN.value
        )
    elif provider == "claude-acp":
        options["permission_mode"] = (
            ClaudeAcpPermissionMode.BYPASS.value if write else ClaudeAcpPermissionMode.PLAN.value
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


def resolve_stage_agent_config(
    policy: LoopAgentPolicy,
    *,
    parent_provider: Provider,
    parent_model: str,
    parent_options: dict[str, object],
    override: LoopBriefAgent | None = None,
) -> StageAgentConfig:
    """Resolve one stage's effective provider configuration.

    Preconditions: the parent config belongs to a registered provider.
    Postconditions: inherited and overridden values are returned without
    mutating the parent options.

    A provider switch invalidates the stage's own model and fast flag --
    they describe a provider that is no longer in play. Re-picking the
    *same* provider is not a switch, so those still stand: a run setup that
    confirms the stage's provider while leaving its model alone should not
    silently drop the model the definition pinned.
    """
    switched = (
        override is not None
        and override.provider is not None
        and override.provider != (policy.provider or parent_provider)
    )
    effective = replace(
        policy,
        provider=(override.provider or policy.provider) if override else policy.provider,
        model=(
            (override.model if override else None)
            or (None if switched else policy.model)
        ),
        fast=None if switched else policy.fast,
    )
    provider = cast(Provider, effective.provider or parent_provider)
    model = resolve_stage_model(provider, parent_provider, parent_model, effective)
    # Everything above is inherited or overridden rather than authored here,
    # so drop what the resolved provider/model cannot honour before applying.
    effective = sanitize_agent_policy(effective, provider=provider, model=model)
    options = apply_stage_agent_policy(
        provider,
        parent_options,
        effective,
        parent_provider=parent_provider,
        parent_model=parent_model,
    )
    if override is not None:
        options.update(override.options)
    return provider, model, options


def validate_stage_agent_policies(
    definition: LoopDefinition,
    *,
    parent_provider: Provider,
    parent_model: str,
    parent_options: dict[str, object],
    parent_folder: Path,
    overrides: dict[str, LoopBriefAgent] | None = None,
) -> None:
    """Preflight every reachable agent stage against its effective parent.

    Preconditions: the parent config and loop definition are complete.
    Postconditions: all reachable agent configs build successfully, or validation
    fails before any loop agent or run is persisted.
    """
    common = CommonAgentConfig(workdir=parent_folder, system_prompt="")
    stages = {stage.step_id: stage for stage in definition.stages}
    first_id = definition.stages[0].step_id
    launched: dict[str, StageAgentConfig] = {}
    pending = [
        (
            first_id,
            parent_provider,
            parent_model,
            dict(parent_options),
            True,
            launched,
        )
    ]
    seen: set[tuple[str, Provider, str, str, bool, str]] = set()
    while pending:
        stage_id, source_provider, source_model, source_options, initial, launched = pending.pop()
        state = (
            stage_id,
            source_provider,
            source_model,
            repr(sorted(source_options.items())),
            initial,
            repr(
                sorted(
                    (
                        launched_id,
                        config[0],
                        config[1],
                        repr(sorted(config[2].items())),
                    )
                    for launched_id, config in launched.items()
                )
            ),
        )
        if state in seen:
            continue
        seen.add(state)
        stage = stages[stage_id]
        provider = source_provider
        model = source_model
        options = source_options
        next_launched = launched
        policy = stage_agent_policy(stage)
        reused = (
            policy is not None
            and policy.session == LoopSessionPolicy.REUSE
            and stage_id in launched
        )
        if reused:
            provider, model, options = launched[stage_id]
        elif policy is not None:
            try:
                provider, model, options = resolve_stage_agent_config(
                    policy,
                    parent_provider=source_provider,
                    parent_model=source_model,
                    parent_options=source_options,
                    override=(overrides or {}).get(stage.step_id),
                )
                SPECS[provider].build(common, model, options)
            except (KeyError, ValueError) as exc:
                raise InvalidProviderConfig(f"{stage.name}: {exc}") from exc
            if policy.session == LoopSessionPolicy.REUSE:
                next_launched = {
                    **launched,
                    stage_id: (provider, model, options),
                }
        if not initial and not stage.supplies_source_agent:
            provider = source_provider
            model = source_model
            options = source_options
        for destination in stage.transitions.values():
            if destination in stages:
                pending.append(
                    (
                        destination,
                        provider,
                        model,
                        options,
                        False,
                        next_launched,
                    )
                )


def apply_retry_overrides(
    provider: Provider,
    model: str,
    options: dict[str, object],
    *,
    model_override: str | None,
    effort_override: str | None,
) -> StageAgentConfig:
    """Overlay a same-provider model/effort change onto a retry's base config.

    Preconditions: ``(provider, model, options)`` is the failed attempt's
    resolved config; a retry never switches provider. Postconditions: returns
    a validated config with the provider unchanged and only the model and/or
    effort replaced; raises :class:`InvalidProviderConfig` when the requested
    model or effort is not valid for ``provider``.
    """
    if model_override is None and effort_override is None:
        return provider, model, dict(options)
    requested = LoopAgentPolicy(
        provider=provider,
        model=model_override or model,
        effort=effort_override,
    )
    errors = validate_agent_policy("Retry override", requested)
    if errors:
        raise InvalidProviderConfig(" ".join(errors))
    resolved = dict(options)
    if effort_override is not None:
        key = effort_option_key(provider)
        if key is None:
            raise InvalidProviderConfig(f"{provider} has no reasoning effort setting")
        resolved[key] = effort_override
    return provider, model_override or model, resolved


__all__ = [
    "StageAgentConfig",
    "apply_retry_overrides",
    "apply_stage_agent_policy",
    "resolve_stage_agent_config",
    "resolve_stage_model",
    "sanitize_agent_policy",
    "supports_fast",
    "validate_stage_agent_policies",
]
