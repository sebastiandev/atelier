"""Goal-driven adapter for the generic loop run state interface.

Backs a run with no source -- one started from a goal in Loop mode.
Story-sourced runs use ``PlanningLoopRunStore``.
"""

from __future__ import annotations

from copy import deepcopy
from dataclasses import replace
from pathlib import Path

from src.domain.loop import actions
from src.domain.loop.dtos import LoopTargetKind
from src.domain.loop.models import LoopRunRecord, LoopRunTarget
from src.domain.loop.persistence import persist_run
from src.domain.loop.ports import LoopRunRepository

DEFAULT_TARGET_ID = "objective"
DEFAULT_WORKTREE_SLUG = "loop"


def loop_run_key(run_id: str) -> str:
    """Return the stable repository key for one standalone run id."""
    return actions.loop_run_id(DEFAULT_TARGET_ID, "", run_id=run_id)


def get_run_record(
    repository: LoopRunRepository,
    work_slug: str,
    run_id: str,
) -> LoopRunRecord | None:
    """Return one objective record, excluding other loop target kinds."""
    record = repository.get(work_slug, loop_run_key(run_id))
    if record is None or record.target_kind != LoopTargetKind.OBJECTIVE:
        return None
    return _with_retained_workspace(repository, record)


def list_runs(
    repository: LoopRunRepository,
    work_slug: str,
) -> tuple[LoopRunRecord, ...]:
    """Return every standalone objective record for one Work."""
    return tuple(
        _with_retained_workspace(repository, record)
        for record in repository.list_for_work(work_slug)
        if record.target_kind == LoopTargetKind.OBJECTIVE
    )


def _with_retained_workspace(
    repository: LoopRunRepository,
    record: LoopRunRecord,
) -> LoopRunRecord:
    """Repair the legacy sourced-run workspace mismatch in the read model."""
    workspace = actions.str_or_empty(record.state.get("workspace_path"))
    source_id = actions.str_or_none(record.state.get("source_run_id"))
    if Path(workspace).name != DEFAULT_WORKTREE_SLUG or source_id is None:
        return record
    source = repository.get(record.work_slug, loop_run_key(source_id))
    if source is None or source.target_kind != LoopTargetKind.OBJECTIVE:
        return record
    loop = actions.dict_or_empty(record.state.get("loop"))
    source_agent_slug = actions.str_or_none(loop.get("source_agent_slug"))
    if source_agent_slug != actions.str_or_none(source.state.get("agent_slug")):
        return record
    source_workspace = actions.str_or_empty(source.state.get("workspace_path"))
    if not source_workspace or Path(source_workspace).name == DEFAULT_WORKTREE_SLUG:
        return record
    state = dict(record.state)
    state["workspace_path"] = source_workspace
    return replace(record, state=state)


class LoopRunStore:
    """Load and save standalone loop runs from the loop repository."""

    def __init__(self, repository: LoopRunRepository) -> None:
        self._repository = repository

    def load(self, work_slug: str, run_id: str) -> LoopRunTarget | None:
        """Return one loop run snapshot without Planning projection."""
        record = get_run_record(self._repository, work_slug, run_id)
        if record is None:
            return None
        root = actions.str_or_empty(record.state.get("root_path"))
        return LoopRunTarget(
            work_slug=work_slug,
            run_id=run_id,
            target_id=DEFAULT_TARGET_ID,
            title=record.target_ref,
            source_ref=root,
            run=deepcopy(record.state),
        )

    def save(self, target: LoopRunTarget) -> None:
        """Persist one objective snapshot as a generic loop record."""
        persist_run(
            self._repository,
            work_slug=target.work_slug,
            target_kind=LoopTargetKind.OBJECTIVE,
            target_ref=target.title,
            run=target.run,
        )


__all__ = [
    "DEFAULT_TARGET_ID",
    "DEFAULT_WORKTREE_SLUG",
    "LoopRunStore",
    "get_run_record",
    "list_runs",
    "loop_run_key",
]
