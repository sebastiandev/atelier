"""Record an agent run against a plan artifact."""

from dataclasses import dataclass

from src.domain.planning import actions
from src.domain.planning.dtos import PlanArtifactDetail
from src.domain.planning.ports import PlanningFiles
from src.domain.planning.service import (
    PlanArtifactNotExecutable,
    PlanArtifactNotFound,
    PlanningNotStarted,
)
from src.domain.workstore.ports import WorkStore


class WorkNotFound(ValueError):
    """The work_slug doesn't exist."""


@dataclass(frozen=True)
class RecordArtifactRunRequest:
    """Command input for linking an agent run to an artifact."""

    work_slug: str
    artifact_id: str
    agent_slug: str


def execute(
    workstore: WorkStore,
    files: PlanningFiles,
    req: RecordArtifactRunRequest,
) -> PlanArtifactDetail:
    """Attach the given agent slug to an executable artifact.

    Preconditions: Work exists and artifact is executable.
    Postconditions: manifest stores a running artifact run and loop snapshot.
    """
    if workstore.get_work(req.work_slug) is None:
        raise WorkNotFound(f"work not found: {req.work_slug}")
    manifest = actions.manifest_or_raise(files, req.work_slug)
    detail = actions.detail_or_raise(files, req.work_slug, req.artifact_id)
    actions.require_executable(detail.artifact)

    now = actions.now_iso()
    runs = actions.artifact_runs_for_update(manifest, req.artifact_id)
    run = actions.find_run(runs, req.agent_slug)
    if run is None:
        runs.append(
            {
                "agent_slug": req.agent_slug,
                "status": "running",
                "started_at": now,
                "completed_at": None,
                "loop": actions.running_loop_snapshot(
                    artifact_id=req.artifact_id,
                    agent_slug=req.agent_slug,
                    existing=None,
                ),
            }
        )
    else:
        existing_loop = actions.dict_or_empty(run.get("loop"))
        run["status"] = "running"
        run.setdefault("started_at", now)
        run["completed_at"] = None
        run["loop"] = actions.running_loop_snapshot(
            artifact_id=req.artifact_id,
            agent_slug=req.agent_slug,
            existing=existing_loop,
        )
    manifest["updated_at"] = now
    files.write_manifest(req.work_slug, manifest)
    return actions.detail_or_raise(files, req.work_slug, req.artifact_id)


__all__ = [
    "PlanArtifactNotExecutable",
    "PlanArtifactNotFound",
    "PlanningNotStarted",
    "RecordArtifactRunRequest",
    "WorkNotFound",
    "execute",
]
