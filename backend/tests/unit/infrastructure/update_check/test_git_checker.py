"""Unit tests for ``GitUpdateChecker``.

The checker shells out to git, so most of the value here is in the
disabled-path (no .git, no git binary) and in how it interprets
subprocess results — not in the actual subprocess calls. We
monkey-patch ``subprocess.run`` to stub git for the path tests.
"""

from __future__ import annotations

import asyncio
import subprocess
from pathlib import Path
from types import SimpleNamespace

import pytest

from src.infrastructure.update_check.git_checker import GitUpdateChecker


def test_disabled_when_not_a_git_worktree(tmp_path: Path) -> None:
    checker = GitUpdateChecker(repo_root=tmp_path)
    assert checker.repo_path == str(tmp_path.resolve())
    assert asyncio.run(checker()) is None


@pytest.mark.anyio
async def test_reports_available_when_shas_differ(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    (tmp_path / ".git").mkdir()

    def fake_run(cmd, **kwargs):
        if "fetch" in cmd:
            return SimpleNamespace(returncode=0, stdout="", stderr="")
        if "rev-parse" in cmd:
            ref = cmd[-1]
            if ref == "HEAD":
                return SimpleNamespace(returncode=0, stdout="aaaa\n", stderr="")
            if ref.endswith("/main"):
                return SimpleNamespace(returncode=0, stdout="bbbb\n", stderr="")
        raise AssertionError(f"unexpected cmd: {cmd}")

    monkeypatch.setattr(subprocess, "run", fake_run)

    checker = GitUpdateChecker(repo_root=tmp_path)
    status = await checker()
    assert status is not None
    assert status.available is True
    assert status.current_sha == "aaaa"
    assert status.latest_sha == "bbbb"


@pytest.mark.anyio
async def test_reports_unavailable_when_shas_match(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    (tmp_path / ".git").mkdir()

    def fake_run(cmd, **kwargs):
        if "fetch" in cmd:
            return SimpleNamespace(returncode=0, stdout="", stderr="")
        return SimpleNamespace(returncode=0, stdout="cccc\n", stderr="")

    monkeypatch.setattr(subprocess, "run", fake_run)

    checker = GitUpdateChecker(repo_root=tmp_path)
    status = await checker()
    assert status is not None
    assert status.available is False
    assert status.current_sha == "cccc"
    assert status.latest_sha == "cccc"


@pytest.mark.anyio
async def test_fetch_failure_returns_none(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    (tmp_path / ".git").mkdir()

    def fake_run(cmd, **kwargs):
        if "fetch" in cmd:
            return SimpleNamespace(
                returncode=128, stdout="", stderr="could not resolve host\n"
            )
        raise AssertionError("should not rev-parse after a failed fetch")

    monkeypatch.setattr(subprocess, "run", fake_run)

    checker = GitUpdateChecker(repo_root=tmp_path)
    assert await checker() is None


@pytest.fixture
def anyio_backend() -> str:
    return "asyncio"
