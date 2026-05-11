"""List branches in an arbitrary git repo on disk.

Used by the New Agent dialog's branch picker so the user can choose an
existing branch instead of typing one. Lives outside ``WorktreeManager``
because the operation has nothing to do with per-agent worktree state —
it just enumerates ``refs/heads/*`` in whatever folder the user pointed
at.

Returns ``[]`` for non-git folders so callers can present a friendly
"not a git repo" hint without distinguishing failure modes.
"""

from __future__ import annotations

import logging
import subprocess
from pathlib import Path

_log = logging.getLogger(__name__)


def list_branches(path: Path) -> list[str]:
    """Return local branch names in ``path``'s git repo, sorted by most
    recent committer date (so the user's likely-target branch surfaces
    first). Returns ``[]`` for non-git folders or any git failure."""
    if not path.exists() or not path.is_dir():
        return []
    try:
        result = subprocess.run(
            [
                "git",
                "for-each-ref",
                "--sort=-committerdate",
                "--format=%(refname:short)",
                "refs/heads/",
            ],
            cwd=str(path),
            capture_output=True,
            text=True,
            check=True,
        )
    except (subprocess.CalledProcessError, FileNotFoundError):
        # Not a git repo, or git binary missing. Either way, no branches
        # to offer — let the FE fall back to free-text entry.
        return []
    return [line for line in result.stdout.splitlines() if line]


__all__ = ["list_branches"]
