"""Accept or reject a plan artifact source proposal."""

from dataclasses import dataclass
from typing import Literal

from src.domain.planning import actions
from src.domain.planning.dtos import PlanArtifactDetail
from src.domain.planning.ports import PlanningFiles
from src.domain.planning.service import (
    PlanArtifactConflict,
    PlanArtifactNotFound,
    PlanArtifactProposalNotFound,
    PlanningNotStarted,
)
from src.domain.workstore.ports import WorkStore


class WorkNotFound(ValueError):
    """The work_slug doesn't exist."""


@dataclass(frozen=True)
class ResolveArtifactProposalRequest:
    """Command input for resolving a source proposal."""

    work_slug: str
    artifact_id: str
    proposal_id: str
    decision: Literal["accept", "reject"]


def execute(
    workstore: WorkStore,
    files: PlanningFiles,
    req: ResolveArtifactProposalRequest,
) -> PlanArtifactDetail:
    """Accept or reject a source proposal.

    Preconditions: Work, artifact, and proposal exist.
    Postconditions: accepted proposals update source; rejected proposals do not.
    """
    if workstore.get_work(req.work_slug) is None:
        raise WorkNotFound(f"work not found: {req.work_slug}")
    manifest = actions.manifest_or_raise(files, req.work_slug)
    detail = actions.detail_or_raise(files, req.work_slug, req.artifact_id)
    proposal = actions.proposal_for_update(
        manifest, req.artifact_id, req.proposal_id
    )
    if proposal is None:
        raise PlanArtifactProposalNotFound(
            f"plan artifact proposal not found: {req.proposal_id}"
        )
    now = actions.now_iso()
    if req.decision == "accept":
        if proposal.get("source_hash") != detail.artifact.source_hash:
            raise PlanArtifactConflict("proposal is stale; reload before accepting")
        files.write_text(
            req.work_slug,
            detail.artifact.path,
            actions.ensure_newline(
                actions.str_or_empty(proposal.get("proposed_content"))
            ),
        )
        proposal["status"] = "accepted"
        manifest["source_hashes"] = actions.current_hashes(files, req.work_slug)
    else:
        proposal["status"] = "rejected"
    proposal["resolved_at"] = now
    manifest["updated_at"] = now
    files.write_manifest(req.work_slug, manifest)
    return actions.detail_or_raise(files, req.work_slug, req.artifact_id)


__all__ = [
    "PlanArtifactConflict",
    "PlanArtifactNotFound",
    "PlanArtifactProposalNotFound",
    "PlanningNotStarted",
    "ResolveArtifactProposalRequest",
    "WorkNotFound",
    "execute",
]
