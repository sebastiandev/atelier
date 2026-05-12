"""Unit tests for ``open_in_terminal``.

The helper does platform branching on ``sys.platform`` and shells out
via ``subprocess.run`` / ``shutil.which``. Tests monkeypatch all three
so the suite runs on any host (CI, dev macOS, Linux box).
"""

from __future__ import annotations

from typing import Any

import pytest

from src.infrastructure.filesystem import terminal as term_mod


# ---------------------------------------------------------------------------
# Test fixtures: a recording fake for ``subprocess.run`` + a controllable
# ``shutil.which`` so each test can pretend specific binaries exist.
# ---------------------------------------------------------------------------


@pytest.fixture
def runs(monkeypatch: pytest.MonkeyPatch) -> list[list[str]]:
    """Patch ``subprocess.run`` and return the list of argv calls."""
    calls: list[list[str]] = []

    def fake_run(args: list[str], **_: Any) -> object:
        calls.append(list(args))
        return _DummyCompleted()

    monkeypatch.setattr(term_mod.subprocess, "run", fake_run)
    return calls


class _DummyCompleted:
    returncode = 0


def _stub_which(present: set[str]) -> Any:
    def which(name: str) -> str | None:
        return f"/usr/bin/{name}" if name in present else None

    return which


# ---------------------------------------------------------------------------
# system / default branch
# ---------------------------------------------------------------------------


def test_system_macos_uses_terminal_app(
    monkeypatch: pytest.MonkeyPatch, runs: list[list[str]]
) -> None:
    monkeypatch.setattr(term_mod.sys, "platform", "darwin")
    term_mod.open_in_terminal("/tmp/foo")
    assert runs == [["open", "-a", "Terminal", "/tmp/foo"]]


def test_system_windows_prefers_windows_terminal(
    monkeypatch: pytest.MonkeyPatch, runs: list[list[str]]
) -> None:
    monkeypatch.setattr(term_mod.sys, "platform", "win32")
    monkeypatch.setattr(term_mod.shutil, "which", _stub_which({"wt.exe"}))
    term_mod.open_in_terminal("C:/work")
    assert runs == [["/usr/bin/wt.exe", "-d", "C:/work"]]


def test_system_windows_falls_back_to_cmd(
    monkeypatch: pytest.MonkeyPatch, runs: list[list[str]]
) -> None:
    monkeypatch.setattr(term_mod.sys, "platform", "win32")
    # No wt available — fall through to cmd.
    monkeypatch.setattr(term_mod.shutil, "which", _stub_which(set()))
    term_mod.open_in_terminal("C:/work")
    assert runs == [["cmd", "/c", "start", "", "cmd", "/K", "cd /D C:/work"]]


def test_system_linux_picks_first_available_terminal(
    monkeypatch: pytest.MonkeyPatch, runs: list[list[str]]
) -> None:
    monkeypatch.setattr(term_mod.sys, "platform", "linux")
    # Only gnome-terminal present — x-terminal-emulator is tried first
    # but missing, so the loop keeps going.
    monkeypatch.setattr(term_mod.shutil, "which", _stub_which({"gnome-terminal"}))
    term_mod.open_in_terminal("/home/u/work")
    assert runs == [["/usr/bin/gnome-terminal", "--working-directory", "/home/u/work"]]


def test_system_linux_raises_when_no_terminal_present(
    monkeypatch: pytest.MonkeyPatch, runs: list[list[str]]
) -> None:
    monkeypatch.setattr(term_mod.sys, "platform", "linux")
    monkeypatch.setattr(term_mod.shutil, "which", _stub_which(set()))
    with pytest.raises(FileNotFoundError):
        term_mod.open_in_terminal("/home/u/work")
    assert runs == []


def test_unknown_kind_falls_back_to_system(
    monkeypatch: pytest.MonkeyPatch, runs: list[list[str]]
) -> None:
    """Bad config (typo, removed option) must never break the button —
    the helper silently routes to the system default."""
    monkeypatch.setattr(term_mod.sys, "platform", "darwin")
    term_mod.open_in_terminal("/tmp/foo", kind="not-a-real-terminal")
    assert runs == [["open", "-a", "Terminal", "/tmp/foo"]]


# ---------------------------------------------------------------------------
# iterm2
# ---------------------------------------------------------------------------


def test_iterm2_on_macos(
    monkeypatch: pytest.MonkeyPatch, runs: list[list[str]]
) -> None:
    monkeypatch.setattr(term_mod.sys, "platform", "darwin")
    term_mod.open_in_terminal("/tmp/foo", kind="iterm2")
    assert runs == [["open", "-a", "iTerm", "/tmp/foo"]]


