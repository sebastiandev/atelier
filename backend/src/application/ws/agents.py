"""Agent stream WebSocket — replay-from-cursor + live fan-out + input.

  - Connect with optional ``?cursor=N``; default 0 replays everything.
  - Replay disk events with ``cursor < seq <= from_seq`` (snapshot at
    subscribe time), then drain the supervisor's per-subscription queue
    for ``seq > from_seq`` — no duplicates, no gaps.
  - Concurrent receive of ``{"type":"input","text":"..."}`` text frames
    forwards to ``supervisor.send_input``, which writes a ``user_input``
    transcript line and forwards to the adapter.
  - The subscription queue is bounded; if we fall behind, the supervisor
    sets ``kicked`` and we close with code 4408 ("slow subscriber"). The
    client retries with backoff and resumes from ``?cursor=N``.
"""

import asyncio
import json
from contextlib import suppress
from typing import Any

from fastapi import APIRouter, WebSocket, WebSocketDisconnect

from src.domain.supervisor import AgentSupervisorService

router = APIRouter()

# WS close codes — see docs/backend.md → WS protocol.
_CLOSE_AGENT_NOT_RUNNING = 4404
_CLOSE_SLOW_SUBSCRIBER = 4408


@router.websocket("/agents/{agent_slug}/stream")
async def stream_agent(websocket: WebSocket, agent_slug: str) -> None:
    workstore = websocket.app.state.workstore
    supervisor = websocket.app.state.supervisor

    work_slug = supervisor.get_work_slug_for(agent_slug)
    if work_slug is None:
        # Reject before accepting — the FastAPI helper that closes pre-accept.
        await websocket.close(code=_CLOSE_AGENT_NOT_RUNNING)
        return

    cursor = _parse_cursor(websocket.query_params.get("cursor"))
    await websocket.accept()

    try:
        async with supervisor.subscribe(agent_slug) as (from_seq, sub):
            # Replay disk-side window: (cursor, from_seq].
            for event in workstore.read_transcript_from_cursor(work_slug, agent_slug, cursor):
                if event["seq"] > from_seq:
                    break
                await websocket.send_json(event)

            # Live: concurrently send queued events, receive input frames,
            # and watch for the supervisor kicking us off the slot.
            send_task = asyncio.create_task(_drain_queue(sub.queue, websocket))
            recv_task = asyncio.create_task(_receive_inputs(websocket, supervisor, agent_slug))
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
    except WebSocketDisconnect:
        pass


async def _drain_queue(queue: asyncio.Queue[dict[str, Any]], websocket: WebSocket) -> None:
    while True:
        event = await queue.get()
        await websocket.send_json(event)


async def _receive_inputs(
    websocket: WebSocket,
    supervisor: AgentSupervisorService,
    agent_slug: str,
) -> None:
    while True:
        msg = await websocket.receive_text()
        try:
            parsed = json.loads(msg)
        except json.JSONDecodeError:
            continue
        if isinstance(parsed, dict) and parsed.get("type") == "input":
            text = parsed.get("text")
            if isinstance(text, str):
                await supervisor.send_input(agent_slug, text)


def _parse_cursor(value: str | None) -> int:
    if value is None:
        return 0
    try:
        n = int(value)
    except ValueError:
        return 0
    return max(n, 0)
