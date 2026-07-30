"""Retry the failed stage of a Planning artifact run in place.

The Planning twin of ``commands.loops.runs.retry_stage``: both are thin
adapters over the one shared ``lifecycle.resume(retry_failed=True)``. Retry
is its own verb here (rather than a flag on ``resume_run``) so a change to
retry semantics -- the continuation hint, a model/effort override -- lands on
both surfaces instead of only the standalone one.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING

from src.domain.agents.ports import AgentAdapterFactory
from src.domain.connections import ConnectionStore
from src.domain.loop import lifecycle
from src.domain.loop.ports import LoopRunRepository
from src.domain.planning import actions
from src.domain.planning.dtos import PlanArtifactDetail
from src.domain.planning.loop_store import PlanningLoopRunStore
from src.domain.planning.ports import PlanningFiles
from src.domain.planning.service import (
    PlanArtifactNotExecutable,
    PlanArtifactNotFound,
    PlanArtifactRunNotFound,
    PlanningNotStarted,
)
from src.domain.sharedfolders.ports import SharedFolderStore, ShareProvisioner
from src.domain.workstore.ports import WorkStore
from src.domain.worktrees import WorktreeManager
from src.settings import Settings

if TYPE_CHECKING:
    from src.domain.supervisor import AgentSupervisorService


class WorkNotFound(ValueError):
    """The work_slug doesn't exist."""


class AgentNotFound(ValueError):
    """The run agent doesn't belong to the Work."""


class PlanArtifactRunNotResumable(ValueError):
    """The run cannot retry from its current state."""


@dataclass(frozen=True)
class RetryArtifactStageRequest:
    """Command input for retrying one failed artifact-run stage.

    ``model``/``effort`` optionally override the retry agent on the same
    provider; both absent reuses the failed attempt's configuration.
    """

    work_slug: str
    artifact_id: str
    run_id: str
    model: str | None = None
    effort: str | None = None


async def execute(
    workstore: WorkStore,
    files: PlanningFiles,
    loop_runs: LoopRunRepository,
    supervisor: AgentSupervisorService,
    worktree_manager: WorktreeManager,
    connection_store: ConnectionStore,
    sharestore: SharedFolderStore,
    share_provisioner: ShareProvisioner,
    adapter_factory: AgentAdapterFactory,
    settings: Settings,
    req: RetryArtifactStageRequest,
) -> PlanArtifactDetail:
    """Retry the failed current stage in its existing run and workspace.

    Preconditions: Work, artifact, and run exist, and the run failed on a
    retryable pinned stage. Postconditions: the same run/worktree and a fresh
    stage agent are active for one additional attempt, optionally with a
    same-provider model/effort override.
    """
    if workstore.get_work(req.work_slug) is None:
        raise WorkNotFound(f"work not found: {req.work_slug}")
    detail = actions.detail_or_raise(files, loop_runs, req.work_slug, req.artifact_id)
    actions.require_executable(detail.artifact)
    store = PlanningLoopRunStore(files, loop_runs, req.artifact_id)
    target = store.load(req.work_slug, req.run_id)
    if target is None:
        raise PlanArtifactRunNotFound(f"plan artifact run not found: {req.run_id}")
    try:
        await lifecycle.resume(
            target,
            workstore,
            supervisor,
            worktree_manager,
            connection_store,
            sharestore,
            share_provisioner,
            adapter_factory,
            settings,
            retry_failed=True,
            retry_model=req.model,
            retry_effort=req.effort,
        )
    except lifecycle.LoopAgentNotFound as exc:
        raise AgentNotFound(str(exc)) from exc
    except lifecycle.LoopRunNotResumable as exc:
        raise PlanArtifactRunNotResumable(str(exc)) from exc
    store.save(target)
    return actions.detail_or_raise(files, loop_runs, req.work_slug, req.artifact_id)


__all__ = [
    "AgentNotFound",
    "PlanArtifactNotExecutable",
    "PlanArtifactNotFound",
    "PlanArtifactRunNotFound",
    "PlanArtifactRunNotResumable",
    "PlanningNotStarted",
    "RetryArtifactStageRequest",
    "WorkNotFound",
    "execute",
]
