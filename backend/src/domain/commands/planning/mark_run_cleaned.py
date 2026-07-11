"""Mark an artifact agent run workspace as cleaned up."""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING

from src.domain.commands.planning import _loop_persistence
from src.domain.loop.dtos import LoopStatus
from src.domain.loop.ports import LoopRunRepository
from src.domain.planning import actions
from src.domain.planning.dtos import PlanArtifactDetail, PlanRunStatus
from src.domain.planning.ports import PlanningFiles
from src.domain.planning.service import (
    PlanArtifactNotFound,
    PlanArtifactRunNotFound,
    PlanningNotStarted,
)
from src.domain.workstore.ports import WorkStore
from src.domain.worktrees import WorktreeManager

if TYPE_CHECKING:
    from src.domain.supervisor import AgentSupervisorService


class WorkNotFound(ValueError):
    """The work_slug doesn't exist."""


class PlanArtifactRunNotCleanable(ValueError):
    """The run has not been approved or accepted."""


@dataclass(frozen=True)
class MarkArtifactRunCleanedRequest:
    """Command input for recording cleanup of one artifact run."""

    work_slug: str
    artifact_id: str
    run_id: str


async def execute(
    workstore: WorkStore,
    files: PlanningFiles,
    loop_runs: LoopRunRepository,
    supervisor: AgentSupervisorService,
    worktree_manager: WorktreeManager,
    req: MarkArtifactRunCleanedRequest,
) -> PlanArtifactDetail:
    """Remove run-owned agents and persist the cleaned state.

    Preconditions: Work, artifact, and linked run exist and the run is accepted.
    Postconditions: all stored stage agents are removed and the run is marked cleaned.
    """
    if workstore.get_work(req.work_slug) is None:
        raise WorkNotFound(f"work not found: {req.work_slug}")
    manifest = actions.manifest_or_raise(files, req.work_slug)
    detail = actions.detail_or_raise(files, req.work_slug, req.artifact_id)
    run = actions.find_run_by_id(
        actions.artifact_runs_for_update(manifest, req.artifact_id),
        req.run_id,
    )
    if run is None:
        raise PlanArtifactRunNotFound(f"plan artifact run not found: {req.run_id}")
    loop = actions.dict_or_empty(run.get("loop"))
    loop_status = actions.loop_status(loop.get("status"), actions.run_status(run))
    if actions.run_status(run) != PlanRunStatus.ACCEPTED or loop_status not in {
        LoopStatus.ACCEPTED,
        LoopStatus.CLEANED,
    }:
        raise PlanArtifactRunNotCleanable(
            f"plan artifact run is not accepted: {req.run_id}"
        )

    for agent_slug in _run_agent_slugs(run, loop):
        if workstore.get_work_slug_for_agent(agent_slug) != req.work_slug:
            continue
        await supervisor.stop_agent(agent_slug)
        worktree_manager.remove(req.work_slug, agent_slug)
        workstore.delete_agent(agent_slug)

    now = actions.now_iso()
    run["cleanup_at"] = now
    loop["status"] = LoopStatus.CLEANED.value
    loop["status_reason"] = "Approved result cleaned up."
    run["loop"] = loop
    manifest["updated_at"] = now
    files.write_manifest(req.work_slug, manifest)
    _loop_persistence.persist_artifact_run(
        loop_runs,
        work_slug=req.work_slug,
        artifact=detail.artifact,
        run=run,
    )
    return actions.detail_or_raise(files, req.work_slug, req.artifact_id)


def _run_agent_slugs(run: dict[str, object], loop: dict[str, object]) -> tuple[str, ...]:
    """Return each run-owned agent once in launch order."""
    slugs: list[str] = []
    owned = loop.get("owned_agent_slugs")
    if isinstance(owned, list):
        slugs.extend(slug for slug in owned if isinstance(slug, str) and slug)
    initial = actions.str_or_none(run.get("agent_slug"))
    if initial and initial not in slugs:
        slugs.append(initial)
    stages = loop.get("stages")
    if isinstance(stages, list):
        for stage in stages:
            if not isinstance(stage, dict):
                continue
            slug = actions.str_or_none(stage.get("agent_slug"))
            if slug and slug not in slugs:
                slugs.append(slug)
    return tuple(slugs)


__all__ = [
    "MarkArtifactRunCleanedRequest",
    "PlanArtifactNotFound",
    "PlanArtifactRunNotCleanable",
    "PlanArtifactRunNotFound",
    "PlanningNotStarted",
    "WorkNotFound",
    "execute",
]
