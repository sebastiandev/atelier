"""Detach an agent from Atelier's UI and hand it to a terminal CLI.

Stops the supervisor's SDK process for this agent (so two processes don't
simultaneously drive the same provider session), flips the agent's status
to ``detached``, writes a transcript marker so the user sees the boundary
in their timeline, then shells out to the user's terminal with the
matching ``claude --resume`` / ``amp threads continue`` invocation.

The shell-out itself is best-effort — if no terminal can be launched we
still flip the status and write the marker, and return the raw command
string so the FE can copy it to the user's clipboard. The reverse action
("re-attach" by clicking the agent's rail entry) lives in the WS resume
path; it runs the catch-up merge from the SDK file before the supervisor
takes back over.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
from typing import TYPE_CHECKING

from src.domain.models import Agent, AgentStatus
from src.domain.sharedfolders.ports import SharedFolderStore, ShareProvisioner
from src.domain.workstore.ports import WorkStore
from src.domain.worktrees import WorktreeManager
from src.infrastructure.cli_launcher import build_resume_command, launch_in_terminal
from src.infrastructure.cli_transcript import sdk_cursor_at_detach

if TYPE_CHECKING:
    # ``AgentSupervisorService`` lives in ``src.domain.supervisor`` whose
    # transitive imports (via ``src.domain.agents``) cycle through
    # ``src.domain.workstore.ports`` and would deadlock module init if
    # we imported it eagerly here. The route hands us a real instance at
    # call time; the annotation only needs the symbol for type checkers.
    from src.domain.supervisor import AgentSupervisorService


@dataclass(frozen=True)
class DetachAgentRequest:
    agent_slug: str
    terminal: str = "system"
    """The terminal app to launch (matches ``TerminalChoice`` on the FE:
    ``system`` / ``iterm2`` / ``gnome-terminal`` / ``konsole`` /
    ``terminator`` / ``tmux``). Unknown values fall back to the system
    terminal in the launcher."""


@dataclass(frozen=True)
class DetachAgentResult:
    command: str
    """The shell command that would resume the CLI session.

    Populated whether or not we managed to launch a terminal — the FE
    uses it as a clipboard fallback when ``launched`` is False, and as
    the body of a "command launched" toast when it's True.
    """

    launched: bool
    """True if we successfully spawned a terminal window."""


class AgentNotFound(ValueError):
    """The agent slug doesn't resolve to a stored agent."""


class AgentNotResumable(ValueError):
    """The agent has no provider ``session_id`` yet — typically because
    its first turn never completed (the SDK hasn't surfaced an ID).
    Detaching without a session_id would just start a fresh CLI
    conversation, defeating the point."""


async def execute(
    workstore: WorkStore,
    supervisor: AgentSupervisorService,
    worktree_manager: WorktreeManager,
    req: DetachAgentRequest,
    sharestore: SharedFolderStore | None = None,
    share_provisioner: ShareProvisioner | None = None,
) -> DetachAgentResult:
    work_slug = workstore.get_work_slug_for_agent(req.agent_slug)
    if work_slug is None:
        raise AgentNotFound(f"agent not found: {req.agent_slug}")
    agent = _find_agent(workstore, work_slug, req.agent_slug)
    if agent is None:
        raise AgentNotFound(f"agent not found: {req.agent_slug}")
    if agent.session_id is None:
        raise AgentNotResumable(
            f"agent {req.agent_slug} has no provider session yet — "
            "send at least one message first"
        )

    # Resolve the actual cwd the user should land in. For git repos this
    # is the per-agent worktree (where the supervisor's SDK process was
    # running and where the agent's branch lives), not the source folder.
    workdir = worktree_manager.ensure(
        work_slug=work_slug,
        agent_slug=req.agent_slug,
        source=agent.folder,
    )
    # Stop the supervisor's SDK process before launching CLI. ``stop_agent``
    # is idempotent — if the agent isn't currently running (e.g. user
    # already closed-to-rail) it's a no-op. We deliberately stop before
    # writing the marker so the supervisor's seq counter doesn't race
    # the marker we're about to append.
    await supervisor.stop_agent(req.agent_slug)

    from src.domain.commands.agents.start import (
        MountedProjectShares,
        _agent_writable_roots,
        _mount_project_shares,
    )

    mounted_shares = MountedProjectShares()
    if sharestore is not None and share_provisioner is not None:
        record = workstore.get_work(work_slug)
        project_slug = record.work.project_slug if record is not None else None

        # Keep detach-to-CLI sandbox roots in sync with in-app Codex runs.
        # The helper is idempotent, so it also repairs missing share symlinks
        # before the CLI opens.
        mounted_shares = _mount_project_shares(
            sharestore=sharestore,
            provisioner=share_provisioner,
            project_slug=project_slug,
            work_slug=work_slug,
            agent_slug=req.agent_slug,
        )
    additional_directories = _agent_writable_roots(
        mounted_shares, worktree_manager, workdir
    )

    # Flip status + write a marker. The marker carries an ``sdk_cursor``
    # snapshot (provider-specific shape) that the catch-up merge later
    # reads to know where the SDK file's "new" entries start.
    cursor = sdk_cursor_at_detach(agent.provider, agent.session_id, workdir)
    workstore.set_agent_status(req.agent_slug, AgentStatus.DETACHED)
    workstore.append_transcript_event_with_seq(
        work_slug,
        req.agent_slug,
        {
            "type": "user_detached",
            "ts": datetime.now(UTC).isoformat(),
            "sdk_cursor": cursor,
        },
    )

    # Forward the agent's primary selector (Claude model id / Amp mode)
    # and persisted options so the CLI session resumes with the same
    # ``--model`` / ``--mode`` / ``--permission-mode`` / ``--effort``
    # the user picked in Atelier — otherwise the CLI silently falls
    # back to its local default. Both arguments are no-ops for legacy
    # agents whose ``options`` column is NULL.
    command = build_resume_command(
        agent.provider,
        agent.session_id,
        workdir,
        model=agent.model,
        options=agent.options,
        additional_directories=additional_directories,
    )
    result = launch_in_terminal(command, kind=req.terminal)
    return DetachAgentResult(command=result.command, launched=result.launched)


def _find_agent(workstore: WorkStore, work_slug: str, agent_slug: str) -> Agent | None:
    return next(
        (a for a in workstore.list_agents_for_work(work_slug) if a.slug == agent_slug),
        None,
    )


__all__ = [
    "AgentNotFound",
    "AgentNotResumable",
    "DetachAgentRequest",
    "DetachAgentResult",
    "execute",
]
