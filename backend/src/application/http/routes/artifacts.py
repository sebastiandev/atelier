"""Artifacts REST router.

Single endpoint today: revealing the underlying file for a doc-type
artifact. Listing happens under the work parent (``works/{slug}/artifacts``)
because that's the natural traversal; reveal is keyed by the artifact's
own slug because the FE knows it from the rail row.
"""

import subprocess

from fastapi import APIRouter, HTTPException, status

from src.application.http.routes.works import WorkStoreDep
from src.infrastructure.filesystem.reveal import open_in_file_browser

router = APIRouter()


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
