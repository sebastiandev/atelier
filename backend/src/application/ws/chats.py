"""Exploratory chat stream WebSocket."""

from __future__ import annotations

import asyncio
import json
from contextlib import suppress

from fastapi import APIRouter, WebSocket, WebSocketDisconnect

from src.domain.agents import ResolvePermission, SendInput, StopTurn, parse_user_action
from src.domain.commands.chats import connect
from src.domain.supervisor import (
    AgentSubscription,
    AgentSupervisorService,
    AgentTerminated,
)

router = APIRouter()

_CLOSE_CHAT_NOT_RUNNING = 4404
_CLOSE_SLOW_SUBSCRIBER = 4408
_CLOSE_ADAPTER_TERMINATED = 4409


@router.websocket("/chats/{chat_slug}/stream")
async def stream_chat(websocket: WebSocket, chat_slug: str) -> None:
    chatstore = websocket.app.state.chatstore
    supervisor = websocket.app.state.chat_supervisor
    workstore = websocket.app.state.workstore
    projectstore = websocket.app.state.projectstore
    settings = websocket.app.state.settings

    cursor = _parse_cursor(websocket.query_params.get("cursor"))

    try:
        async with connect.execute(
            chatstore,
            supervisor,
            workstore,
            projectstore,
            settings,
            connect.ConnectChatRequest(chat_slug=chat_slug, cursor=cursor),
        ) as sub:
            await websocket.accept()
            send_task = asyncio.create_task(_drain(sub, websocket))
            recv_task = asyncio.create_task(
                _receive_inputs(websocket, supervisor, chat_slug)
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
    except connect.ChatNotFound:
        await _safe_close(websocket, code=_CLOSE_CHAT_NOT_RUNNING)
    except WebSocketDisconnect:
        pass


async def _drain(sub: AgentSubscription, websocket: WebSocket) -> None:
    async for event in sub.stream():
        await websocket.send_json(event)


async def _receive_inputs(
    websocket: WebSocket,
    supervisor: AgentSupervisorService,
    chat_slug: str,
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
            match action:
                case SendInput(text=text, contexts=contexts):
                    if contexts:
                        await websocket.send_json(
                            {
                                "type": "client_error",
                                "message": (
                                    "Chat context attachments are not supported yet."
                                ),
                            }
                        )
                    else:
                        await supervisor.send_input(chat_slug, text)
                case StopTurn():
                    await supervisor.stop_turn(chat_slug)
                case ResolvePermission(request_id=request_id, decision=decision):
                    await supervisor.resolve_permission(
                        chat_slug, request_id, decision
                    )
        except AgentTerminated:
            with suppress(Exception):
                await websocket.send_json(
                    {
                        "type": "client_error",
                        "message": (
                            "Chat's underlying process ended. Reconnecting..."
                        ),
                    }
                )
            await _safe_close(websocket, code=_CLOSE_ADAPTER_TERMINATED)
            return


def _is_send_after_close(exc: BaseException) -> bool:
    return (
        isinstance(exc, RuntimeError)
        and 'Cannot call "send" once a close message has been sent.' in str(exc)
    )


async def _safe_close(websocket: WebSocket, *, code: int) -> None:
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
