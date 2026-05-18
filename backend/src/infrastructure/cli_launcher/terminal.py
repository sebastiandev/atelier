"""Platform-specific terminal launchers for detach-to-CLI."""

from __future__ import annotations

import shutil
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class LaunchResult:
    """Outcome of an attempt to launch the user's terminal."""

    command: str
    launched: bool


def launch_in_terminal(command: str) -> LaunchResult:
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


def _launch_macos(command: str) -> bool:
    if Path("/Applications/iTerm.app").exists():
        script = (
            'tell application "iTerm" to activate\n'
            'tell application "iTerm" to create window with default profile\n'
            'tell application "iTerm" to tell current session of current window '
            f'to write text "{_applescript_quote(command)}"\n'
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
