"""Tests for the persona/role → system_prompt rendering helper."""

from pathlib import Path

from src.domain.agents import render_system_prompt


def test_render_includes_persona_and_role() -> None:
    out = render_system_prompt("architect", "design the schema")
    assert "architect" in out
    assert "design the schema" in out


def test_render_is_deterministic() -> None:
    a = render_system_prompt("developer", "build the API")
    b = render_system_prompt("developer", "build the API")
    assert a == b


def test_render_includes_workdir_when_supplied() -> None:
    """The agent needs to know its working directory explicitly —
    without it, models routinely write files to $HOME and then call
    record_doc with a relative path that the tracker resolves
    elsewhere."""
    out = render_system_prompt(
        "developer", "build it", workdir=Path("/Users/seba/repos/atelier")
    )
    assert "Working directory: /Users/seba/repos/atelier" in out
    assert "relative to this directory" in out


def test_render_omits_workdir_block_when_unset() -> None:
    out = render_system_prompt("developer", "build it")
    assert "Working directory" not in out
