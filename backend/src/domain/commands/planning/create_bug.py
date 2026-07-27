"""Create a source-backed bug from an artifact finding."""

from dataclasses import dataclass

from src.domain.loop.ports import LoopRunRepository
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
class CreateArtifactBugRequest:
    """Command input for creating a bug finding source."""

    work_slug: str
    artifact_id: str
    title: str
    description: str


def execute(
    workstore: WorkStore,
    files: PlanningFiles,
    loop_runs: LoopRunRepository,
    req: CreateArtifactBugRequest,
) -> PlanArtifactDetail:
    """Create a bug doc and link it from the source artifact.

    Preconditions: Work and source artifact exist.
    Postconditions: a bug Markdown source exists and is linked from the artifact.
    """
    if workstore.get_work(req.work_slug) is None:
        raise WorkNotFound(f"work not found: {req.work_slug}")
    manifest = actions.manifest_or_raise(files, req.work_slug)
    detail = actions.detail_or_raise(files, loop_runs, req.work_slug, req.artifact_id)
    bug_id, bug_path = actions.next_bug_path(files, req.work_slug)
    now = actions.now_iso()
    files.write_text(
        req.work_slug,
        bug_path,
        actions.bug_template(detail.artifact, bug_id, req.title, req.description),
    )
    actions.upsert_artifact_entry(
        manifest,
        path=bug_path,
        title=actions.clean(req.title) or bug_id,
        artifact_kind="bug",
        executable=True,
        dependencies=[],
    )
    links = actions.artifact_tracking_for_update(manifest, req.artifact_id)
    links.append(
        {
            "id": actions.next_id("track", links),
            "kind": "bug",
            "title": actions.clean(req.title) or bug_id,
            "url": "",
            "status": "open",
            "ref": bug_id,
            "notes": actions.clean(req.description) or "",
            "created_at": now,
        }
    )
    manifest["updated_at"] = now
    manifest["source_hashes"] = actions.current_hashes(files, loop_runs, req.work_slug)
    files.write_manifest(req.work_slug, manifest)
    return actions.detail_or_raise(files, loop_runs, req.work_slug, req.artifact_id)


__all__ = [
    "CreateArtifactBugRequest",
    "PlanArtifactNotFound",
    "PlanningNotStarted",
    "WorkNotFound",
    "execute",
]
