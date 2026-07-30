"""The one place that knows how effort is spelled, per provider."""

import pytest

from src.domain.agents.effort import (
    effort_option,
    effort_option_key,
    spec_option_key,
)
from src.domain.models import Provider


@pytest.mark.parametrize(
    ("provider", "expected"),
    [
        ("claude-code", "thinking_effort"),
        ("claude-acp", "thinking_effort"),
        ("codex", "reasoning_effort"),
        ("codex-acp", "reasoning_effort"),
        ("opencode", "reasoning_effort"),
        ("amp", None),
    ],
)
def test_effort_option_key_per_provider(
    provider: Provider, expected: str | None
) -> None:
    assert effort_option_key(provider) == expected


def test_effort_option_returns_the_field_alongside_its_key() -> None:
    found = effort_option("claude-acp")

    assert found is not None
    key, field = found
    assert key == "thinking_effort"
    assert "high" in field.values


@pytest.mark.parametrize(
    ("provider", "config_id", "expected"),
    [
        # The wire id for claude-acp and opencode collapses to "effort".
        ("claude-acp", "effort", "thinking_effort"),
        ("opencode", "effort", "reasoning_effort"),
        # codex-acp names it the same as its spec key, so it matches directly.
        ("codex-acp", "reasoning_effort", "reasoning_effort"),
        # Non-effort ids pass through when the provider declares them.
        ("opencode", "mode", "mode"),
        # A provider with no dial persists nothing.
        ("amp", "effort", None),
        # Options Atelier doesn't model are not persisted.
        ("opencode", "temperature", None),
    ],
)
def test_spec_option_key_maps_live_config_ids(
    provider: Provider, config_id: str, expected: str | None
) -> None:
    assert spec_option_key(provider, config_id) == expected
