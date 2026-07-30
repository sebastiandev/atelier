"""Archive a Work while preserving its durable history and workspace by default."""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING

from src.domain.chatstore import ChatStore
from src.domain.loop.dtos import LoopStatus
from src.domain.loop.ports import LoopRunRepository
from src.domain.workstore.dtos import UpdateWorkRequest
from src.domain.workstore.ports import WorkStore
from src.domain.worktrees import WorktreeManager, WorktreeState

if TYPE_CHECKING:
    # Same import-cycle dance as detach.py: AgentSupervisorService transitively
    # imports back into this layer; fine to defer to the call-site type.
    from src.domain.supervisor import AgentSupervisorService


@dataclass(frozen=True)
class CompleteWorkRequest:
    """Inputs for archiving one Work."""

    work_slug: str
    remove_workspaces: bool = False


@dataclass(frozen=True)
class CompleteWorkPreview:
    """Current resources and blockers shown before completion."""

    work_slug: str
    agent_count: int
    active_run_count: int
    workspaces: tuple[WorktreeState, ...]


@dataclass(frozen=True)
class CompleteWorkResult:
    """Resources affected by a successful completion."""

    work_slug: str
    agent_count: int
    workspace_count: int
    workspaces_removed: int


class WorkNotFound(ValueError):
    """The work slug doesn't resolve to a stored work."""


class WorkNotActive(ValueError):
    """The work isn't in 'active' status — completion only fires once."""


class WorkHasActiveRuns(ValueError):
    """The Work has a run that must finish or be cancelled first."""


class WorkspaceNotClean(ValueError):
    """One or more workspaces cannot be removed without risking data loss."""


_TERMINAL_RUN_STATUSES = {
    LoopStatus.ACCEPTED,
    LoopStatus.CANCELLED,
    LoopStatus.CLEANED,
    LoopStatus.FAILED,
}


def preview(
    workstore: WorkStore,
    loop_runs: LoopRunRepository,
    worktree_manager: WorktreeManager,
    work_slug: str,
) -> CompleteWorkPreview:
    """Describe completion impact without mutating state.

    Preconditions: ``work_slug`` identifies an existing Work.
    Postconditions: no Work, run, agent, transcript, or workspace is changed.
    """
    record = workstore.get_work(work_slug)
    if record is None:
        raise WorkNotFound(f"work not found: {work_slug}")
    active_runs = tuple(
        run
        for run in loop_runs.list_for_work(work_slug)
        if run.status not in _TERMINAL_RUN_STATUSES
    )
    return CompleteWorkPreview(
        work_slug=work_slug,
        agent_count=len(workstore.list_agents_for_work(work_slug)),
        active_run_count=len(active_runs),
        workspaces=worktree_manager.list_states(work_slug),
    )


def workspace_is_removable(state: WorktreeState) -> bool:
    """Return whether an explicit cleanup can remove a workspace safely.

    Preconditions: ``state`` came from the managed WorktreeManager.
    Postconditions: returns true only for an inspectable git worktree with a
    clean index and working tree.
    """
    return state.is_git_repo and state.error is None and not state.status


async def execute(
    workstore: WorkStore,
    loop_runs: LoopRunRepository,
    supervisor: AgentSupervisorService,
    chatstore: ChatStore,
    chat_supervisor: AgentSupervisorService,
    worktree_manager: WorktreeManager,
    req: CompleteWorkRequest,
) -> CompleteWorkResult:
    """Stop runtimes and archive one Work, optionally removing clean worktrees.

    Preconditions: the Work is active, all runs are terminal, and every
    explicitly removed workspace is clean. Postconditions: agent records,
    transcripts, and the Work folder remain; managed workspaces remain unless
    the caller explicitly requested their removal.
    """
    record = workstore.get_work(req.work_slug)
    if record is None:
        raise WorkNotFound(f"work not found: {req.work_slug}")
    if record.work.status != "active":
        raise WorkNotActive(
            f"work {req.work_slug} is not active (current: {record.work.status})"
        )

    impact = preview(workstore, loop_runs, worktree_manager, req.work_slug)
    if impact.active_run_count:
        raise WorkHasActiveRuns(
            f"work {req.work_slug} has {impact.active_run_count} active run(s); "
            "finish or cancel them before completing the work"
        )
    if req.remove_workspaces:
        protected = tuple(
            state for state in impact.workspaces if not workspace_is_removable(state)
        )
        if protected:
            names = ", ".join(state.workdir.name for state in protected)
            raise WorkspaceNotClean(
                f"workspace cleanup blocked because these workspaces are dirty or "
                f"cannot be inspected: {names}"
            )

    agents = workstore.list_agents_for_work(req.work_slug)
    agent_slugs = [a.slug for a in agents if a.slug is not None]
    linked_chat_slugs = [
        slug
        for record in chatstore.list_chats()
        if (slug := record.chat.slug) is not None
        and (
            record.chat.promoted_to_work_slug == req.work_slug
            or (
                record.chat.grounding_kind == "work"
                and record.chat.grounding_ref == req.work_slug
            )
        )
    ]

    # Status is the reconnect barrier: once archived, new stream connections
    # replay history without rebuilding an adapter or workspace.
    workstore.update_work(
        UpdateWorkRequest(work_slug=req.work_slug, status="completed")
    )

    # Stop supervisor tasks first so no SDK process is racing the FS clean-up.
    # ``stop_agent`` is idempotent — no-op when the agent isn't currently live.
    for slug in agent_slugs:
        await supervisor.stop_agent(slug)
    for chat_slug in linked_chat_slugs:
        await chat_supervisor.stop_agent(chat_slug)

    if req.remove_workspaces:
        for state in impact.workspaces:
            worktree_manager.remove(req.work_slug, state.workdir.name, force=False)

    return CompleteWorkResult(
        work_slug=req.work_slug,
        agent_count=len(agent_slugs),
        workspace_count=len(impact.workspaces),
        workspaces_removed=(len(impact.workspaces) if req.remove_workspaces else 0),
    )


__all__ = [
    "CompleteWorkPreview",
    "CompleteWorkRequest",
    "CompleteWorkResult",
    "WorkHasActiveRuns",
    "WorkNotActive",
    "WorkNotFound",
    "WorkspaceNotClean",
    "execute",
    "preview",
    "workspace_is_removable",
]
