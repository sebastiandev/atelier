"""Switch an agent's underlying provider thread without losing the agent.

Used for Amp's auto-handoff: when the CLI exhausts a thread's context,
it creates a new thread and emits an assistant message saying "work
continues in T-XXX". The SDK stream typically ends with that message,
leaving the agent stuck on the closed thread. This command rebuilds
the adapter against the new thread so the same Atelier agent (slug,
workdir, persona, role, provider options) keeps going.

Flow:
  1. Stop the supervisor's SDK process for the agent — symmetric with
     ``detach``. Idempotent if the agent isn't currently registered.
  2. Persist ``session_id = new_thread_id`` so the rebuilt adapter resumes
     against the new thread on its next start.
  3. Flip status to IDLE — the agent was likely stuck on ``thinking``
     when the old SDK stream ended, and we want the UI to show it ready
     for input.
  4. Append a ``handoff_accepted`` marker to the transcript so the FE
     can clear the pending-handoff pill (and so the boundary is visible
     in the timeline).
  5. Re-register via ``resume.execute`` with ``lazy=True``. The actual
     CLI subprocess spawns on the next user input, when the supervisor
     calls ``send_input`` on the new adapter — same shape as a fresh
     resume after page reload.

Only Amp emits ``HandoffOffered`` today, but this command stays
provider-agnostic: any future provider with a similar auto-handoff
concept can reuse it as long as ``session_id`` is the right key to
swap.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import TYPE_CHECKING

from src.domain.commands.agents import resume
from src.domain.models import AgentStatus
from src.domain.sharedfolders.ports import SharedFolderStore, ShareProvisioner
from src.domain.workstore.ports import WorkStore
from src.domain.worktrees import WorktreeManager
from src.settings import Settings

if TYPE_CHECKING:
    from src.domain.supervisor import AgentSupervisorService


# Amp thread ids are UUID-shaped with a ``T-`` prefix. We accept that
# exact form to keep accidental garbage out of the session_id column.
_THREAD_ID_PATTERN = re.compile(
    r"^T-[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}"
    r"-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}$"
)


@dataclass(frozen=True)
class SwitchThreadRequest:
    agent_slug: str
    new_thread_id: str


class AgentNotFound(ValueError):
    pass


class InvalidThreadId(ValueError):
    pass


async def execute(
    workstore: WorkStore,
    supervisor: AgentSupervisorService,
    worktree_manager: WorktreeManager,
    sharestore: SharedFolderStore,
    share_provisioner: ShareProvisioner,
    settings: Settings,
    req: SwitchThreadRequest,
) -> None:
    if not _THREAD_ID_PATTERN.match(req.new_thread_id):
        raise InvalidThreadId(
            f"thread id doesn't match expected shape: {req.new_thread_id!r}"
        )

    work_slug = workstore.get_work_slug_for_agent(req.agent_slug)
    if work_slug is None:
        raise AgentNotFound(f"agent not found: {req.agent_slug}")

    # Stop first so the old SDK process can't race the session_id update
    # (any in-flight SessionEstablished it might emit would otherwise
    # overwrite the new id back to the old one).
    await supervisor.stop_agent(req.agent_slug)

    workstore.set_agent_session_id(
        req.agent_slug, req.new_thread_id, mirror_agent_json=True
    )
    workstore.set_agent_status(req.agent_slug, AgentStatus.IDLE)
    workstore.append_transcript_event_with_seq(
        work_slug,
        req.agent_slug,
        {
            "type": "handoff_accepted",
            "ts": datetime.now(UTC).isoformat(),
            "new_thread_id": req.new_thread_id,
        },
    )

    # Re-register via the resume path so the adapter is rebuilt with
    # the row's now-updated session_id. ``lazy=True`` (resume's default)
    # means the CLI subprocess only spawns on the next user input.
    await resume.execute(
        workstore,
        supervisor,
        worktree_manager,
        sharestore,
        share_provisioner,
        settings,
        resume.ResumeAgentRequest(
            work_slug=work_slug,
            agent_slug=req.agent_slug,
        ),
    )


__all__ = [
    "AgentNotFound",
    "InvalidThreadId",
    "SwitchThreadRequest",
    "execute",
]
