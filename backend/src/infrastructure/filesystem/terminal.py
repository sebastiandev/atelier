"""Cross-platform "open a terminal at this path" shell-out.

Sibling of ``reveal.open_in_file_browser`` — same shape (one function,
one platform branch, no state) so both the work-level and agent-level
"open console" buttons share a single implementation. Tests monkeypatch
``subprocess.run`` here.

The ``kind`` argument lets the caller pick a specific terminal app
instead of the platform default. Supported values:

    - ``"system"`` (default) — the platform's native terminal:
      Terminal.app on macOS, Windows Terminal / cmd on Windows, the
      Debian ``x-terminal-emulator`` alternative on Linux.
    - ``"iterm2"`` — iTerm2 on macOS via ``open -a iTerm <path>``.
    - ``"terminator"`` — Terminator on Linux.
    - ``"gnome-terminal"`` — GNOME Terminal on Linux.
    - ``"konsole"`` — KDE Konsole on Linux.
    - ``"tmux"`` — split semantics: when Atelier itself runs inside a
      tmux session (``$TMUX`` set), opens a new window in that session
      cd'd into ``path``; otherwise creates / attaches a detached
      ``atelier`` session and asks the system terminal to attach to it.

Unknown kinds fall back to ``"system"`` so a bad config never breaks
the button.
"""

from __future__ import annotations

import os
import shutil
import subprocess
import sys

TerminalKind = str  # narrow alias — keep backend untyped, frontend owns the union


def open_in_terminal(path: str, kind: TerminalKind = "system") -> None:
    """Open a terminal session whose CWD is ``path``.

    Raises ``FileNotFoundError`` (no terminal binary found on the host)
    or ``subprocess.SubprocessError`` (launch failed) on error — the
    caller is expected to convert these into an HTTP error.
    """
    if kind == "iterm2":
        _open_iterm2(path)
        return
    if kind == "terminator":
        _open_named_linux_terminal("terminator", "--working-directory", path)
        return
    if kind == "gnome-terminal":
        _open_named_linux_terminal("gnome-terminal", "--working-directory", path)
        return
    if kind == "konsole":
        _open_named_linux_terminal("konsole", "--workdir", path)
        return
    if kind == "tmux":
        _open_tmux(path)
        return
    # "system" + anything unknown.
    _open_system_terminal(path)


def _open_system_terminal(path: str) -> None:
    if sys.platform == "darwin":
        subprocess.run(["open", "-a", "Terminal", path], check=True)
        return

    if sys.platform == "win32":
        wt = shutil.which("wt.exe") or shutil.which("wt")
        if wt:
            subprocess.run([wt, "-d", path], check=False)
            return
        subprocess.run(
            ["cmd", "/c", "start", "", "cmd", "/K", f"cd /D {path}"],
            check=False,
        )
        return

    for binary, args in _LINUX_TERMINALS:
        resolved = shutil.which(binary)
        if not resolved:
            continue
        subprocess.run([resolved, *args, path], check=True)
        return

    raise FileNotFoundError(
        "no terminal emulator found on PATH "
        "(tried x-terminal-emulator, gnome-terminal, konsole, terminator, ...)"
    )


def _open_iterm2(path: str) -> None:
    if sys.platform != "darwin":
        raise FileNotFoundError("iTerm2 is only available on macOS")
    # ``open -a iTerm`` accepts a path argument and iTerm2 cd's its new
    # window into it. Same shape as the Terminal.app branch above.
    subprocess.run(["open", "-a", "iTerm", path], check=True)


def _open_named_linux_terminal(binary: str, cwd_flag: str, path: str) -> None:
    resolved = shutil.which(binary)
    if not resolved:
        raise FileNotFoundError(f"{binary!r} not found on PATH")
    subprocess.run([resolved, cwd_flag, path], check=True)


