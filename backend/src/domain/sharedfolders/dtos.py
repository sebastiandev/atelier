"""DTOs for the SharedFolderStore boundary.

Request shapes mirror the two creation flows ("+ New" and "+ Add
existing") plus rename + delete. Read shapes are simple wrappers so
the public boundary doesn't leak the raw entity.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from src.domain.models import SharedFolder


@dataclass(frozen=True, kw_only=True)
class CreateNewShareRequest:
    """Create a brand-new share. ``real_path`` is optional — None means
    the share lives at its canonical location under the project dir
    (``<workspace>/projects/<PRJ>/shared/<slug>/``). When set, the
    canonical path becomes a symlink to ``real_path``."""

    project_slug: str
    name: str
    mount_path: str
    real_path: Path | None = None


@dataclass(frozen=True, kw_only=True)
class CreateExistingShareRequest:
    """Register an already-populated folder as a share. Nondestructive:
    Atelier just creates a symlink at the canonical path pointing to
    ``existing_path``; no data is moved or copied."""

    project_slug: str
    name: str
    mount_path: str
    existing_path: Path


@dataclass(frozen=True, kw_only=True)
class UpdateShareRequest:
    """Rename the share's display label. Mount path is immutable
    post-creation — changing it would orphan existing worktree
    symlinks."""

    project_slug: str
    share_slug: str
    name: str


@dataclass(frozen=True)
class ShareRecord:
    """Read-side wrapper."""

    share: SharedFolder


@dataclass(frozen=True, kw_only=True)
class ShareSummary:
    """Lightweight projection passed to ``render_system_prompt`` so the
    agent's system prompt can list shares without leaking storage
    details (real_path, etc.) the model doesn't need."""

    name: str
    mount_path: str


__all__ = [
    "CreateExistingShareRequest",
    "CreateNewShareRequest",
    "ShareRecord",
    "ShareSummary",
    "UpdateShareRequest",
]
