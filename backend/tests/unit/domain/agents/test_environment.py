"""Atelier's own virtualenv must not reach the agent subprocess."""

from __future__ import annotations

import os

import pytest

from src.domain.agents import agent_environment

_ATELIER_VENV = "/Users/dev/src/atelier/backend/.venv"


def _path(*entries: str) -> str:
    return os.pathsep.join(entries)


@pytest.mark.parametrize("variable", ["VIRTUAL_ENV", "PYTHONHOME", "PYTHONPATH"])
def test_inherited_python_variables_are_dropped(variable: str) -> None:
    result = agent_environment({variable: "/anything", "HOME": "/Users/dev"})

    assert variable not in result
    assert result["HOME"] == "/Users/dev"


@pytest.mark.parametrize("bin_dir", ["bin", "Scripts"])
def test_the_active_venv_leaves_the_path(bin_dir: str) -> None:
    venv_bin = os.path.join(_ATELIER_VENV, bin_dir)
    result = agent_environment({"VIRTUAL_ENV": _ATELIER_VENV, "PATH": _path(venv_bin, "/usr/bin")})

    assert result["PATH"] == "/usr/bin"


def test_unrelated_venvs_stay_on_the_path() -> None:
    """Only Atelier's venv goes; the workspace's own must survive."""
    workspace_bin = "/Users/dev/src/shiphero/.venv/bin"
    result = agent_environment(
        {
            "VIRTUAL_ENV": _ATELIER_VENV,
            "PATH": _path(os.path.join(_ATELIER_VENV, "bin"), workspace_bin, "/usr/bin"),
        }
    )

    assert result["PATH"] == _path(workspace_bin, "/usr/bin")


def test_the_path_is_untouched_without_an_active_venv() -> None:
    original = _path("/opt/homebrew/bin", "/usr/bin")

    result = agent_environment({"PATH": original})

    assert result["PATH"] == original


def test_an_override_wins_over_the_removal() -> None:
    """A caller that deliberately sets one of these keeps it."""
    result = agent_environment(
        {"VIRTUAL_ENV": _ATELIER_VENV},
        {"PYTHONPATH": "/src/warehouse_hero_api/src"},
    )

    assert result["PYTHONPATH"] == "/src/warehouse_hero_api/src"


def test_overrides_are_applied_over_the_inherited_environment() -> None:
    result = agent_environment({"HOME": "/Users/dev", "TERM": "dumb"}, {"TERM": "xterm"})

    assert result == {"HOME": "/Users/dev", "TERM": "xterm"}