def _open_tmux(path: str) -> None:
    """Open a tmux window cd'd into ``path``.

    Two flavors:
      * Atelier was launched from inside tmux (``$TMUX`` is set) — open
        a new window in that session via ``tmux new-window -c <path>``.
        Best UX since the window appears right next to whatever the
        user is doing.
      * Otherwise — create-or-attach a detached ``atelier`` session
        with the right CWD, then ask the system terminal to attach to
        it. The user gets a Terminal/iTerm/Linux-terminal window with
        ``tmux attach -t atelier`` already running.
    """
    tmux = shutil.which("tmux")
    if not tmux:
        raise FileNotFoundError("tmux not found on PATH")

    if os.environ.get("TMUX"):
        subprocess.run([tmux, "new-window", "-c", path], check=True)
        return

    # Idempotent detached create — ``new-session -A`` attaches if the
    # session already exists. ``-d`` keeps it detached so we can open a
    # terminal next.
    session = "atelier"
    subprocess.run(
        [tmux, "new-session", "-d", "-A", "-s", session, "-c", path],
        check=True,
    )
    # Move the *new* window inside the session to ``path`` too — when
    # the session was pre-existing, ``new-session -A`` won't have
    # changed CWD, but a fresh window will.
    subprocess.run(
        [tmux, "new-window", "-t", session, "-c", path],
        check=False,
    )
    _attach_tmux_in_system_terminal(tmux, session)


def _attach_tmux_in_system_terminal(tmux: str, session: str) -> None:
    """Launch the platform terminal and have it run ``tmux attach``."""
    if sys.platform == "darwin":
        # AppleScript: tell Terminal to ``do script`` (opens a new
        # window running the command).
        subprocess.run(
            [
                "osascript",
                "-e",
                f'tell application "Terminal" to do script '
                f'"exec {tmux} attach -t {session}"',
            ],
            check=True,
        )
        return

    if sys.platform == "win32":
        # tmux on Windows is rare; if the user picked it, assume they
        # know what they're doing and use Windows Terminal.
        wt = shutil.which("wt.exe") or shutil.which("wt")
        if wt:
            subprocess.run([wt, tmux, "attach", "-t", session], check=False)
            return
        raise FileNotFoundError("Windows Terminal not found for tmux attach")

    # Linux — pick whichever terminal is around and tell it to ``-e``
    # the attach command. Most terminals support a ``-e`` style flag.
    for binary, exec_flag in _LINUX_EXEC_TERMINALS:
        resolved = shutil.which(binary)
        if not resolved:
            continue
        subprocess.run(
            [resolved, exec_flag, tmux, "attach", "-t", session],
            check=True,
        )
        return

    raise FileNotFoundError(
        "no terminal emulator found on PATH for tmux attach "
        "(tried x-terminal-emulator, gnome-terminal, konsole, xterm, ...)"
    )


# Terminal binary + the flag that points it at a working directory.
# Order matters — first match wins.
_LINUX_TERMINALS: tuple[tuple[str, tuple[str, ...]], ...] = (
    ("x-terminal-emulator", ("--working-directory",)),
    ("gnome-terminal", ("--working-directory",)),
    ("konsole", ("--workdir",)),
    ("terminator", ("--working-directory",)),
    ("xfce4-terminal", ("--working-directory",)),
    ("alacritty", ("--working-directory",)),
    ("kitty", ("--directory",)),
)

# Terminal binary + the flag that runs a command inside it (for tmux
# attach). ``-e`` is the convention on most terminals.
_LINUX_EXEC_TERMINALS: tuple[tuple[str, str], ...] = (
    ("x-terminal-emulator", "-e"),
    ("gnome-terminal", "--"),
    ("konsole", "-e"),
    ("terminator", "-x"),
    ("xfce4-terminal", "-x"),
    ("alacritty", "-e"),
    ("kitty", "-e"),
    ("xterm", "-e"),
)


__all__ = ["open_in_terminal", "TerminalKind"]
