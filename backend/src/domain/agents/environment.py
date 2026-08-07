"""Build the environment an agent subprocess is launched with.

Agents inherit Atelier's own process environment so they keep the user's
shell setup — PATH, credentials, tool configuration. That inheritance also
carries Atelier's active virtualenv, because ``scripts/dev.sh`` activates
``backend/.venv`` before starting the server. Nothing chose to pass it down,
and in the agent's workspace it is actively wrong: a bare ``python``,
``pytest`` or ``ruff`` there would resolve to Atelier's interpreter instead
of the workspace's own. ``uv`` warns about the mismatch and ignores it; tools
without that defence would not.

Worktrees get the repository's real venvs symlinked in by
``infrastructure/git/worktree_manager``, so removing Atelier's leaves the
agent with the right one rather than none.
"""

from __future__ import annotations

import os
from collections.abc import Mapping

_VENV_BIN_DIRS = ("bin", "Scripts")
"""POSIX and Windows layouts; a venv has one, and we don't know which."""

_INHERITED_PYTHON_VARS = ("VIRTUAL_ENV", "PYTHONHOME", "PYTHONPATH")


def agent_environment(
    base: Mapping[str, str],
    overrides: Mapping[str, str] | None = None,
) -> dict[str, str]:
    """Return ``base`` without Atelier's virtualenv, then ``overrides`` applied.

    Preconditions: ``base`` is the environment to inherit, normally
    ``os.environ``. ``overrides`` are the caller's deliberate additions.
    Postconditions: the result carries no ``VIRTUAL_ENV``/``PYTHONHOME``/
    ``PYTHONPATH`` inherited from ``base`` and no ``PATH`` entry belonging to
    the virtualenv ``base`` had active. An override always wins, so a caller
    that genuinely needs one of those variables can still set it.
    """
    env = {key: value for key, value in base.items() if key not in _INHERITED_PYTHON_VARS}
    venv = base.get("VIRTUAL_ENV", "").strip()
    path = base.get("PATH")
    if venv and path is not None:
        env["PATH"] = _without_venv_entries(path, venv)
    env.update(overrides or {})
    return env


def _without_venv_entries(path: str, venv: str) -> str:
    """Drop the entries of ``path`` that point inside ``venv``'s binary dirs."""
    unwanted = {os.path.normpath(os.path.join(venv, name)) for name in _VENV_BIN_DIRS}
    kept = [entry for entry in path.split(os.pathsep) if os.path.normpath(entry) not in unwanted]
    return os.pathsep.join(kept)


__all__ = ["agent_environment"]
