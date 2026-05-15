"""Artifacts REST router.

Single endpoint today: revealing the underlying file for a doc-type
artifact. Listing happens under the work parent (``works/{slug}/artifacts``)
because that's the natural traversal; reveal is keyed by the artifact's
own slug because the FE knows it from the rail row.
"""

import subprocess

from fastapi import APIRouter, HTTPException, Request, status
from pydantic import BaseModel

from src.application.http.routes.works import WorkStoreDep
from src.infrastructure.filesystem.reveal import open_in_file_browser

router = APIRouter()


class RefreshPrStatusesResponse(BaseModel):
    """Result of a PR-status refresh request.

    ``ran=False`` when the call was throttled or the poller isn't
    available; counts are zero in that case. ``ran=True`` with all
    counts at zero means the work has no non-terminal PR rows to
    check, which is the steady-state case for completed work.
    """

    ran: bool
    checked: int = 0
    updated: int = 0
    skipped: int = 0
    not_modified: int = 0


@router.post("/artifacts/refresh-pr-statuses")
async def refresh_pr_statuses_endpoint(
    request: Request,
) -> RefreshPrStatusesResponse:
    """Trigger an out-of-band PR-status refresh.

    Called by the FE when a WorkView mounts so a freshly-opened tab
    sees fresh statuses without waiting up to 5 minutes for the
    scheduled cycle. The poller throttles repeat calls within ~30s so
    bouncing between tabs doesn't fan out hundreds of GitHub fetches.

    Returns ``ran=false`` when the throttle short-circuits — the
    frontend treats that as 'cached data is current enough', no
    follow-up refetch needed.
    """
    poller = getattr(request.app.state, "pr_status_poller", None)
    if poller is None:
        return RefreshPrStatusesResponse(ran=False)
    result = await poller.refresh_now()
    if result is None:
        return RefreshPrStatusesResponse(ran=False)
    return RefreshPrStatusesResponse(
        ran=True,
        checked=result.checked,
        updated=result.updated,
        skipped=result.skipped,
        not_modified=result.not_modified,
    )


@router.post(
    "/artifacts/{artifact_slug}/reveal", status_code=status.HTTP_204_NO_CONTENT
)
def reveal_artifact_endpoint(
    artifact_slug: str, workstore: WorkStoreDep
) -> None:
    """Open a doc-type artifact in the OS file browser. Symmetric with
    the work / agent reveal endpoints — shells out to ``open`` /
    ``xdg-open`` / ``explorer`` depending on platform.

    404 if the slug is unknown; 422 if the artifact isn't a doc or has
    no resolved path."""
    artifact = workstore.get_artifact_by_slug(artifact_slug)
    if artifact is None:
        raise HTTPException(
            status.HTTP_404_NOT_FOUND, detail=f"artifact not found: {artifact_slug}"
        )
    if artifact.type != "doc" or not artifact.doc_path:
        raise HTTPException(
            status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="reveal is only supported for doc-type artifacts",
        )
    try:
        open_in_file_browser(artifact.doc_path)
    except (OSError, subprocess.SubprocessError) as exc:
        raise HTTPException(
            status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"reveal failed: {exc}",
        ) from exc
