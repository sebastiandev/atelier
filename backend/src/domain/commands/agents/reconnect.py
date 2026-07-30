"""Restart an agent's provider runtime without resending input."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
from typing import TYPE_CHECKING, Any

from src.domain.models import AgentStatus
from src.domain.workstore.ports import WorkStore

if TYPE_CHECKING:
    from src.domain.supervisor import AgentSupervisorService


@dataclass(frozen=True)
class ReconnectAgentRequest:
    """Identify the agent runtime to restart."""

    agent_slug: str


class AgentNotFound(ValueError):
    """The agent slug does not resolve to a stored agent."""


async def execute(
    workstore: WorkStore,
    supervisor: AgentSupervisorService,
    req: ReconnectAgentRequest,
) -> None:
    """Stop a stalled runtime and leave its transcript ready to resume.

    Preconditions: ``req.agent_slug`` identifies a stored agent.
    Postconditions: the provider runtime is stopped and an interrupted active
    turn is visibly closed without resending its input.
    """
    work_slug = workstore.get_work_slug_for_agent(req.agent_slug)
    if work_slug is None:
        raise AgentNotFound(f"agent not found: {req.agent_slug}")

    events = list(workstore.read_transcript_from_cursor(work_slug, req.agent_slug, 0))
    if _turn_is_open(events):
        now = datetime.now(UTC).isoformat()
        for event in (
            {
                "type": "error",
                "ts": now,
                "message": (
                    "The previous turn was interrupted when its runtime disconnected. "
                    "Send Continue to resume."
                ),
            },
            {"type": "status_change", "ts": now, "status": "idle"},
        ):
            if not await supervisor.publish_external_event(req.agent_slug, event):
                workstore.append_transcript_event_with_seq(
                    work_slug, req.agent_slug, event
                )
        workstore.set_agent_status(req.agent_slug, AgentStatus.IDLE)

    await supervisor.stop_agent(req.agent_slug)


def _turn_is_open(events: list[dict[str, Any]]) -> bool:
    """Return whether the transcript tail represents an active turn."""
    for event in reversed(events):
        event_type = event.get("type")
        if event_type in {
            "message_complete",
            "turn_metrics",
            "error",
            "user_stop",
            "permission_request",
        }:
            return False
        if event_type == "status_change":
            return event.get("status") in {"thinking", "live"}
        if event_type in {
            "user_input",
            "message_delta",
            "thinking_delta",
            "thinking_complete",
            "tool_call",
            "tool_result",
        }:
            return True
    return False


__all__ = ["AgentNotFound", "ReconnectAgentRequest", "execute"]
