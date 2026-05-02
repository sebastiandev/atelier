"""Tests for the persona/role → system_prompt rendering helper."""

from src.domain.agents import render_system_prompt


def test_render_includes_persona_and_role() -> None:
    out = render_system_prompt("architect", "design the schema")
    assert "architect" in out
    assert "design the schema" in out


def test_render_is_deterministic() -> None:
    a = render_system_prompt("developer", "build the API")
    b = render_system_prompt("developer", "build the API")
    assert a == b
