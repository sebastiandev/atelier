"""Planning status projection for shared loop runs."""

from __future__ import annotations

from src.domain.loop.dtos import LoopStatus
from src.domain.planning.dtos import PlanRunStatus


def loop_status_for_run_status(status: PlanRunStatus) -> LoopStatus:
    """Project a Planning aggregate status onto the shared loop status."""
    if status == PlanRunStatus.RUNNING:
        return LoopStatus.RUNNING
    if status == PlanRunStatus.NEEDS_ATTENTION:
        return LoopStatus.NEEDS_AGENT
    if status == PlanRunStatus.BLOCKED:
        return LoopStatus.BLOCKED_USER
    return LoopStatus.COMPLETED


__all__ = ["loop_status_for_run_status"]
