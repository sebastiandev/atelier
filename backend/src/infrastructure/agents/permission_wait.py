"""Wait for the user's answer to a permission request.

A pending prompt is held open until the user answers it. Nothing here
decides on their behalf: a tool approval is a human judgement, and the
user may simply have walked away. Denying a legitimate request because
nobody was at the keyboard destroys the turn's work for no gain.

This used to expire after 30 minutes and deny, as a backstop against a
prompt that can never be answered — a closed tab, a dropped subscriber,
a dialog gated off by mistake — wedging the provider session with no
decision, no turn metrics and no trailing ``idle``. That case is already
covered where it actually resolves: when the agent is next registered,
``AgentSupervisorService._clear_stale_permission_requests`` denies every
orphaned request, so the prompt clears and the agent accepts input again.
A deadline here only added a way to lose work that was waiting on a
person.
"""

from __future__ import annotations

import asyncio
from typing import TypeVar

_Decision = TypeVar("_Decision")


async def await_permission_decision(
    fut: asyncio.Future[_Decision],
    *,
    request_id: str,
    tool_name: str,
    cancelled: _Decision,
) -> _Decision:
    """Return the user's decision, waiting as long as it takes.

    Preconditions: ``fut`` is the pending future for ``request_id``; the
    caller removes it from its pending map afterwards.
    Postconditions: returns the user's decision, or ``cancelled`` (the
    caller's own sentinel) when the turn is torn down. It never
    synthesises an answer on a live turn.
    """
    del request_id, tool_name  # retained for call-site clarity and logging hooks
    try:
        return await fut
    except asyncio.CancelledError:
        return cancelled


__all__ = ["await_permission_decision"]
