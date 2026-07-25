"""Planning projection into the generic loop run repository."""

from __future__ import annotations

from typing import Any

from src.domain.loop.dtos import LoopTargetKind
from src.domain.loop.persistence import persist_run
from src.domain.loop.ports import LoopRunRepository
from src.domain.planning.dtos import PlanArtifactSummary


def persist_artifact_run(
    repository: LoopRunRepository,
    *,
    work_slug: str,
    artifact: PlanArtifactSummary,
    run: dict[str, Any],
) -> None:
    """Persist one Planning artifact run through the generic loop action.

    Preconditions: the run belongs to the supplied source artifact.
    Postconditions: SQL stores the same run and pinned definition as the manifest.
    """
    run_id = run.get("id")
    persist_run(
        repository,
        work_slug=work_slug,
        target_kind=LoopTargetKind.PLANNING_ARTIFACT,
        target_ref=artifact.source_ref,
        artifact_id=artifact.id,
        plan_run_id=run_id if isinstance(run_id, str) else None,
        run=run,
    )


__all__ = ["persist_artifact_run"]
