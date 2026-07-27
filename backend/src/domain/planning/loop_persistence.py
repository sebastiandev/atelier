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
    Postconditions: SQL holds the run's canonical state.
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


def artifact_run_rows(
    repository: LoopRunRepository,
    work_slug: str,
    artifact_id: str,
) -> list[dict[str, Any]]:
    """Return one artifact's run states, oldest first.

    Preconditions: none; an artifact with no runs yields an empty list.
    Postconditions: the returned dicts are the canonical run states from
    SQL. Mutating one only takes effect once it is passed back through
    ``persist_artifact_run``.

    Ordering is by run id, which is allocated as ``run-NNN`` and so sorts
    chronologically. Callers rely on it: ``select_run`` treats the last
    entry as the latest run.
    """
    rows = [
        record.state
        for record in repository.list_for_work(work_slug)
        if (source := record.source) is not None and source.ref == artifact_id
    ]
    rows.sort(key=lambda row: str(row.get("id") or ""))
    return rows


__all__ = ["artifact_run_rows", "persist_artifact_run"]
