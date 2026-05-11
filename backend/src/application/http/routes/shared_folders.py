"""Project-scoped shared-folders REST router.

Mounted under ``/api/projects/{project_slug}/shares``. Thin endpoints
that parse → call service → format. All business logic lives behind
``SharedFolderStore``.
"""

from __future__ import annotations

from datetime import datetime
from pathlib import Path
from typing import Annotated, Literal

from fastapi import APIRouter, Depends, HTTPException, Query, Request, status
from pydantic import BaseModel, Field

from src.domain.models import SharedFolder
from src.domain.sharedfolders import (
    CreateExistingShareRequest,
    CreateNewShareRequest,
    InvalidMountPath,
    SharedFolderStore,
    UpdateShareRequest,
)
from src.domain.sharedfolders.service import (
    CustomLocationProtected,
    MountPathConflict,
    ShareNotFound,
)

router = APIRouter()


def get_sharestore(request: Request) -> SharedFolderStore:
    return request.app.state.sharestore  # type: ignore[no-any-return]


ShareStoreDep = Annotated[SharedFolderStore, Depends(get_sharestore)]


# --- Schemas -----------------------------------------------------------------


class ShareSummary(BaseModel):
    slug: str
    name: str
    mount_path: str
    canonical_path: str
    real_path: str | None
    is_custom_location: bool
    created_at: datetime


class NewShareRequest(BaseModel):
    mode: Literal["new", "existing"]
    name: str = Field(min_length=1, max_length=120)
    mount_path: str = Field(min_length=1, max_length=255)
    # For mode=new: optional custom location (None → default).
    # For mode=existing: required path to the existing folder.
    location: str | None = None


class RenameShareRequest(BaseModel):
    name: str = Field(min_length=1, max_length=120)


# --- Endpoints ---------------------------------------------------------------


@router.get(
    "/projects/{project_slug}/shares",
    response_model=list[ShareSummary],
)
def list_shares_endpoint(
    project_slug: str,
    sharestore: ShareStoreDep,
    request: Request,
) -> list[ShareSummary]:
    paths = request.app.state.workspace_paths
    shares = sharestore.list_for_project(project_slug)
    return [_to_summary(s, project_slug, paths) for s in shares]


@router.post(
    "/projects/{project_slug}/shares",
    response_model=ShareSummary,
    status_code=status.HTTP_201_CREATED,
)
def create_share_endpoint(
    project_slug: str,
    payload: NewShareRequest,
    sharestore: ShareStoreDep,
    request: Request,
) -> ShareSummary:
    paths = request.app.state.workspace_paths
    try:
        if payload.mode == "new":
            real_path = Path(payload.location) if payload.location else None
            if real_path is not None and not real_path.is_absolute():
                raise HTTPException(
                    status.HTTP_400_BAD_REQUEST,
                    detail="location must be an absolute path",
                )
            record = sharestore.create_new(
                CreateNewShareRequest(
                    project_slug=project_slug,
                    name=payload.name,
                    mount_path=payload.mount_path,
                    real_path=real_path,
                )
            )
        else:  # "existing"
            if not payload.location:
                raise HTTPException(
                    status.HTTP_400_BAD_REQUEST,
                    detail="location is required for mode='existing'",
                )
            existing = Path(payload.location)
            if not existing.is_absolute():
                raise HTTPException(
                    status.HTTP_400_BAD_REQUEST,
                    detail="location must be an absolute path",
                )
            record = sharestore.create_from_existing(
                CreateExistingShareRequest(
                    project_slug=project_slug,
                    name=payload.name,
                    mount_path=payload.mount_path,
                    existing_path=existing,
                )
            )
    except InvalidMountPath as e:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, detail=str(e)) from e
    except MountPathConflict as e:
        raise HTTPException(status.HTTP_409_CONFLICT, detail=str(e)) from e
    except ValueError as e:
        # Catches "project not found" + "existing path is not a directory"
        raise HTTPException(status.HTTP_400_BAD_REQUEST, detail=str(e)) from e
    return _to_summary(record.share, project_slug, paths)


@router.patch(
    "/projects/{project_slug}/shares/{share_slug}",
    response_model=ShareSummary,
)
def rename_share_endpoint(
    project_slug: str,
    share_slug: str,
    payload: RenameShareRequest,
    sharestore: ShareStoreDep,
    request: Request,
) -> ShareSummary:
    paths = request.app.state.workspace_paths
    try:
        record = sharestore.rename(
            UpdateShareRequest(
                project_slug=project_slug,
                share_slug=share_slug,
                name=payload.name,
            )
        )
    except ShareNotFound as e:
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail=str(e)) from e
    except ValueError as e:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, detail=str(e)) from e
    return _to_summary(record.share, project_slug, paths)


@router.delete(
    "/projects/{project_slug}/shares/{share_slug}",
    status_code=status.HTTP_204_NO_CONTENT,
)
def delete_share_endpoint(
    project_slug: str,
    share_slug: str,
    sharestore: ShareStoreDep,
    delete_data: bool = Query(default=False, alias="delete_data"),
) -> None:
    try:
        if delete_data:
            sharestore.delete_contents(project_slug, share_slug)
        else:
            sharestore.stop_sharing(project_slug, share_slug)
    except ShareNotFound as e:
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail=str(e)) from e
    except CustomLocationProtected as e:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, detail=str(e)) from e


def _to_summary(
    share: SharedFolder, project_slug: str, paths: object
) -> ShareSummary:
    assert share.slug is not None
    # paths is WorkspacePaths; typed loosely on app.state so we don't
    # bring an infra import into this route file's signature.
    canonical = paths.project_share_dir(project_slug, share.slug)  # type: ignore[attr-defined]
    return ShareSummary(
        slug=share.slug,
        name=share.name,
        mount_path=share.mount_path,
        canonical_path=str(canonical),
        real_path=str(share.real_path) if share.real_path is not None else None,
        is_custom_location=share.real_path is not None,
        created_at=share.created_at,
    )
