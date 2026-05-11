"""Doc-artifact state derivation.

Pure functions over a doc artifact's resolved path:

  - ``classify_location`` → ``worktree`` / ``shared`` / ``None``
  - ``git_state`` → ``committed`` / ``uncommitted`` / ``None``

Both are recomputed every time the artifacts list is served — no
caching, no DB columns. Cheap in practice (the rail rarely shows more
than a handful of doc artifacts) and avoids a stale-state class of
bugs.

The two axes are deliberately orthogonal:

  - *Location* tells the user whether the doc lives in the agent's
    git-backed worktree (likely on a feature branch) or in a project
    shared folder (typically scratch / planning notes the agent can
    read/write across handoffs).
  - *Git state* answers "did the agent commit it?" — useful only when
    the file is inside a git repo. Shared folders are usually plain
    dirs, so ``git_state`` returns ``None`` for them and the FE shows
    only the location chip.
"""

from __future__ import annotations

import subprocess
from collections.abc import Iterable
from pathlib import Path
from typing import Literal

LocationKind = Literal["worktree", "shared"]
GitState = Literal["committed", "uncommitted"]


def classify_location(
    doc_path: Path, *, worktree: Path | None, share_roots: Iterable[Path]
) -> LocationKind | None:
    """Decide whether ``doc_path`` lives in the agent's worktree or in a
    shared folder. Returns ``None`` when it's under neither (which
    shouldn't happen — the marker validator rejects such paths — but
    we don't want a stale row to crash list rendering).

    Comparison is on the *resolved* real path: shared folders may be
    accessed via a symlink under the worktree (``shares/<mount>/...``)
    and we want those classified as ``shared``, not ``worktree``.
    """
    real = _safe_resolve(doc_path)
    if real is None:
        return None
    worktree_real = _safe_resolve(worktree) if worktree is not None else None
    share_reals = [r for r in (_safe_resolve(s) for s in share_roots) if r is not None]

    # Check shares first so a doc accessed via the worktree's symlink
    # into a shared folder (``./shares/<mount>/x.md``) classifies as
    # ``shared`` rather than ``worktree``.
    if any(_is_inside(real, root) for root in share_reals):
        return "shared"
    if worktree_real is not None and _is_inside(real, worktree_real):
        return "worktree"
    return None


def git_state(doc_path: Path) -> GitState | None:
    """``committed`` if the file matches HEAD; ``uncommitted`` if the
    working tree (or index) differs from HEAD; ``None`` when the path
    isn't inside a git repo or git isn't reachable.

    "Staged but not modified" collapses into ``committed`` for v1 — the
    agent flow is typically Write → record_doc with no manual ``git
    add``, so staged-clean is a rounding-error state and an extra chip
    would just be noise.
    """
    real = _safe_resolve(doc_path)
    if real is None or not real.is_file():
        return None
    parent = real.parent
    if not _is_git_repo(parent):
        return None
    # ``--porcelain`` is line-per-file; an empty result means the file
    # is clean relative to HEAD + index. Any output (M/A/D/R/?) means
    # there's a working-tree or index diff.
    try:
        result = subprocess.run(
            ["git", "status", "--porcelain", "--", str(real)],
            cwd=str(parent),
            check=False,
            capture_output=True,
            text=True,
            timeout=2.0,
        )
    except (OSError, subprocess.SubprocessError):
        return None
    if result.returncode != 0:
        return None
    return "uncommitted" if result.stdout.strip() else "committed"


def _safe_resolve(path: Path | None) -> Path | None:
    if path is None:
        return None
    try:
        return path.resolve()
    except (OSError, RuntimeError):
        return None


def _is_inside(child: Path, parent: Path) -> bool:
    try:
        child.relative_to(parent)
        return True
    except ValueError:
        return False


def _is_git_repo(start: Path) -> bool:
    try:
        result = subprocess.run(
            ["git", "rev-parse", "--is-inside-work-tree"],
            cwd=str(start),
            check=False,
            capture_output=True,
            text=True,
            timeout=2.0,
        )
    except (OSError, subprocess.SubprocessError):
        return False
    return result.returncode == 0 and result.stdout.strip() == "true"


__all__ = ["GitState", "LocationKind", "classify_location", "git_state"]
