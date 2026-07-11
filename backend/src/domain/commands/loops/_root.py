"""Shared working-root lookup for loop definition commands."""

from src.domain.loop.definitions import LoopRootUnavailable
from src.domain.planning.ports import PlanningSessionRepository
from src.domain.workstore.ports import WorkStore


class WorkNotFound(ValueError):
    """The requested Work does not exist."""


def resolve_working_root(
    workstore: WorkStore,
    planning_sessions: PlanningSessionRepository,
    work_slug: str,
) -> str:
    """Resolve a Work's persisted repository root.

    Preconditions: the caller is operating on a Work-scoped loop resource.
    Postconditions: returns the PlanningSession root without changing state.
    """
    if workstore.get_work(work_slug) is None:
        raise WorkNotFound(f"work not found: {work_slug}")
    session = planning_sessions.get_by_work_slug(work_slug)
    if session is None or not session.root_path:
        raise LoopRootUnavailable(f"loop working root is not configured: {work_slug}")
    return session.root_path


__all__ = ["WorkNotFound", "resolve_working_root"]
