"""Monitor a Planning artifact through the shared Loop engine."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import TYPE_CHECKING
from uuid import uuid4

from src.domain.agents.ports import AgentAdapterFactory
from src.domain.artifacts.pr_status import PrLifecycleGateway
from src.domain.connections import ConnectionStore
from src.domain.loop import monitor as loop_monitor
from src.domain.loop import pr_review
from src.domain.loop.ports import LoopCheckRunner, LoopRunRepository
from src.domain.planning import actions
from src.domain.planning.dtos import PlanArtifactDetail
from src.domain.planning.loop_store import PlanningLoopRunStore
from src.domain.planning.ports import PlanningFiles
from src.domain.sharedfolders.ports import SharedFolderStore, ShareProvisioner
from src.domain.workstore.ports import WorkStore
from src.domain.worktrees import WorktreeManager
from src.settings import Settings

if TYPE_CHECKING:
    from src.domain.supervisor import AgentSupervisorService


@dataclass(frozen=True)
class MonitorArtifactRunRequest:
    """Command input for monitoring one Planning artifact run."""

    work_slug: str
    artifact_id: str
    run_id: str
    poll_interval_seconds: float = 0.5
    idle_timeout_seconds: float | None = None
    worker_id: str = field(default_factory=lambda: f"worker-{uuid4().hex}")


async def execute(
    workstore: WorkStore,
    files: PlanningFiles,
    supervisor: AgentSupervisorService,
    worktree_manager: WorktreeManager,
    connection_store: ConnectionStore,
    sharestore: SharedFolderStore,
    share_provisioner: ShareProvisioner,
    adapter_factory: AgentAdapterFactory,
    check_runner: LoopCheckRunner,
    loop_runs: LoopRunRepository,
    settings: Settings,
    req: MonitorArtifactRunRequest,
    *,
    pr_gateway: PrLifecycleGateway | None = None,
) -> PlanArtifactDetail:
    """Adapt Planning persistence to the shared Loop monitor.

    Preconditions: the Planning artifact run is persisted in the Loop index.
    Postconditions: the shared engine advances it to its next pause state.
    """
    store = PlanningLoopRunStore(files, loop_runs, req.artifact_id)
    target = await loop_monitor.execute(
        workstore,
        store,
        supervisor,
        worktree_manager,
        connection_store,
        sharestore,
        share_provisioner,
        adapter_factory,
        check_runner,
        loop_runs,
        settings,
        loop_monitor.MonitorLoopRunRequest(
            work_slug=req.work_slug,
            run_id=req.run_id,
            poll_interval_seconds=req.poll_interval_seconds,
            idle_timeout_seconds=req.idle_timeout_seconds,
            worker_id=req.worker_id,
        ),
    )
    if pr_gateway is not None:
        await pr_review.post_addressed_replies(target, pr_gateway, lambda: store.save(target))
        store.save(target)
    return actions.detail_or_raise(files, loop_runs, req.work_slug, req.artifact_id)


__all__ = ["MonitorArtifactRunRequest", "execute"]
