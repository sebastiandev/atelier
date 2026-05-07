"""Mark a Work as completed: stop running agents, remove worktrees, flip status.

Symmetric with detach: the command stops supervisor-side state without
destroying audit trail. Transcripts (NDJSON) and the work folder under
``~/Atelier/works/<slug>/`` are preserved — completion is about clearing
out scratch state (per-agent git worktrees + supervisor tasks), not
nuking history. A separate "delete permanently" lives outside this
command.

The supervisor's ``stop_agent`` and ``WorktreeManager.remove`` are both
idempotent, so the command runs cleanly even if some agents are already
detached or never had a worktree provisioned.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING

from src.domain.workstore.dtos import UpdateWorkRequest
from src.domain.workstore.ports import WorkStore
from src.domain.worktrees import WorktreeManager

if TYPE_CHECKING:
    # Same import-cycle dance as detach.py: AgentSupervisorService transitively
    # imports back into this layer; fine to defer to the call-site type.
    from src.domain.supervisor import AgentSupervisorService


@dataclass(frozen=True)
class CompleteWorkRequest:
    work_slug: str


@dataclass(frozen=True)
class CompleteWorkResult:
    work_slug: str
    agent_count: int
    """Number of agents that were on the work. All had ``stop_agent`` and
    ``worktree.remove`` invoked (both idempotent, so the count is the
    upper bound — actual side effects depend on prior state)."""


class WorkNotFound(ValueError):
    """The work slug doesn't resolve to a stored work."""


class WorkNotActive(ValueError):
    """The work isn't in 'active' status — completion only fires once."""


async def execute(
    workstore: WorkStore,
    supervisor: AgentSupervisorService,
    worktree_manager: WorktreeManager,
    req: CompleteWorkRequest,
) -> CompleteWorkResult:
    record = workstore.get_work(req.work_slug)
    if record is None:
        raise WorkNotFound(f"work not found: {req.work_slug}")
    if record.work.status != "active":
        raise WorkNotActive(
            f"work {req.work_slug} is not active (current: {record.work.status})"
        )

    agents = workstore.list_agents_for_work(req.work_slug)
    agent_slugs = [a.slug for a in agents if a.slug is not None]

    # Stop supervisor tasks first so no SDK process is racing the FS clean-up.
    # ``stop_agent`` is idempotent — no-op when the agent isn't currently live.
    for slug in agent_slugs:
        await supervisor.stop_agent(slug)

    # Remove per-agent git worktrees. The manager handles missing dirs +
    # dirty trees + lock files internally (see infrastructure/git/worktree_manager.py).
    for slug in agent_slugs:
        worktree_manager.remove(req.work_slug, slug)

    # Flip status last — keeps the work visible-as-active until the cleanup
    # is actually done, so a crash mid-clean doesn't leave it lying about as
    # "completed" with live processes still hanging on.
    workstore.update_work(
        UpdateWorkRequest(work_slug=req.work_slug, status="completed")
    )

    return CompleteWorkResult(
        work_slug=req.work_slug,
        agent_count=len(agent_slugs),
    )


__all__ = [
    "CompleteWorkRequest",
    "CompleteWorkResult",
    "WorkNotActive",
    "WorkNotFound",
    "execute",
]
