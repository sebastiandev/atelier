"""Delete an agent end-to-end: stop supervisor, remove worktree, wipe data.

Symmetric with ``complete_work``'s per-agent cleanup but for a single
agent on the user's explicit request. The DB row, the workspace agent
dir (transcript + agent.json + contexts/), and the per-agent git
worktree are all removed.

The supervisor's ``stop_agent`` and ``WorktreeManager.remove`` are both
idempotent, so the command is safe to retry if the FS/DB step fails.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING

from src.domain.workstore.ports import WorkStore
from src.domain.worktrees import WorktreeManager

if TYPE_CHECKING:
    from src.domain.supervisor import AgentSupervisorService


@dataclass(frozen=True)
class DeleteAgentRequest:
    agent_slug: str


@dataclass(frozen=True)
class DeleteAgentResult:
    agent_slug: str
    work_slug: str


class AgentNotFound(ValueError):
    """The agent slug doesn't resolve to a stored agent."""


async def execute(
    workstore: WorkStore,
    supervisor: AgentSupervisorService,
    worktree_manager: WorktreeManager,
    req: DeleteAgentRequest,
) -> DeleteAgentResult:
    work_slug = workstore.get_work_slug_for_agent(req.agent_slug)
    if work_slug is None:
        raise AgentNotFound(f"agent not found: {req.agent_slug}")

    # Stop supervisor first so no SDK process is racing the FS clean-up.
    await supervisor.stop_agent(req.agent_slug)
    # Remove the per-agent worktree (idempotent — handles missing dirs,
    # dirty trees, lock files internally).
    worktree_manager.remove(work_slug, req.agent_slug)
    # Drop the agent dir + DB row.
    workstore.delete_agent(req.agent_slug)

    return DeleteAgentResult(agent_slug=req.agent_slug, work_slug=work_slug)


__all__ = [
    "AgentNotFound",
    "DeleteAgentRequest",
    "DeleteAgentResult",
    "execute",
]
