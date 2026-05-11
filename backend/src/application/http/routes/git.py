"""GET /api/git/branches — list local branches in a folder's git repo.

Used by the New Agent dialog so the branch picker can offer existing
branches instead of forcing the user to type a name. Returns ``[]`` for
non-git folders so the FE can render a friendly "not a git repo" hint
instead of branching on error codes.
"""

from __future__ import annotations

from pathlib import Path

from fastapi import APIRouter, HTTPException, Query
from pydantic import BaseModel

from src.infrastructure.git.branches import list_branches

router = APIRouter()


class BranchListing(BaseModel):
    path: str
    branches: list[str]


@router.get("/git/branches", response_model=BranchListing)
def git_branches(path: str = Query(min_length=1)) -> BranchListing:
    expanded = Path(path).expanduser()
    if not expanded.is_absolute():
        raise HTTPException(
            status_code=400, detail="path must be absolute (or start with ~)"
        )
    return BranchListing(path=str(expanded), branches=list_branches(expanded))
