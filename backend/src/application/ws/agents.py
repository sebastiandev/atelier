"""Agent stream WebSocket — thin adapter on top of ``connect.execute``.

The endpoint's only job is to translate WS frames to/from the supervisor:

  - ``GET /agents/{slug}/stream?cursor=N`` opens a ``connect.execute``
    context. Truly unknown slugs close with 4404.
  - The Subscription's ``stream()`` already yields disk-replay then live
    events with ``seq > cursor``, exactly once, in order — so this
    handler just forwards.
  - Inbound frames (``input`` / ``stop`` / ``permission``) call the
    supervisor directly.
  - On slow-subscriber overflow, the supervisor sets
    ``subscription.kicked``; we close with 4408. The client retries
    with backoff and resumes from ``?cursor=N``.
"""

import asyncio
import json
from contextlib import suppress

from fastapi import APIRouter, WebSocket, WebSocketDisconnect

from src.domain.agents import parse_user_action
from src.domain.commands.agents import connect, handle_user_action
from src.domain.supervisor import AgentSubscription, AgentSupervisorService

router = APIRouter()

# WS close codes — see docs/backend.md → WS protocol.
_CLOSE_AGENT_NOT_RUNNING = 4404
_CLOSE_SLOW_SUBSCRIBER = 4408


@router.websocket("/agents/{agent_slug}/stream")
async def stream_agent(websocket: WebSocket, agent_slug: str) -> None:
    workstore = websocket.app.state.workstore
    supervisor = websocket.app.state.supervisor
    worktree_manager = websocket.app.state.worktree_manager
    settings = websocket.app.state.settings

    cursor = _parse_cursor(websocket.query_params.get("cursor"))

    try:
        async with connect.execute(
            workstore,
            supervisor,
            worktree_manager,
            settings,
            connect.ConnectRequest(agent_slug=agent_slug, cursor=cursor),
        ) as sub:
            await websocket.accept()
            send_task = asyncio.create_task(_drain(sub, websocket))
            recv_task = asyncio.create_task(
                _receive_inputs(websocket, supervisor, agent_slug)
            )
            kick_task = asyncio.create_task(sub.kicked.wait())
            try:
                done, _pending = await asyncio.wait(
                    {send_task, recv_task, kick_task},
                    return_when=asyncio.FIRST_COMPLETED,
                )
                if kick_task in done:
                    await websocket.close(code=_CLOSE_SLOW_SUBSCRIBER)
                    return
                # Surface any task exception (other than disconnect/cancel).
                for task in done:
                    exc = task.exception()
                    if exc is not None and not isinstance(exc, WebSocketDisconnect):
                        raise exc
            finally:
                for task in (send_task, recv_task, kick_task):
                    task.cancel()
                    with suppress(asyncio.CancelledError, WebSocketDisconnect):
                        await task
    except connect.AgentNotFound:
        await websocket.close(code=_CLOSE_AGENT_NOT_RUNNING)
    except WebSocketDisconnect:
        pass


async def _drain(sub: AgentSubscription, websocket: WebSocket) -> None:
    async for event in sub.stream():
        await websocket.send_json(event)


async def _receive_inputs(
    websocket: WebSocket,
    supervisor: AgentSupervisorService,
    agent_slug: str,
) -> None:
    while True:
        msg = await websocket.receive_text()
        try:
            data = json.loads(msg)
        except json.JSONDecodeError:
            continue
        if not isinstance(data, dict):
            continue
        action = parse_user_action(data)
        if action is not None:
            await handle_user_action.execute(supervisor, agent_slug, action)


def _parse_cursor(value: str | None) -> int:
    if value is None:
        return 0
    try:
        n = int(value)
    except ValueError:
        return 0
    return max(n, 0)
