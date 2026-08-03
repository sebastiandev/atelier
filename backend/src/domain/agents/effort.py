"""One place that knows how "reasoning effort" is spelled.

Every provider calls the dial something different: ``thinking_effort``
for the Claude family, ``reasoning_effort`` for the Codex family and
OpenCode. On the wire it's a third thing again — claude-acp and opencode
both publish it as ``effort`` while codex-acp reuses its spec key. Those
mappings used to be re-derived by hand at five call sites (loop policy
twice, definition validation, two WS action handlers), so widening one
provider's support meant remembering all five. Adding OpenCode's effort
touched exactly one of them.
"""

from __future__ import annotations

from src.domain.agents.configs import ACP_EFFORT_CONFIG_ID
from src.domain.agents.specs import SPECS, EnumOption
from src.domain.models import Provider

#: Spec option keys that carry effort, most specific first. Providers
#: declare at most one of them.
EFFORT_OPTION_KEYS: tuple[str, ...] = ("thinking_effort", "reasoning_effort")


def effort_option(provider: Provider) -> tuple[str, EnumOption] | None:
    """The provider's effort field as ``(spec key, option)``, or None when
    it has no effort dial (Amp)."""
    options = SPECS[provider].describe().options
    for key in EFFORT_OPTION_KEYS:
        field = options.get(key)
        if field is not None:
            return key, field
    return None


def effort_option_key(provider: Provider) -> str | None:
    """The provider's effort spec key, or None when it has no dial."""
    found = effort_option(provider)
    return None if found is None else found[0]


def allowed_efforts(provider: Provider, model: str | None = None) -> tuple[str, ...]:
    """Effort values ``provider`` accepts, narrowed to ``model`` when it says.

    A model's ladder can be shorter than its provider's: OpenCode exposes
    effort as a per-model *variant*, so one model offers ``high``/``max``
    and another offers nothing at all. ``model_meta`` carries that when the
    provider knows it; an absent entry means "no opinion", so the
    provider-wide ladder stands.

    Preconditions: ``provider`` is registered.
    Postconditions: empty when the provider has no dial, or when the model
    declares it has none.
    """
    found = effort_option(provider)
    if found is None:
        return ()
    allowed = tuple(found[1].values)
    if not model:
        return allowed
    meta = SPECS[provider].describe().model_meta.get(model)
    if meta is None or meta.effort_values is None:
        return allowed
    return tuple(meta.effort_values)


def spec_option_key(provider: Provider, config_id: str) -> str | None:
    """Map a live ACP config id onto the spec option key that stores it.

    Config ids usually match spec keys one-for-one; ``effort`` is the
    exception, because the wire collapses the per-family spellings. Returns
    None when the provider has no such option, so a live change to an
    option Atelier doesn't model is simply not persisted.
    """
    options = SPECS[provider].describe().options
    if config_id in options:
        return config_id
    if config_id == ACP_EFFORT_CONFIG_ID:
        return effort_option_key(provider)
    return None


__all__ = [
    "ACP_EFFORT_CONFIG_ID",
    "EFFORT_OPTION_KEYS",
    "allowed_efforts",
    "effort_option",
    "effort_option_key",
    "spec_option_key",
]
