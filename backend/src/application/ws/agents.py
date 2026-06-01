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
from src.domain.commands.agents import add_contexts, connect, handle_user_action
from src.domain.connections import ConnectionStore, ContextFetchError
from src.domain.supervisor import (
    AgentSubscription,
    AgentSupervisorService,
    AgentTerminated,
)
from src.domain.workstore.ports import WorkStore

router = APIRouter()

# WS close codes — see docs/backend.md → WS protocol.
_CLOSE_AGENT_NOT_RUNNING = 4404
_CLOSE_SLOW_SUBSCRIBER = 4408
# Supervisor's event pump ended (subprocess died, upstream 429, EOF)
# and we caught ``AgentTerminated`` while routing a user input. The
# FE retries with backoff which lands in the resume path and rebuilds
# the adapter; semantically identical to 4408's reconnect.
_CLOSE_ADAPTER_TERMINATED = 4409


@router.websocket("/agents/{agent_slug}/stream")
async def stream_agent(websocket: WebSocket, agent_slug: str) -> None:
    workstore = websocket.app.state.workstore
    supervisor = websocket.app.state.supervisor
    worktree_manager = websocket.app.state.worktree_manager
    connection_store = websocket.app.state.connection_store
    sharestore = websocket.app.state.sharestore
    share_provisioner = websocket.app.state.share_provisioner
    settings = websocket.app.state.settings

    cursor = _parse_cursor(websocket.query_params.get("cursor"))

    try:
        async with connect.execute(
            workstore,
            supervisor,
            worktree_manager,
            sharestore,
            share_provisioner,
            settings,
            connect.ConnectRequest(agent_slug=agent_slug, cursor=cursor),
        ) as sub:
            await websocket.accept()
            send_task = asyncio.create_task(_drain(sub, websocket))
            recv_task = asyncio.create_task(
                _receive_inputs(
                    websocket, supervisor, workstore, connection_store, agent_slug
                )
            )
            kick_task = asyncio.create_task(sub.kicked.wait())
            try:
                done, _pending = await asyncio.wait(
                    {send_task, recv_task, kick_task},
                    return_when=asyncio.FIRST_COMPLETED,
                )
                if kick_task in done:
                    await _safe_close(websocket, code=_CLOSE_SLOW_SUBSCRIBER)
                    return
                # Surface any task exception (other than disconnect/cancel).
                for task in done:
                    exc = task.exception()
                    if exc is None or isinstance(exc, WebSocketDisconnect):
                        continue
                    if _is_send_after_close(exc):
                        return
                    raise exc
            finally:
                for task in (send_task, recv_task, kick_task):
                    task.cancel()
                    with suppress(
                        asyncio.CancelledError, WebSocketDisconnect, RuntimeError
                    ):
                        await task
    except connect.AgentNotFound:
        await _safe_close(websocket, code=_CLOSE_AGENT_NOT_RUNNING)
    except WebSocketDisconnect:
        pass


async def _drain(sub: AgentSubscription, websocket: WebSocket) -> None:
    async for event in sub.stream():
        await websocket.send_json(event)


def _is_send_after_close(exc: BaseException) -> bool:
    return (
        isinstance(exc, RuntimeError)
        and 'Cannot call "send" once a close message has been sent.' in str(exc)
    )


async def _receive_inputs(
    websocket: WebSocket,
    supervisor: AgentSupervisorService,
    workstore: WorkStore,
    connection_store: ConnectionStore,
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
        if action is None:
            continue
        try:
            await handle_user_action.execute(
                supervisor, workstore, connection_store, agent_slug, action
            )
        except (ContextFetchError, add_contexts.AgentNotFound) as exc:
            # Add-context failed (bad credentials, network, missing
            # connection). The user's message was NOT delivered to the
            # SDK. Surface the failure to the client so it can show a
            # toast — the WS frame is purely advisory and not stamped
            # into the transcript ledger.
            with suppress(Exception):
                await websocket.send_json(
                    {"type": "client_error", "message": f"Add context failed: {exc}"}
                )
        except AgentTerminated:
            # The pump exited (upstream rate limit, provider EOF,
            # subprocess crash). Tell the FE briefly, then close the
            # socket so its reconnect-with-backoff lands in resume
            # and rebuilds the adapter.
            with suppress(Exception):
                await websocket.send_json(
                    {
                        "type": "client_error",
                        "message": (
                            "Agent's underlying process ended. "
                            "Reconnecting…"
                        ),
                    }
                )
            await _safe_close(websocket, code=_CLOSE_ADAPTER_TERMINATED)
            return


async def _safe_close(websocket: WebSocket, *, code: int) -> None:
    """Close the socket if Starlette/Uvicorn has not already completed it.

    Expected reconnect paths can race with the browser or proxy closing the
    socket first. A second ASGI close raises ``RuntimeError``; suppress it so
    benign reconnects do not produce scary backend tracebacks.
    """

    with suppress(RuntimeError, WebSocketDisconnect):
        await websocket.close(code=code)


def _parse_cursor(value: str | None) -> int:
    if value is None:
        return 0
    try:
        n = int(value)
    except ValueError:
        return 0
    return max(n, 0)
