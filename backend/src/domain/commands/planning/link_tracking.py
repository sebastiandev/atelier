"""Attach lightweight tracking metadata to a plan artifact."""

from dataclasses import dataclass

from src.domain.planning import actions
from src.domain.planning.dtos import (
    PlanArtifactDetail,
    PlanTrackingKind,
)
from src.domain.planning.ports import PlanningFiles
from src.domain.planning.service import (
    PlanArtifactNotFound,
    PlanningNotStarted,
)
from src.domain.workstore.ports import WorkStore


class WorkNotFound(ValueError):
    """The work_slug doesn't exist."""


@dataclass(frozen=True)
class LinkArtifactTrackingRequest:
    """Command input for tracking links attached to an artifact."""

    work_slug: str
    artifact_id: str
    kind: PlanTrackingKind
    title: str
    url: str = ""
    status: str = ""
    ref: str = ""
    notes: str = ""


def execute(
    workstore: WorkStore,
    files: PlanningFiles,
    req: LinkArtifactTrackingRequest,
) -> PlanArtifactDetail:
    """Persist a Jira, PR, blocker, or bug tracking link.

    Preconditions: Work and artifact exist.
    Postconditions: tracking metadata is persisted in the plan manifest.
    """
    if workstore.get_work(req.work_slug) is None:
        raise WorkNotFound(f"work not found: {req.work_slug}")
    manifest = actions.manifest_or_raise(files, req.work_slug)
    actions.detail_or_raise(files, req.work_slug, req.artifact_id)
    now = actions.now_iso()
    links = actions.artifact_tracking_for_update(manifest, req.artifact_id)
    links.append(
        {
            "id": actions.next_id("track", links),
            "kind": actions.tracking_kind(req.kind),
            "title": actions.clean(req.title) or req.kind.title(),
            "url": actions.clean(req.url) or "",
            "status": actions.clean(req.status) or "",
            "ref": actions.clean(req.ref) or "",
            "notes": actions.clean(req.notes) or "",
            "created_at": now,
        }
    )
    manifest["updated_at"] = now
    files.write_manifest(req.work_slug, manifest)
    return actions.detail_or_raise(files, req.work_slug, req.artifact_id)


__all__ = [
    "LinkArtifactTrackingRequest",
    "PlanArtifactNotFound",
    "PlanningNotStarted",
    "WorkNotFound",
    "execute",
]
