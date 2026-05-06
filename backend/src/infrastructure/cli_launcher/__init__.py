"""Open the user's terminal at a given folder, running a CLI resume command.

Atelier's "detach agent to CLI" flow uses this to drop the user into a
real shell with the matching ``claude --resume <id>`` or ``amp threads
continue <id>`` ready to go. The supervisor's SDK process is stopped
*before* this fires so two processes don't simultaneously drive the same
session.

Strategy is per-platform:

  - macOS: ``osascript`` against iTerm if installed (``/Applications/iTerm.app``),
    else Terminal.app. Both AppleScript dictionaries support running a
    command in a fresh window.
  - Linux: ``x-terminal-emulator`` (Debian convention), then a fallback
    chain through ``gnome-terminal`` / ``konsole`` / ``xterm``.
  - Windows: ``wt new-tab cmd /k "..."`` if Windows Terminal is on PATH,
    else ``start cmd /k "..."``.

If every option fails (headless box, locked-down sandbox, unusual config),
we return the raw command string instead of raising — the caller surfaces
it to the FE which copies to the clipboard. "Worst case is a paste" beats
"worst case is a 500 with no recovery path."
"""

from __future__ import annotations

import shutil
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path

from src.domain.models import Provider


@dataclass(frozen=True)
class LaunchResult:
    """Outcome of an attempt to launch the user's terminal."""

    command: str
    """The shell command that would resume the CLI session — useful both
    to display in the toast and as the clipboard fallback."""

    launched: bool
    """True if a terminal window was actually spawned. False if every
    platform-specific path failed and the caller should ask the FE to
    copy ``command`` to the clipboard instead."""


def build_resume_command(provider: Provider, session_id: str, workdir: Path) -> str:
    """The shell command that drops the user into the CLI mid-conversation.

    Both providers' resume invocations are well-known. We ``cd`` first so
    relative paths and git context match what the supervisor's SDK process
    was using.
    """
    cwd = _shell_quote(str(workdir))
    sid = _shell_quote(session_id)
    if provider == "claude-code":
        return f"cd {cwd} && claude --resume {sid}"
    if provider == "amp":
        return f"cd {cwd} && amp threads continue {sid}"
    raise ValueError(f"unknown provider for CLI resume: {provider!r}")


def launch_in_terminal(command: str) -> LaunchResult:
    """Open the user's preferred terminal and run ``command`` in a new
    window. Falls back to returning ``launched=False`` so the caller can
    offer clipboard copy."""
    try:
        if sys.platform == "darwin":
            launched = _launch_macos(command)
        elif sys.platform == "win32":
            launched = _launch_windows(command)
        else:
            launched = _launch_linux(command)
    except (OSError, subprocess.SubprocessError):
        launched = False
    return LaunchResult(command=command, launched=launched)


# ---------------------------------------------------------------------------
# Platform implementations


def _launch_macos(command: str) -> bool:
    # iTerm wins if installed — many macOS power-users have it set as
    # default and Terminal.app is the fallback for vanilla setups.
    if Path("/Applications/iTerm.app").exists():
        script = (
            'tell application "iTerm" to activate\n'
            'tell application "iTerm" to create window with default profile\n'
            f'tell application "iTerm" to tell current session of current window to write text "{_applescript_quote(command)}"\n'
        )
        result = subprocess.run(["osascript", "-e", script], check=False)
        if result.returncode == 0:
            return True
    script = (
        f'tell application "Terminal" to activate\n'
        f'tell application "Terminal" to do script "{_applescript_quote(command)}"\n'
    )
    result = subprocess.run(["osascript", "-e", script], check=False)
    return result.returncode == 0


def _launch_linux(command: str) -> bool:
    # ``; exec bash`` keeps the shell alive after the resume command
    # exits (otherwise the user loses their terminal the moment they
    # type ``/exit`` in the CLI).
    payload = f"{command}; exec bash"
    candidates: list[list[str]] = []
    if shutil.which("x-terminal-emulator"):
        candidates.append(["x-terminal-emulator", "-e", "bash", "-c", payload])
    if shutil.which("gnome-terminal"):
        candidates.append(["gnome-terminal", "--", "bash", "-c", payload])
    if shutil.which("konsole"):
        candidates.append(["konsole", "-e", "bash", "-c", payload])
    if shutil.which("xterm"):
        candidates.append(["xterm", "-e", "bash", "-c", payload])
    for argv in candidates:
        result = subprocess.run(argv, check=False)
        if result.returncode == 0:
            return True
    return False


def _launch_windows(command: str) -> bool:
    # Windows Terminal is the modern default; if absent, ``cmd`` ships
    # with every Windows install. ``/k`` keeps the cmd window open after
    # the resume command exits.
    if shutil.which("wt"):
        result = subprocess.run(
            ["wt", "new-tab", "cmd", "/k", command], check=False
        )
        if result.returncode in (0, 1):  # wt returns 0; cmd returns 1 even on success
            return True
    result = subprocess.run(["cmd", "/k", command], check=False)
    return result.returncode in (0, 1)


# ---------------------------------------------------------------------------
# Quoting


def _shell_quote(value: str) -> str:
    # Single-quote for POSIX shells; escape embedded single-quotes by
    # closing+escaping+reopening. Same rule shlex.quote uses.
    return "'" + value.replace("'", "'\\''") + "'"


def _applescript_quote(value: str) -> str:
    # AppleScript strings are double-quoted; backslashes and double quotes
    # are the only escapes that need handling.
    return value.replace("\\", "\\\\").replace('"', '\\"')


__all__ = ["LaunchResult", "build_resume_command", "launch_in_terminal"]
