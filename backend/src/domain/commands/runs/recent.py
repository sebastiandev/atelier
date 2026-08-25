"""List the most recently updated loop runs across every Work.

Feeds the ⌘K picker, which spans works and therefore cannot use the
per-work ``GET /works/{slug}/runs`` listing. Rows carry the run's Work
name and, for runs a planning story triggered, that story's title.

Deliberately *not* under ``commands/loops``: that package may not import
planning or sibling commands (``tests/unit/domain/loop/test_architecture``
enforces it) so the Loop engine stays independent of the features that
trigger it. This is the composition of the two, so it lives outside the
boundary and depends inward on both.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass

from src.domain.commands.loops.runs import (
    RECENT_RUNS_DEFAULT_LIMIT,
    list_recent_runs,
)
from src.domain.loop.models import LoopRunRecord
from src.domain.loop.ports import LoopRunRepository
from src.domain.planning.ports import PlanningFiles
from src.domain.planning.service import PlanningService
from src.domain.workstore.ports import WorkStore

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class RecentRun:
    """One recent run with the labels a cross-work row needs."""

    record: LoopRunRecord
    work_name: str
    source_title: str | None


def execute(
    repository: LoopRunRepository,
    workstore: WorkStore,
    planning: PlanningFiles,
    limit: int = RECENT_RUNS_DEFAULT_LIMIT,
) -> tuple[RecentRun, ...]:
    """Return recent runs, newest first, labelled for display.

    Preconditions: none. ``limit`` is bounded by ``list_recent_runs``.

    Postconditions: nothing is written. Ordering is the repository's --
    this only decorates. A run whose Work or plan cannot be read still
    appears, with an empty ``work_name`` / ``None`` title: a listing
    that drops rows because one plan file is unreadable is worse than
    one that shows them unlabelled.
    """
    runs = list_recent_runs(repository, limit)
    if not runs:
        return ()

    names = {work.slug: work.name for work in workstore.list_works()}
    # One plan read per Work, not per run, and only for Works that
    # actually have story-triggered runs -- this is the only file I/O on
    # the path and it is what would make the listing expensive.
    story_works = {
        run.work_slug for run in runs if run.source is not None
    }
    titles = {
        work_slug: _artifact_titles(planning, repository, work_slug)
        for work_slug in story_works
    }
    return tuple(
        RecentRun(
            record=run,
            work_name=names.get(run.work_slug, ""),
            source_title=(
                titles.get(run.work_slug, {}).get(run.source.ref)
                if run.source is not None
                else None
            ),
        )
        for run in runs
    )


def _artifact_titles(
    planning: PlanningFiles,
    repository: LoopRunRepository,
    work_slug: str,
) -> dict[str, str]:
    """Map artifact id -> title for one Work's plan, or {} if unreadable."""
    try:
        plan = PlanningService(planning, repository).get_plan(work_slug)
    except Exception:  # a bad plan must not fail the whole listing
        logger.debug("recent runs: plan unreadable for %s", work_slug, exc_info=True)
        return {}
    if plan is None:
        return {}
    return {artifact.id: artifact.title for artifact in plan.artifacts}


__all__ = ["RecentRun", "execute"]
