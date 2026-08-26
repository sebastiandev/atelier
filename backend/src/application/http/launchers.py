"""Run an OS launcher for an HTTP route, with one error contract.

Reveal, open-in-console and open-in-editor each hand a directory to a
different launcher and then map the same two failures the same way: an
unresolvable entity is a 404, a launcher that will not start is a 500
carrying the underlying reason. Doing that once keeps the routes to
"resolve, launch" and keeps their error text consistent.
"""

from __future__ import annotations

import subprocess
from collections.abc import Callable
from pathlib import Path

from fastapi import HTTPException, status

from src.infrastructure.filesystem.workspace_targets import WorkspaceNotFound


def launch(
    target: Path,
    launcher: Callable[[str], None],
    *,
    label: str,
) -> None:
    """Launch ``target``, translating failure into an HTTP error.

    ``label`` names the action in the message the UI shows -- "reveal",
    "open in console" -- so the detail line reads as one sentence.
    """
    try:
        launcher(str(target))
    except (OSError, subprocess.SubprocessError) as exc:
        raise HTTPException(
            status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"{label} failed: {exc}",
        ) from exc


def resolved(resolve: Callable[[], Path]) -> Path:
    """Call a workspace resolver, translating "not found" into a 404."""
    try:
        return resolve()
    except WorkspaceNotFound as exc:
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail=str(exc)) from exc


__all__ = ["launch", "resolved"]