def test_iterm2_off_macos_raises(
    monkeypatch: pytest.MonkeyPatch, runs: list[list[str]]
) -> None:
    monkeypatch.setattr(term_mod.sys, "platform", "linux")
    with pytest.raises(FileNotFoundError, match="macOS"):
        term_mod.open_in_terminal("/tmp/foo", kind="iterm2")
    assert runs == []


# ---------------------------------------------------------------------------
# named linux terminals
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "kind, binary, flag",
    [
        ("terminator", "terminator", "--working-directory"),
        ("gnome-terminal", "gnome-terminal", "--working-directory"),
        ("konsole", "konsole", "--workdir"),
    ],
)
def test_named_linux_terminals(
    monkeypatch: pytest.MonkeyPatch,
    runs: list[list[str]],
    kind: str,
    binary: str,
    flag: str,
) -> None:
    monkeypatch.setattr(term_mod.sys, "platform", "linux")
    monkeypatch.setattr(term_mod.shutil, "which", _stub_which({binary}))
    term_mod.open_in_terminal("/home/u/work", kind=kind)
    assert runs == [[f"/usr/bin/{binary}", flag, "/home/u/work"]]


def test_named_terminal_missing_raises(
    monkeypatch: pytest.MonkeyPatch, runs: list[list[str]]
) -> None:
    monkeypatch.setattr(term_mod.sys, "platform", "linux")
    monkeypatch.setattr(term_mod.shutil, "which", _stub_which(set()))
    with pytest.raises(FileNotFoundError, match="terminator"):
        term_mod.open_in_terminal("/home/u/work", kind="terminator")
    assert runs == []


# ---------------------------------------------------------------------------
# tmux
# ---------------------------------------------------------------------------


def test_tmux_inside_tmux_opens_new_window(
    monkeypatch: pytest.MonkeyPatch, runs: list[list[str]]
) -> None:
    """When ``$TMUX`` is set, the helper just adds a new window in the
    current session — no system-terminal launch needed."""
    monkeypatch.setattr(term_mod.shutil, "which", _stub_which({"tmux"}))
    monkeypatch.setenv("TMUX", "/tmp/tmux-501/default,1234,0")
    term_mod.open_in_terminal("/tmp/foo", kind="tmux")
    assert runs == [["/usr/bin/tmux", "new-window", "-c", "/tmp/foo"]]


def test_tmux_outside_tmux_creates_session_and_attaches_macos(
    monkeypatch: pytest.MonkeyPatch, runs: list[list[str]]
) -> None:
    """Without ``$TMUX``, the helper creates / attaches a detached
    session, opens a fresh window cd'd into the path, then asks the
    system terminal (Terminal.app via osascript on macOS) to attach."""
    monkeypatch.setattr(term_mod.sys, "platform", "darwin")
    monkeypatch.setattr(term_mod.shutil, "which", _stub_which({"tmux"}))
    monkeypatch.delenv("TMUX", raising=False)

    term_mod.open_in_terminal("/tmp/foo", kind="tmux")

    assert len(runs) == 3
    assert runs[0] == [
        "/usr/bin/tmux",
        "new-session",
        "-d",
        "-A",
        "-s",
        "atelier",
        "-c",
        "/tmp/foo",
    ]
    assert runs[1] == [
        "/usr/bin/tmux",
        "new-window",
        "-t",
        "atelier",
        "-c",
        "/tmp/foo",
    ]
    # AppleScript invocation — assert structure, not exact quoting.
    assert runs[2][0] == "osascript"
    assert "-e" in runs[2]
    script = runs[2][runs[2].index("-e") + 1]
    assert "Terminal" in script
    assert "tmux attach -t atelier" in script


def test_tmux_outside_tmux_linux_uses_terminal_with_exec_flag(
    monkeypatch: pytest.MonkeyPatch, runs: list[list[str]]
) -> None:
    monkeypatch.setattr(term_mod.sys, "platform", "linux")
    monkeypatch.setattr(
        term_mod.shutil,
        "which",
        _stub_which({"tmux", "x-terminal-emulator"}),
    )
    monkeypatch.delenv("TMUX", raising=False)

    term_mod.open_in_terminal("/tmp/foo", kind="tmux")

    # Last call attaches via the chosen terminal. ``x-terminal-emulator``
    # uses ``-e`` per ``_LINUX_EXEC_TERMINALS``.
    assert runs[-1] == [
        "/usr/bin/x-terminal-emulator",
        "-e",
        "/usr/bin/tmux",
        "attach",
        "-t",
        "atelier",
    ]


def test_tmux_missing_binary_raises(
    monkeypatch: pytest.MonkeyPatch, runs: list[list[str]]
) -> None:
    monkeypatch.setattr(term_mod.shutil, "which", _stub_which(set()))
    monkeypatch.delenv("TMUX", raising=False)
    with pytest.raises(FileNotFoundError, match="tmux"):
        term_mod.open_in_terminal("/tmp/foo", kind="tmux")
    assert runs == []
