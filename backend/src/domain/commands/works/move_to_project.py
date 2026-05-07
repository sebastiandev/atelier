"""Re-parent a Work to a different Project (or to Loose / no project).

The command validates the target project exists (when one is supplied)
and then delegates to ``WorkStore.move_work_to_project``, which writes
both the SQL row and ``work.json``. ``project_slug=None`` is the
"Loose" target — a first-class state, not a degenerate one.

Why a dedicated command instead of folding into ``update_work``: PATCH
semantics treat ``None`` as "leave alone", which collides with the
explicit "set to None to make it Loose" intent for projects. A focused
endpoint sidesteps that ambiguity entirely.
"""

from __future__ import annotations

from dataclasses import dataclass

from src.domain.projectstore.ports import ProjectStore
from src.domain.workstore.dtos import WorkRecord
from src.domain.workstore.ports import WorkStore


@dataclass(frozen=True)
class MoveWorkToProjectRequest:
    work_slug: str
    project_slug: str | None
    """``None`` moves the work to Loose (no project). A non-None value
    must reference an existing project; the command validates this
    before touching the work."""


class WorkNotFound(ValueError):
    """The work slug doesn't resolve to a stored work."""


class ProjectNotFound(ValueError):
    """The target project slug doesn't resolve to a stored project."""


def execute(
    workstore: WorkStore,
    projectstore: ProjectStore,
    req: MoveWorkToProjectRequest,
) -> WorkRecord:
    record = workstore.get_work(req.work_slug)
    if record is None:
        raise WorkNotFound(f"work not found: {req.work_slug}")

    if req.project_slug is not None:
        if projectstore.get_project(req.project_slug) is None:
            raise ProjectNotFound(f"project not found: {req.project_slug}")

    return workstore.move_work_to_project(req.work_slug, req.project_slug)


__all__ = [
    "MoveWorkToProjectRequest",
    "ProjectNotFound",
    "WorkNotFound",
    "execute",
]
