"""Git-backed implementation of the update checker.

Runs ``git fetch <remote> <branch>`` and compares the local ``HEAD`` to
the fetched remote tip. The repo root is detected once at construction
time (via the backend source layout — ``main.py`` lives at
``backend/src/main.py``, so the repo root is two parents up from this
package's tree). Construction validates that the path is in fact a git
working tree; if not, the checker exists in an inert state and every
call returns ``None`` so the poller / route can carry on without
errors.

Design notes:

- The git invocations run in a worker thread via ``asyncio.to_thread``
  so the event loop isn't blocked by ``git fetch``'s network call.
- All git calls use ``-C <repo_root>`` so the checker is robust to
  whatever the process's cwd happens to be.
- The remote/branch default to ``origin/main`` but are configurable for
  forks that point ``origin`` at their own repo and want to track
  ``upstream/main`` instead. Today the wiring uses the defaults; the
  knob exists so a future Setting can flip it without code change.
"""

from __future__ import annotations

import asyncio
import logging
import shutil
import subprocess
from pathlib import Path

from src.domain.update_check import UpdateStatus

_log = logging.getLogger(__name__)


def _default_repo_root() -> Path:
    """Resolve the Atelier repo root from this module's location.

    The package tree is ``backend/src/infrastructure/update_check/`` —
    four parents up from this file is the repo root. We don't call
    ``git rev-parse --show-toplevel`` here because we want this to
    succeed at import time even on hosts that don't have git installed
    (the checker will return ``None`` at run time in that case).
    """

    return Path(__file__).resolve().parents[4]


class GitUpdateChecker:
    """Compares local HEAD to <remote>/<branch>.

    Errors during ``git fetch`` (offline host, auth failure, network
    timeouts) are logged and surfaced as ``None``. A ``None`` return
    means "we couldn't check this cycle" — the poller treats that as
    "keep the last good status".
    """

    def __init__(
        self,
        *,
        repo_root: Path | None = None,
        remote: str = "origin",
        branch: str = "main",
        fetch_timeout_seconds: float = 30.0,
    ) -> None:
        self._repo_root = (repo_root or _default_repo_root()).resolve()
        self._remote = remote
        self._branch = branch
        self._fetch_timeout = fetch_timeout_seconds
        self._git = shutil.which("git")
        self._enabled = self._git is not None and self._is_git_worktree()
        if not self._enabled:
            _log.info(
                "update-checker disabled: git=%s repo=%s",
                bool(self._git), self._repo_root,
            )

    def _is_git_worktree(self) -> bool:
        if self._git is None:
            return False
        return (self._repo_root / ".git").exists()

    @property
    def repo_path(self) -> str:
        return str(self._repo_root)

    async def __call__(self) -> UpdateStatus | None:
        if not self._enabled:
            return None
        try:
            return await asyncio.to_thread(self._check_sync)
        except Exception:
            _log.exception("update-check failed")
            return None

    def _check_sync(self) -> UpdateStatus | None:
        assert self._git is not None
        fetch = subprocess.run(
            [self._git, "-C", str(self._repo_root),
             "fetch", "--quiet", self._remote, self._branch],
            capture_output=True,
            text=True,
            timeout=self._fetch_timeout,
        )
        if fetch.returncode != 0:
            # Fork without an upstream-style remote, offline host, etc.
            # Don't spam logs on every 2h cycle — debug level is enough.
            _log.debug(
                "git fetch failed (rc=%s): %s",
                fetch.returncode, fetch.stderr.strip(),
            )
            return None

        current = self._rev_parse("HEAD")
        latest = self._rev_parse(f"{self._remote}/{self._branch}")
        if not current or not latest:
            return None

        return UpdateStatus(
            available=current != latest,
            current_sha=current,
            latest_sha=latest,
            repo_path=str(self._repo_root),
        )

    def _rev_parse(self, ref: str) -> str | None:
        assert self._git is not None
        proc = subprocess.run(
            [self._git, "-C", str(self._repo_root), "rev-parse", ref],
            capture_output=True,
            text=True,
            timeout=5.0,
        )
        if proc.returncode != 0:
            return None
        return proc.stdout.strip() or None


__all__ = ["GitUpdateChecker"]
