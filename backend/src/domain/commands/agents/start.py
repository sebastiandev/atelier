"""Create an agent and register it with the supervisor."""

from __future__ import annotations

from typing import TYPE_CHECKING

from src.domain.agents.launch import (
    FRESH_AGENT_BASE_REF,
    AgentFolderMissing,
    AgentLaunchRequest,
    InvalidProviderConfig,
    MountedProjectShares,
    WorkNotFound,
    launch_agent,
)
from src.domain.agents.ports import AgentAdapterFactory
from src.domain.connections import ConnectionStore
from src.domain.models import Agent
from src.domain.sharedfolders.ports import SharedFolderStore, ShareProvisioner
from src.domain.workstore.ports import WorkStore
from src.domain.worktrees import WorktreeManager

if TYPE_CHECKING:
    from src.domain.supervisor import AgentSupervisorService

StartAgentRequest = AgentLaunchRequest


async def execute(
    workstore: WorkStore,
    supervisor: AgentSupervisorService,
    worktree_manager: WorktreeManager,
    connection_store: ConnectionStore,
    sharestore: SharedFolderStore,
    share_provisioner: ShareProvisioner,
    adapter_factory: AgentAdapterFactory,
    req: StartAgentRequest,
) -> Agent:
    """Launch one user-requested agent.

    Preconditions: the request references an existing Work and valid provider.
    Postconditions: the agent is fully live or partial state is rolled back.
    """
    return await launch_agent(
        workstore,
        supervisor,
        worktree_manager,
        connection_store,
        sharestore,
        share_provisioner,
        adapter_factory,
        req,
    )


__all__ = [
    "FRESH_AGENT_BASE_REF",
    "AgentFolderMissing",
    "InvalidProviderConfig",
    "MountedProjectShares",
    "StartAgentRequest",
    "WorkNotFound",
    "execute",
]
