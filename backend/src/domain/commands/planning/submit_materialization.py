"""Record framework-generated planning files from a metadata-only package."""

from __future__ import annotations

from dataclasses import dataclass

from src.domain.planning import materialization
from src.domain.planning.dtos import (
    PlanArtifactEntry,
    PlanningFramework,
    PlanningProfile,
    WorkPlanView,
)
from src.domain.planning.ports import PlanningFiles
from src.domain.workstore.ports import WorkStore

WorkNotFound = materialization.WorkNotFound
PlanningFrameworkNotReady = materialization.PlanningFrameworkNotReady
InvalidPlanMaterialization = materialization.InvalidPlanMaterialization


@dataclass(frozen=True)
class SubmitPlanMaterializationRequest:
    """Metadata-only package for files a framework already wrote.

    Preconditions: all ``artifacts`` paths are relative Markdown files under
    the planning folder and already exist on disk.
    Postconditions: manifest metadata and source hashes reflect those files.
    """

    work_slug: str
    root_path: str
    framework: PlanningFramework
    profile: PlanningProfile
    artifacts: tuple[PlanArtifactEntry, ...]


def execute(
    workstore: WorkStore,
    files: PlanningFiles,
    req: SubmitPlanMaterializationRequest,
) -> WorkPlanView:
    """Persist metadata for framework-generated planning files.

    Preconditions: Work exists, framework is ready in ``root_path``, and every
    artifact path exists under ``.atelier/planning/<work_slug>/``.
    Postconditions: no Markdown source content is changed; manifest metadata
    and source hashes are updated.
    """
    return materialization.submit_plan_materialization(
        workstore,
        files,
        work_slug=req.work_slug,
        root_path=req.root_path,
        framework=req.framework,
        profile=req.profile,
        artifacts=req.artifacts,
    )


__all__ = [
    "InvalidPlanMaterialization",
    "PlanningFrameworkNotReady",
    "SubmitPlanMaterializationRequest",
    "WorkNotFound",
    "execute",
]
