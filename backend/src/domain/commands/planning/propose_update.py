"""Create a reviewable source proposal for a plan artifact."""

from dataclasses import dataclass

from src.domain.planning import actions
from src.domain.planning.dtos import PlanArtifactDetail
from src.domain.planning.ports import PlanningFiles
from src.domain.planning.service import (
    PlanArtifactNotFound,
    PlanningNotStarted,
)
from src.domain.workstore.ports import WorkStore


class WorkNotFound(ValueError):
    """The work_slug doesn't exist."""


@dataclass(frozen=True)
class ProposeArtifactUpdateRequest:
    """Command input for storing a source proposal."""

    work_slug: str
    artifact_id: str
    title: str
    proposed_content: str


def execute(
    workstore: WorkStore,
    files: PlanningFiles,
    req: ProposeArtifactUpdateRequest,
) -> PlanArtifactDetail:
    """Store a reviewable full-document source proposal.

    Preconditions: Work and source artifact exist.
    Postconditions: a pending proposal is recorded; source Markdown is unchanged.
    """
    if workstore.get_work(req.work_slug) is None:
        raise WorkNotFound(f"work not found: {req.work_slug}")
    manifest = actions.manifest_or_raise(files, req.work_slug)
    detail = actions.detail_or_raise(files, req.work_slug, req.artifact_id)
    now = actions.now_iso()
    proposals = actions.artifact_proposals_for_update(manifest, req.artifact_id)
    proposals.append(
        {
            "id": actions.next_id("proposal", proposals),
            "artifact_id": req.artifact_id,
            "title": actions.clean(req.title)
            or f"Proposed update for {req.artifact_id}",
            "path": detail.artifact.path,
            "source_hash": detail.artifact.source_hash,
            "proposed_content": actions.ensure_newline(req.proposed_content),
            "status": "pending",
            "created_at": now,
            "resolved_at": None,
        }
    )
    manifest["updated_at"] = now
    files.write_manifest(req.work_slug, manifest)
    return actions.detail_or_raise(files, req.work_slug, req.artifact_id)


__all__ = [
    "PlanArtifactNotFound",
    "PlanningNotStarted",
    "ProposeArtifactUpdateRequest",
    "WorkNotFound",
    "execute",
]
