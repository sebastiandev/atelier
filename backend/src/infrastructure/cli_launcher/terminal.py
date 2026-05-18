"""Platform-specific terminal launchers for detach-to-CLI.

``kind`` mirrors the values accepted by ``infrastructure.filesystem.terminal.
open_in_terminal`` so the Settings → Console preference drives both
flows consistently. Unknown / unsupported values fall back to the
platform's system terminal.
"""

from __future__ import annotations

import shutil
import subprocess
import sys
from dataclasses import dataclass


@dataclass(frozen=True)
class LaunchResult:
    """Outcome of an attempt to launch the user's terminal."""

    command: str
    launched: bool


def launch_in_terminal(command: str, kind: str = "system") -> LaunchResult:
    try:
        if sys.platform == "darwin":
            launched = _launch_macos(command, kind)
        elif sys.platform == "win32":
            launched = _launch_windows(command)
        else:
            launched = _launch_linux(command, kind)
    except (OSError, subprocess.SubprocessError):
        launched = False
    return LaunchResult(command=command, launched=launched)


def _launch_macos(command: str, kind: str) -> bool:
    if kind == "iterm2":
        return _applescript_iterm(command)
    # "system" + any non-macOS kind (konsole etc.) → Terminal.app.
    return _applescript_terminal(command)


def _applescript_iterm(command: str) -> bool:
    script = (
        'tell application "iTerm" to activate\n'
        'tell application "iTerm" to create window with default profile\n'
        'tell application "iTerm" to tell current session of current window '
        f'to write text "{_applescript_quote(command)}"\n'
    )
    result = subprocess.run(["osascript", "-e", script], check=False)
    return result.returncode == 0


def _applescript_terminal(command: str) -> bool:
    script = (
        f'tell application "Terminal" to activate\n'
        f'tell application "Terminal" to do script "{_applescript_quote(command)}"\n'
    )
    result = subprocess.run(["osascript", "-e", script], check=False)
    return result.returncode == 0


def _launch_linux(command: str, kind: str) -> bool:
    payload = f"{command}; exec bash"
    explicit = _LINUX_EXPLICIT.get(kind)
    if explicit is not None:
        binary, args = explicit
        if shutil.which(binary):
            return subprocess.run([binary, *args, "bash", "-c", payload], check=False).returncode == 0
        return False
    # "system" / iterm2 / tmux / unknown → first available emulator.
    for binary, args in _LINUX_FALLBACKS:
        if shutil.which(binary):
            if subprocess.run([binary, *args, "bash", "-c", payload], check=False).returncode == 0:
                return True
    return False


_LINUX_EXPLICIT: dict[str, tuple[str, tuple[str, ...]]] = {
    "gnome-terminal": ("gnome-terminal", ("--",)),
    "konsole": ("konsole", ("-e",)),
    "terminator": ("terminator", ("-x",)),
}

_LINUX_FALLBACKS: tuple[tuple[str, tuple[str, ...]], ...] = (
    ("x-terminal-emulator", ("-e",)),
    ("gnome-terminal", ("--",)),
    ("konsole", ("-e",)),
    ("xterm", ("-e",)),
)


def _launch_windows(command: str) -> bool:
    if shutil.which("wt"):
        result = subprocess.run(
            ["wt", "new-tab", "cmd", "/k", command], check=False
        )
        if result.returncode in (0, 1):
            return True
    result = subprocess.run(["cmd", "/k", command], check=False)
    return result.returncode in (0, 1)


def _applescript_quote(value: str) -> str:
    return value.replace("\\", "\\\\").replace('"', '\\"')
