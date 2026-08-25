"""Unit tests for the shell-free Emacs launcher."""

from __future__ import annotations

import subprocess
from typing import Any

import pytest

from src.infrastructure.filesystem import editor as editor_mod


@pytest.mark.parametrize("platform", ["darwin", "linux"])
def test_open_in_emacs_uses_fixed_argv(platform: str, monkeypatch: pytest.MonkeyPatch) -> None:
    calls: list[tuple[list[str], dict[str, Any]]] = []
    monkeypatch.setattr(editor_mod.sys, "platform", platform)
    monkeypatch.setattr(
        editor_mod.subprocess,
        "run",
        lambda args, **kwargs: calls.append((list(args), kwargs)),
    )

    path = "/tmp/work; $(touch nope) with spaces"
    editor_mod.open_in_emacs(path)

    assert len(calls) == 1
    argv, kwargs = calls[0]
    assert argv == ["emacsclient", "-n", "-c", path]
    assert "-s" not in argv
    assert kwargs["check"] is True
    assert kwargs["timeout"] == 10


def test_open_in_emacs_removes_alternate_editor_from_child_environment(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    captured: dict[str, Any] = {}
    monkeypatch.setattr(editor_mod.sys, "platform", "linux")
    monkeypatch.setenv("ALTERNATE_EDITOR", "emacs --daemon")
    monkeypatch.setenv("ATELIER_EDITOR_TEST", "preserved")

    def fake_run(args: list[str], **kwargs: Any) -> object:
        captured.update(kwargs)
        return object()

    monkeypatch.setattr(editor_mod.subprocess, "run", fake_run)
    editor_mod.open_in_emacs("/tmp/work")

    child_env = captured["env"]
    assert isinstance(child_env, dict)
    assert "ALTERNATE_EDITOR" not in child_env
    assert child_env["ATELIER_EDITOR_TEST"] == "preserved"
    assert editor_mod.os.environ["ALTERNATE_EDITOR"] == "emacs --daemon"


def test_open_in_emacs_rejects_windows(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(editor_mod.sys, "platform", "win32")
    with pytest.raises(OSError, match="unsupported on Windows"):
        editor_mod.open_in_emacs("C:/work")


def test_open_in_emacs_propagates_subprocess_errors(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(editor_mod.sys, "platform", "linux")

    def raising(*args: object, **kwargs: object) -> None:
        raise subprocess.CalledProcessError(1, args[0])

    monkeypatch.setattr(editor_mod.subprocess, "run", raising)
    with pytest.raises(subprocess.CalledProcessError):
        editor_mod.open_in_emacs("/tmp/work")


def test_open_in_emacs_propagates_timeout_expired(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(editor_mod.sys, "platform", "linux")

    def raising(*args: object, **kwargs: object) -> None:
        raise subprocess.TimeoutExpired(args[0], kwargs["timeout"])

    monkeypatch.setattr(editor_mod.subprocess, "run", raising)
    with pytest.raises(subprocess.TimeoutExpired) as exc_info:
        editor_mod.open_in_emacs("/tmp/work")
    assert exc_info.value.timeout == 10
