"""Dispatch a parsed ``UserAction`` to the supervisor.

Symmetric inbound counterpart to ``connect`` on the streaming side: the
WS endpoint translates a wire frame to a typed action and hands it
off here. Adding a new action type is a one-class addition in
``domain/agents/user_actions.py`` plus one ``case`` branch below — the
WS handler stays unchanged.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from src.domain.agents import (
    ResolvePermission,
    SendInput,
    StopTurn,
    UserAction,
)

if TYPE_CHECKING:
    from src.domain.supervisor import AgentSupervisorService


async def execute(
    supervisor: AgentSupervisorService,
    agent_slug: str,
    action: UserAction,
) -> None:
    match action:
        case SendInput(text=text):
            await supervisor.send_input(agent_slug, text)
        case StopTurn():
            await supervisor.stop_turn(agent_slug)
        case ResolvePermission(request_id=request_id, decision=decision):
            await supervisor.resolve_permission(agent_slug, request_id, decision)


__all__ = ["execute"]
