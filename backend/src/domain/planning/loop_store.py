"""Planning manifest adapter for the generic loop run state interface."""

from __future__ import annotations

from copy import deepcopy

from src.domain.loop.models import LoopRunTarget
from src.domain.loop.ports import LoopRunRepository
from src.domain.planning import actions
from src.domain.planning.loop_persistence import (
    artifact_run_rows,
    persist_artifact_run,
)
from src.domain.planning.ports import PlanningFiles
from src.domain.planning.service import PlanArtifactRunNotFound


class PlanningLoopRunStore:
    """Load and save one artifact's loop state through its Planning manifest."""

    def __init__(
        self,
        files: PlanningFiles,
        repository: LoopRunRepository,
        artifact_id: str,
    ) -> None:
        self._files = files
        self._repository = repository
        self._artifact_id = artifact_id

    def load(self, work_slug: str, run_id: str) -> LoopRunTarget | None:
        """Return one executable artifact run and its prompt-facing metadata."""
        detail = actions.detail_or_raise(
            self._files, self._repository, work_slug, self._artifact_id
        )
        actions.require_executable(detail.artifact)
        run = actions.find_run_by_id(
            artifact_run_rows(self._repository, work_slug, self._artifact_id),
            run_id,
        )
        if run is None:
            return None
        return LoopRunTarget(
            work_slug=work_slug,
            run_id=run_id,
            target_id=detail.artifact.id,
            title=detail.artifact.title,
            source_ref=detail.artifact.source_ref,
            run=deepcopy(run),
        )

    def save(self, target: LoopRunTarget) -> None:
        """Write one run back to the shared loop repository.

        SQL is canonical, so the manifest is not rewritten here: it holds
        run ids only, and those are fixed when the run is created.
        """
        detail = actions.detail_or_raise(
            self._files,
            self._repository,
            target.work_slug,
            self._artifact_id,
        )
        known = {
            row.get("id")
            for row in artifact_run_rows(
                self._repository, target.work_slug, self._artifact_id
            )
        }
        if target.run_id not in known:
            raise PlanArtifactRunNotFound(f"plan artifact run not found: {target.run_id}")
        persist_artifact_run(
            self._repository,
            work_slug=target.work_slug,
            artifact=detail.artifact,
            run=target.run,
        )


__all__ = ["PlanningLoopRunStore"]
