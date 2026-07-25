"""Planning manifest adapter for the generic loop run state interface."""

from __future__ import annotations

from copy import deepcopy

from src.domain.loop import actions as loop_actions
from src.domain.loop.models import LoopRunTarget
from src.domain.loop.ports import LoopRunRepository
from src.domain.planning import actions
from src.domain.planning.loop_persistence import persist_artifact_run
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
        manifest = actions.manifest_or_raise(self._files, work_slug)
        detail = actions.detail_or_raise(self._files, work_slug, self._artifact_id)
        actions.require_executable(detail.artifact)
        run = actions.find_run_by_id(
            actions.artifact_runs_for_update(manifest, self._artifact_id),
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
        """Write one run back to the manifest and shared loop repository."""
        manifest = actions.manifest_or_raise(self._files, target.work_slug)
        detail = actions.detail_or_raise(
            self._files,
            target.work_slug,
            self._artifact_id,
        )
        runs = actions.artifact_runs_for_update(manifest, self._artifact_id)
        for index, run in enumerate(runs):
            if run.get("id") == target.run_id:
                runs[index] = deepcopy(target.run)
                break
        else:
            raise PlanArtifactRunNotFound(f"plan artifact run not found: {target.run_id}")
        manifest["updated_at"] = loop_actions.now_iso()
        self._files.write_manifest(target.work_slug, manifest)
        persist_artifact_run(
            self._repository,
            work_slug=target.work_slug,
            artifact=detail.artifact,
            run=target.run,
        )


__all__ = ["PlanningLoopRunStore"]
