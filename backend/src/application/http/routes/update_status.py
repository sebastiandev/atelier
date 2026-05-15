"""Thin route that returns the last-known update status.

The poller in ``infrastructure/update_check/`` owns the cycle; this
route only formats the snapshot for the frontend. A 200 with
``available=false`` is the default when the poller hasn't completed a
successful cycle yet (e.g. backend just started, or the host has no
network) — the UI degrades quietly rather than flashing a banner.
"""

from __future__ import annotations

from fastapi import APIRouter, Request
from pydantic import BaseModel

router = APIRouter()


class UpdateStatusResponse(BaseModel):
    available: bool
    repo_path: str
    current_sha: str | None = None
    latest_sha: str | None = None


@router.get("/update-status", response_model=UpdateStatusResponse)
def get_update_status(request: Request) -> UpdateStatusResponse:
    poller = getattr(request.app.state, "update_check_poller", None)
    checker = getattr(request.app.state, "update_checker", None)
    repo_path = getattr(checker, "repo_path", "") if checker else ""
    status = getattr(poller, "status", None) if poller else None
    if status is None:
        return UpdateStatusResponse(available=False, repo_path=repo_path)
    return UpdateStatusResponse(
        available=status.available,
        repo_path=status.repo_path,
        current_sha=status.current_sha,
        latest_sha=status.latest_sha,
    )
