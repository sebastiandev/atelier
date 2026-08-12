"""Runtime actions shared by Loop and Planning commands."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from src.domain.agents import resume_runtime
from src.domain.loop import actions
from src.domain.sharedfolders.ports import SharedFolderStore, ShareProvisioner
from src.domain.supervisor import AgentTerminated
from src.domain.workstore.ports import WorkStore
from src.domain.worktrees import WorktreeManager

if TYPE_CHECKING:
    from src.domain.supervisor import AgentSupervisorService


class AgentNotFound(ValueError):
    """The agent does not belong to the requested Work."""


async def release_run_agents(
    workstore: WorkStore,
    supervisor: AgentSupervisorService,
    *,
    work_slug: str,
    run: dict[str, Any],
    loop: dict[str, Any],
) -> None:
    """Release provider runtimes owned by one terminal loop run.

    Preconditions: ``run`` and ``loop`` are persisted snapshots for ``work_slug``.
    Postconditions: linked runtimes are stopped; agents, transcripts, and files remain.
    """
    for agent_slug in actions.run_agent_slugs(run, loop):
        if workstore.get_work_slug_for_agent(agent_slug) == work_slug:
            await supervisor.stop_agent(agent_slug)


async def send_loop_prompt(
    workstore: WorkStore,
    supervisor: AgentSupervisorService,
    worktree_manager: WorktreeManager,
    sharestore: SharedFolderStore,
    share_provisioner: ShareProvisioner,
    settings: Any,
    *,
    work_slug: str,
    agent_slug: str,
    prompt: str,
    fresh_session: bool = False,
) -> None:
    """Ensure a linked agent runtime and send its next loop instruction.

    Preconditions: the agent belongs to the Work and the prompt is non-empty.
    Postconditions: the runtime is registered and the prompt is in its transcript.
    """
    if fresh_session:
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
        fresh_session=fresh_session,
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
            fresh_session=fresh_session,
        )
        await supervisor.send_input(agent_slug, prompt)


def workspace_prompt_context(
    workstore: WorkStore,
    worktree_manager: WorktreeManager,
    *,
    work_slug: str,
    agent_slug: str,
) -> str:
    """Return compact live workspace state for one stage prompt.

    Preconditions: the stage agent belongs to the Work and its checkout was
    already provisioned. Postconditions: no files are changed; the returned
    text identifies the current revision and paths requiring inspection.
    """
    agent = next(
        (item for item in workstore.list_agents_for_work(work_slug) if item.slug == agent_slug),
        None,
    )
    if agent is None:
        return "Workspace state unavailable: stage agent was not found."
    workdir = worktree_manager.ensure(
        work_slug,
        agent.worktree_slug or agent_slug,
        agent.folder,
    )
    state = worktree_manager.describe_state(workdir)
    if state.error:
        return f"Workspace state unavailable: {state.error}"
    if not state.is_git_repo:
        return "The workspace is not a Git checkout; inspect its files directly."
    revision = f"{state.branch or '(detached HEAD)'} @ {state.head or '?'}"
    status = state.status.strip() or "clean"
    return (
        f"Git: {revision}\n"
        f"Status:\n{status}\n"
        "Inspect the complete live patch with `git diff` and include untracked files "
        "when assessing the stage."
    )


def last_transcript_seq(
    workstore: WorkStore,
    *,
    work_slug: str,
    agent_slug: str,
) -> int:
    """Return the highest persisted transcript sequence for one linked agent."""
    seqs = [
        seq
        for event in workstore.read_transcript_from_cursor(work_slug, agent_slug, 0)
        if isinstance((seq := event.get("seq")), int)
    ]
    return max(seqs, default=0)


def holds_a_session(workstore: WorkStore, *, work_slug: str, agent_slug: str) -> bool:
    """Whether resuming this agent would continue a provider session at all.

    An agent with no persisted session id is resumed with ``session_id=None``,
    which starts a new session however the caller asked. A prompt that assumes
    the transcript already holds the stage -- a bare nudge, a follow-up -- would
    then land in an empty one, so callers check before choosing that shape.
    """
    return any(
        agent.slug == agent_slug and agent.session_id
        for agent in workstore.list_agents_for_work(work_slug)
    )


async def _ensure_registered(
    workstore: WorkStore,
    supervisor: AgentSupervisorService,
    worktree_manager: WorktreeManager,
    sharestore: SharedFolderStore,
    share_provisioner: ShareProvisioner,
    settings: Any,
    *,
    work_slug: str,
    agent_slug: str,
    fresh_session: bool,
) -> None:
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
                fresh_session=fresh_session,
            ),
        )
    except resume_runtime.AgentNotFound as exc:
        raise AgentNotFound(str(exc)) from exc


__all__ = [
    "AgentNotFound",
    "last_transcript_seq",
    "release_run_agents",
    "send_loop_prompt",
    "workspace_prompt_context",
]
