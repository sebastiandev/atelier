"""Persist an approved source-backed plan artifact edit."""

from dataclasses import dataclass

from src.domain.planning import actions
from src.domain.planning.dtos import PlanArtifactDetail
from src.domain.planning.ports import PlanningFiles
from src.domain.planning.service import (
    PlanArtifactConflict,
    PlanArtifactNotFound,
    PlanningNotStarted,
)
from src.domain.workstore.ports import WorkStore


class WorkNotFound(ValueError):
    """The work_slug doesn't exist."""


@dataclass(frozen=True)
class SavePlanArtifactRequest:
    """Command input for replacing a plan artifact source file."""

    work_slug: str
    artifact_id: str
    content: str
    expected_hash: str


def execute(
    workstore: WorkStore,
    files: PlanningFiles,
    req: SavePlanArtifactRequest,
) -> PlanArtifactDetail:
    """Replace one plan artifact source file.

    Preconditions: Work and artifact exist, and ``expected_hash`` matches.
    Postconditions: source file, source hashes, and edit record are persisted.
    """
    if workstore.get_work(req.work_slug) is None:
        raise WorkNotFound(f"work not found: {req.work_slug}")
    manifest = actions.manifest_or_raise(files, req.work_slug)
    detail = actions.detail_or_raise(files, req.work_slug, req.artifact_id)
    if detail.artifact.source_hash != req.expected_hash:
        raise PlanArtifactConflict("plan artifact changed; reload before saving")

    files.write_text(req.work_slug, detail.artifact.path, actions.ensure_newline(req.content))
    now = actions.now_iso()
    manifest["updated_at"] = now
    manifest["source_hashes"] = actions.current_hashes(files, req.work_slug)
    approved = actions.dict_or_empty(manifest.get("approved_source_hashes"))
    if approved:
        approved[detail.artifact.path] = manifest["source_hashes"][detail.artifact.path]
        manifest["approved_source_hashes"] = approved
    manifest.setdefault("edit_records", []).append(
        {
            "artifact_id": req.artifact_id,
            "path": detail.artifact.path,
            "before_hash": detail.artifact.source_hash,
            "after_hash": manifest["source_hashes"][detail.artifact.path],
            "updated_at": now,
        }
    )
    files.write_manifest(req.work_slug, manifest)
    return actions.detail_or_raise(files, req.work_slug, req.artifact_id)


__all__ = [
    "PlanArtifactConflict",
    "PlanArtifactNotFound",
    "PlanningNotStarted",
    "SavePlanArtifactRequest",
    "WorkNotFound",
    "execute",
]
