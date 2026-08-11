"""Manage PR-stage passes for Planning artifact runs."""

from __future__ import annotations

from dataclasses import dataclass

from src.domain.artifacts.pr_status import PrLifecycleGateway
from src.domain.loop import pr_lifecycle, pr_review
from src.domain.loop.models import LoopRunTarget
from src.domain.loop.ports import LoopRunRepository
from src.domain.planning import actions
from src.domain.planning.dtos import PlanArtifactDetail
from src.domain.planning.loop_store import PlanningLoopRunStore
from src.domain.planning.ports import PlanningFiles
from src.domain.planning.service import (
    PlanArtifactNotFound,
    PlanArtifactRunNotFound,
    PlanningNotStarted,
)
from src.domain.workstore.ports import WorkStore


class WorkNotFound(ValueError):
    """The Work does not exist."""


@dataclass(frozen=True)
class CreatePlanningPrRequest:
    """Command input for adding a PR stage to one Planning run."""

    work_slug: str
    artifact_id: str
    run_id: str
    setup: pr_lifecycle.PrSetup


@dataclass(frozen=True)
class SendPlanningPrFeedbackRequest:
    """Command input for starting another pass from PR feedback."""

    work_slug: str
    artifact_id: str
    run_id: str
    comments: tuple[pr_lifecycle.PrFeedbackItem, ...] = ()
    instruction: str = ""


@dataclass(frozen=True)
class RefreshPlanningPrRequest:
    """Command input for synchronizing one Planning run's pull request."""

    work_slug: str
    artifact_id: str
    run_id: str
    force: bool = False


def create_stage(
    workstore: WorkStore,
    files: PlanningFiles,
    loop_runs: LoopRunRepository,
    req: CreatePlanningPrRequest,
) -> PlanArtifactDetail:
    """Add and schedule a Work-local PR stage on an accepted Planning run."""
    target, store = _target(workstore, files, loop_runs, req)
    pr_lifecycle.add_one_off_stage(target, req.setup)
    store.save(target)
    return actions.detail_or_raise(files, loop_runs, req.work_slug, req.artifact_id)


def send_feedback(
    workstore: WorkStore,
    files: PlanningFiles,
    loop_runs: LoopRunRepository,
    req: SendPlanningPrFeedbackRequest,
) -> PlanArtifactDetail:
    """Schedule selected PR feedback through the same Planning loop run."""
    target, store = _target(workstore, files, loop_runs, req)
    pr_lifecycle.prepare_feedback(target, req.comments, req.instruction)
    store.save(target)
    return actions.detail_or_raise(files, loop_runs, req.work_slug, req.artifact_id)


async def refresh(
    workstore: WorkStore,
    files: PlanningFiles,
    loop_runs: LoopRunRepository,
    gateway: PrLifecycleGateway,
    req: RefreshPlanningPrRequest,
) -> PlanArtifactDetail:
    """Synchronize PR review state for one Planning run."""
    target, store = _target(workstore, files, loop_runs, req)
    await pr_review.refresh(
        target, gateway, force=req.force, save=lambda: store.save(target)
    )
    store.save(target)
    return actions.detail_or_raise(files, loop_runs, req.work_slug, req.artifact_id)


def _target(
    workstore: WorkStore,
    files: PlanningFiles,
    loop_runs: LoopRunRepository,
    req: (
        CreatePlanningPrRequest
        | SendPlanningPrFeedbackRequest
        | RefreshPlanningPrRequest
    ),
) -> tuple[LoopRunTarget, PlanningLoopRunStore]:
    """Load one Planning loop target and its persistence adapter."""
    if workstore.get_work(req.work_slug) is None:
        raise WorkNotFound(f"work not found: {req.work_slug}")
    detail = actions.detail_or_raise(files, loop_runs, req.work_slug, req.artifact_id)
    actions.require_executable(detail.artifact)
    store = PlanningLoopRunStore(files, loop_runs, req.artifact_id)
    target = store.load(req.work_slug, req.run_id)
    if target is None:
        raise PlanArtifactRunNotFound(f"plan artifact run not found: {req.run_id}")
    return target, store


__all__ = [
    "CreatePlanningPrRequest",
    "PlanArtifactNotFound",
    "PlanArtifactRunNotFound",
    "PlanningNotStarted",
    "RefreshPlanningPrRequest",
    "SendPlanningPrFeedbackRequest",
    "WorkNotFound",
    "create_stage",
    "refresh",
    "send_feedback",
]
