"""Finish the planning conversation phase."""

from src.domain.loop.ports import LoopRunRepository
from src.domain.planning import actions
from src.domain.planning.dtos import WorkPlanView
from src.domain.planning.ports import PlanningFiles
from src.domain.planning.service import PlanningNotStarted
from src.domain.workstore.ports import WorkStore


class WorkNotFound(ValueError):
    """The work_slug doesn't exist."""


def execute(
    workstore: WorkStore,
    files: PlanningFiles,
    loop_runs: LoopRunRepository,
    work_slug: str
) -> WorkPlanView:
    """Move an existing Work plan into the planned overview phase.

    Preconditions: the Work exists and planning has started.
    Postconditions: manifest phase is ``planned`` and source hashes are current.
    """
    if workstore.get_work(work_slug) is None:
        raise WorkNotFound(f"work not found: {work_slug}")
    manifest = actions.manifest_or_raise(files, work_slug)
    manifest["phase"] = "planned"
    manifest["updated_at"] = actions.now_iso()
    manifest["source_hashes"] = actions.current_hashes(files, loop_runs, work_slug)
    files.write_manifest(work_slug, manifest)
    return actions.get_plan_or_raise(files, loop_runs, work_slug)


__all__ = ["PlanningNotStarted", "WorkNotFound", "execute"]
