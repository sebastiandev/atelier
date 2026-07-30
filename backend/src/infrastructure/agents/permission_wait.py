"""Bounded wait for the user's answer to a permission request.

Every adapter used to ``await fut`` with no deadline, so one unanswered
prompt wedged the provider session permanently: no decision, no turn
metrics, no trailing ``idle``. The chat looked like it was still
thinking, forever, and the only way out was sending another message.

That is reachable whenever the prompt never renders -- a suppressed
dialog, a posture whose exit handshake nobody can answer, a browser tab
closed mid-turn -- so bound it rather than relying on the UI always
getting it right.

The deadline is deliberately generous: answering a permission prompt is
a human action, and denying a legitimate one because someone took a
lunch break is its own bug. This is a stuck-session backstop, not a
politeness timer.
"""

from __future__ import annotations

import asyncio
import logging
from typing import TypeVar

_log = logging.getLogger(__name__)

_Decision = TypeVar("_Decision")

PERMISSION_DECISION_TIMEOUT_S = 30 * 60


async def await_permission_decision(
    fut: asyncio.Future[_Decision],
    *,
    request_id: str,
    tool_name: str,
    cancelled: _Decision,
    expired: _Decision,
) -> _Decision:
    """Return the user's decision, or a synthetic one if it never arrives.

    Preconditions: ``fut`` is the pending future for ``request_id``; the
    caller removes it from its pending map afterwards.
    Postconditions: always returns a decision. ``cancelled`` is returned
    when the turn is torn down (the caller's own sentinel) and ``expired``
    when the deadline passes. Callers pass a deny for ``expired``:
    auto-allowing would run a tool the user never approved.
    """
    try:
        return await asyncio.wait_for(fut, PERMISSION_DECISION_TIMEOUT_S)
    except asyncio.CancelledError:
        return cancelled
    except TimeoutError:
        _log.warning(
            "permission request expired after %ss tool=%s rid=%s; denying",
            PERMISSION_DECISION_TIMEOUT_S,
            tool_name,
            request_id,
        )
        return expired


__all__ = ["PERMISSION_DECISION_TIMEOUT_S", "await_permission_decision"]
