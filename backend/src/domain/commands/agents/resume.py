"""Re-register an existing agent with the supervisor."""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING

from src.domain.agents import resume_runtime
from src.domain.models import Agent
from src.domain.sharedfolders.ports import SharedFolderStore, ShareProvisioner
from src.domain.workstore.ports import WorkStore
from src.domain.worktrees import WorktreeManager
from src.settings import Settings

if TYPE_CHECKING:
    from src.domain.supervisor import AgentSupervisorService

AgentNotFound = resume_runtime.AgentNotFound


@dataclass(frozen=True)
class ResumeAgentRequest:
    """Inputs for re-registering a stored agent runtime."""

    work_slug: str
    agent_slug: str


async def execute(
    workstore: WorkStore,
    supervisor: AgentSupervisorService,
    worktree_manager: WorktreeManager,
    sharestore: SharedFolderStore,
    share_provisioner: ShareProvisioner,
    settings: Settings,
    req: ResumeAgentRequest,
) -> Agent:
    """Re-register an existing agent with the supervisor.

    Preconditions: ``req`` identifies a stored agent row.
    Postconditions: the supervisor has a lazy runtime for the agent and any
    detached CLI transcript events have been merged.
    """
    return await resume_runtime.resume_agent(
        workstore,
        supervisor,
        worktree_manager,
        sharestore,
        share_provisioner,
        settings,
        resume_runtime.ResumeAgentRequest(
            work_slug=req.work_slug,
            agent_slug=req.agent_slug,
        ),
    )


async def catch_up_cli_events(
    workstore: WorkStore,
    worktree_manager: WorktreeManager,
    *,
    work_slug: str,
    agent_slug: str,
) -> bool:
    """Merge provider CLI transcript entries since the last CLI cursor."""
    return await resume_runtime.catch_up_cli_events(
        workstore,
        worktree_manager,
        work_slug=work_slug,
        agent_slug=agent_slug,
    )


__all__ = [
    "AgentNotFound",
    "ResumeAgentRequest",
    "catch_up_cli_events",
    "execute",
]
