"""Agent stream WebSocket — replay-from-cursor + live fan-out + input.

  - Connect with optional ``?cursor=N``; default 0 replays everything.
  - If the supervisor has no live state for the slug, the handler resumes
    the provider session via the ``resume_agent`` command and attaches a
    fresh subscription. Truly unknown slugs still 4404.
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
from datetime import UTC, datetime
from typing import Any

from fastapi import APIRouter, WebSocket, WebSocketDisconnect

from src.domain.commands.agents import resume_plan
from src.domain.models import AgentStatus
from src.domain.supervisor import AgentSupervisorService
from src.infrastructure.cli_transcript import merge_cli_transcript

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

    work_slug = supervisor.get_work_slug_for(agent_slug)
    if work_slug is None:
        # Supervisor has no live state — backend restart, the agent was
        # closed-to-rail, or it was detached to CLI. Resume the provider
        # session from the row if the agent exists; truly unknown slugs
        # still 4404. If detached, run the SDK-transcript catch-up merge
        # first so the user's NDJSON includes anything the CLI typed
        # before the supervisor takes back over.
        history_work_slug = workstore.get_work_slug_for_agent(agent_slug)
        if history_work_slug is None:
            await websocket.close(code=_CLOSE_AGENT_NOT_RUNNING)
            return
        try:
            plan = resume_plan.execute(
                workstore,
                worktree_manager,
                settings,
                resume_plan.ResumeAgentRequest(
                    work_slug=history_work_slug, agent_slug=agent_slug
                ),
            )
        except resume_plan.AgentNotFound:
            await websocket.close(code=_CLOSE_AGENT_NOT_RUNNING)
            return
        if plan.agent.status == AgentStatus.DETACHED:
            await asyncio.to_thread(
                _catch_up_detached_agent,
                workstore,
                history_work_slug,
                agent_slug,
                plan,
            )
        try:
            await supervisor.start_agent(
                history_work_slug, agent_slug, plan.adapter, plan.context
            )
        except RuntimeError:
            # A concurrent WS connection (React StrictMode double-mount,
            # rapid clicks, or just two tabs) saw the same "no live state"
            # snapshot and won the race to start_agent. The supervisor
            # owns the agent now — close our adapter copy to avoid leaking
            # an SDK process and proceed to subscribe.
            with suppress(Exception):
                await plan.adapter.close()
            if supervisor.get_work_slug_for(agent_slug) is None:
                # Still no live state — the failure was something other
                # than the race (truly unrecoverable). Close the WS.
                await websocket.close(code=_CLOSE_AGENT_NOT_RUNNING)
                return
        work_slug = history_work_slug

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
        if not isinstance(parsed, dict):
            continue
        kind = parsed.get("type")
        if kind == "input":
            text = parsed.get("text")
            if isinstance(text, str):
                await supervisor.send_input(agent_slug, text)
        elif kind == "stop":
            await supervisor.stop_turn(agent_slug)
        elif kind == "permission":
            request_id = parsed.get("request_id")
            decision = parsed.get("decision")
            if (
                isinstance(request_id, str)
                and decision in ("allow", "allow_always", "deny")
            ):
                await supervisor.resolve_permission(agent_slug, request_id, decision)


def _catch_up_detached_agent(
    workstore: Any,
    work_slug: str,
    agent_slug: str,
    plan: resume_plan.ResumeAgentPlan,
) -> None:
    """Read the SDK's transcript file, append any events we don't have
    yet, then flip the agent's status back to ``idle`` so the supervisor
    starts it normally. Sync because ``WorkStoreService`` and the SDK
    file are both blocking; the caller bridges to the loop via
    ``asyncio.to_thread``.

    The cursor lives on the most recent ``user_detached`` event in our
    NDJSON. If we can't find it (older detach without a cursor, file
    corruption), we pass ``None`` and the merge starts from "now" — i.e.
    no events appended, which is the safe degenerate behaviour."""
    if plan.agent.session_id is None:
        workstore.set_agent_status(agent_slug, AgentStatus.IDLE)
        return

    cursor = _last_detach_cursor(workstore, work_slug, agent_slug)
    new_events = merge_cli_transcript(
        plan.agent.provider,
        plan.agent.session_id,
        plan.context.workdir,
        cursor,
    )
    for event in new_events:
        workstore.append_transcript_event_with_seq(work_slug, agent_slug, event)
    workstore.append_transcript_event_with_seq(
        work_slug,
        agent_slug,
        {
            "type": "user_reattached",
            "ts": datetime.now(UTC).isoformat(),
            "events_merged": len(new_events),
        },
    )
    workstore.set_agent_status(agent_slug, AgentStatus.IDLE)


def _last_detach_cursor(
    workstore: Any, work_slug: str, agent_slug: str
) -> dict[str, Any] | None:
    """Walk our NDJSON to find the most recent ``user_detached`` marker.
    Returns the marker's ``sdk_cursor`` payload, or None if not found."""
    cursor: dict[str, Any] | None = None
    for event in workstore.read_transcript_from_cursor(work_slug, agent_slug, 0):
        if event.get("type") == "user_detached":
            payload = event.get("sdk_cursor")
            if isinstance(payload, dict):
                cursor = payload
    return cursor


def _parse_cursor(value: str | None) -> int:
    if value is None:
        return 0
    try:
        n = int(value)
    except ValueError:
        return 0
    return max(n, 0)
