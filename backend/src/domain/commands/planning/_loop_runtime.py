"""Shared runtime helpers for planning artifact loops."""

from __future__ import annotations

from typing import TYPE_CHECKING

from src.domain.agents import resume_runtime
from src.domain.sharedfolders.ports import SharedFolderStore, ShareProvisioner
from src.domain.supervisor import AgentTerminated
from src.domain.workstore.ports import WorkStore
from src.domain.worktrees import WorktreeManager
from src.settings import Settings

if TYPE_CHECKING:
    from src.domain.supervisor import AgentSupervisorService


class AgentNotFound(ValueError):
    """The agent_slug doesn't belong to the work."""


async def send_loop_prompt(
    workstore: WorkStore,
    supervisor: AgentSupervisorService,
    worktree_manager: WorktreeManager,
    sharestore: SharedFolderStore,
    share_provisioner: ShareProvisioner,
    settings: Settings,
    *,
    work_slug: str,
    agent_slug: str,
    prompt: str,
) -> None:
    """Send a planning-loop prompt to a linked agent.

    Preconditions: ``agent_slug`` belongs to ``work_slug`` and ``prompt`` is
    the next loop instruction.
    Postconditions: the supervisor has a runtime for the agent and the prompt
    has been appended to the agent transcript.
    """
    await _ensure_registered(
        workstore,
        supervisor,
        worktree_manager,
        sharestore,
        share_provisioner,
        settings,
        work_slug=work_slug,
        agent_slug=agent_slug,
    )
    try:
        await supervisor.send_input(agent_slug, prompt)
    except AgentTerminated:
        await supervisor.stop_agent(agent_slug)
        await _ensure_registered(
            workstore,
            supervisor,
            worktree_manager,
            sharestore,
            share_provisioner,
            settings,
            work_slug=work_slug,
            agent_slug=agent_slug,
        )
        await supervisor.send_input(agent_slug, prompt)


def last_transcript_seq(
    workstore: WorkStore,
    *,
    work_slug: str,
    agent_slug: str,
) -> int:
    """Return the highest transcript sequence seen for one agent.

    Preconditions: ``agent_slug`` belongs to ``work_slug``.
    Postconditions: no transcript events are changed.
    """
    seqs = [
        seq
        for event in workstore.read_transcript_from_cursor(work_slug, agent_slug, 0)
        if isinstance((seq := event.get("seq")), int)
    ]
    return max(seqs, default=0)


async def _ensure_registered(
    workstore: WorkStore,
    supervisor: AgentSupervisorService,
    worktree_manager: WorktreeManager,
    sharestore: SharedFolderStore,
    share_provisioner: ShareProvisioner,
    settings: Settings,
    *,
    work_slug: str,
    agent_slug: str,
) -> None:
    """Ensure the loop agent has a lazy/live supervisor runtime."""
    if supervisor.is_registered(agent_slug):
        return
    try:
        await resume_runtime.resume_agent(
            workstore,
            supervisor,
            worktree_manager,
            sharestore,
            share_provisioner,
            settings,
            resume_runtime.ResumeAgentRequest(
                work_slug=work_slug,
                agent_slug=agent_slug,
            ),
        )
    except resume_runtime.AgentNotFound as exc:
        raise AgentNotFound(str(exc)) from exc


__all__ = ["AgentNotFound", "last_transcript_seq", "send_loop_prompt"]
