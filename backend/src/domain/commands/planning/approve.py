"""Approve the latest source-backed Work plan."""

from src.domain.planning import actions
from src.domain.planning.dtos import WorkPlanView
from src.domain.planning.ports import PlanningFiles
from src.domain.planning.service import PlanningNotStarted
from src.domain.workstore.ports import WorkStore


class WorkNotFound(ValueError):
    """The work_slug doesn't exist."""


def execute(
    workstore: WorkStore, files: PlanningFiles, work_slug: str
) -> WorkPlanView:
    """Approve the latest source-backed plan snapshot.

    Preconditions: the Work exists and at least one planning artifact is indexed.
    Postconditions: current source hashes are stored as the launch baseline.
    """
    if workstore.get_work(work_slug) is None:
        raise WorkNotFound(f"work not found: {work_slug}")
    manifest = actions.manifest_or_raise(files, work_slug)
    hashes = actions.current_hashes(files, work_slug)
    if not hashes:
        raise PlanningNotStarted(f"no planning artifacts found: {work_slug}")
    now = actions.now_iso()
    manifest["approved_at"] = now
    manifest["approved_source_hashes"] = hashes
    manifest["source_hashes"] = hashes
    manifest["updated_at"] = now
    files.write_manifest(work_slug, manifest)
    return actions.get_plan_or_raise(files, work_slug)


__all__ = ["PlanningNotStarted", "WorkNotFound", "execute"]
