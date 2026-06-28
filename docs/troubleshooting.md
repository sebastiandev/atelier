# Troubleshooting stuck streams

Use this when an agent or chat shows `thinking`, `reconnecting`, or accepts input without producing a reply. The goal is to prove which layer is stale before changing code.

## First checks

1. Confirm the exact runtime id and storage path.
   - Agent: `~/Atelier/works/<WRK>/agents/<agt>/transcript.ndjson`
   - Chat: `~/Atelier/chats/<CHT>/transcript.ndjson`
   - Agent metadata: `~/Atelier/works/<WRK>/agents/<agt>/agent.json`

2. Check whether the transcript is moving.
   - `stat -f '%Sm %z %N' <transcript.ndjson>`
   - `tail -n 80 <transcript.ndjson>`
   - Repeat after sending input. If `mtime` and tail do not change, the frontend is not hiding new output; the backend/provider path is not appending durable events.

3. Check the persisted runtime state.
   - `curl -sS http://127.0.0.1:8001/api/works/<WRK>/agents`
   - `sqlite3 ~/Atelier/atelier.db "select slug,provider,status,session_id,folder from agents where slug='<agt>'"`
   - `agent.json` can be stale for runtime fields; SQLite and `session_established` transcript events are the source of truth for the active provider session id.

4. Check live provider/backend processes.
   - `ps -axo pid,command | rg -i 'uvicorn|atelier|codex-acp|claude|opencode|npm exec'`
   - A live provider subprocess plus an unchanged transcript usually means a stuck registered backend state or provider transport, not a pure frontend render bug.

5. Check the websocket recovery path.
   - Agent WS route: `backend/src/application/ws/agents.py`
   - Chat WS route: `backend/src/application/ws/chats.py`
   - Close `4409` means the frontend should reconnect and land in `connect.execute` / `resume.execute` only after the supervisor has no live state for that slug.

## Hypotheses

| Hypothesis | Evidence that supports it | What to check before fixing |
| --- | --- | --- |
| Frontend stale replay state | Last transcript event is old but active-looking, UI says `thinking`, no new transcript rows appear. | `frontend/src/AgentTile.tsx:isAgentActive`, `frontend/src/Chat.tsx`, and `frontend/src/useAgentStream.ts`. Confirm the UI is deriving activity from stale replay rather than live socket state. |
| Frontend reconnect cursor bug | Transcript has newer rows, but the tile does not show them after reconnect. | `useAgentStream` `lastSeqRef`, local advisory frames such as `client_error`, and whether frames without server `seq` are advancing the replay cursor. |
| Backend state is registered but stale | API reports the agent exists, transcript is unchanged after input, reconnecting browser sockets attach to the same state, and provider subprocesses remain alive. | `AgentSupervisorService.is_registered`, `_run_agent`, `_evict_after_pump_end`, `stop_agent`, and whether inbound `AgentTerminated` closes the WS with `4409`. A fix in ACP restore will not help if `connect.execute` never reaches resume. |
| Adapter accepted a prompt into a dead transport | Transcript shows repeated `failed to deliver user input: ConnectionError('Connection closed')` or terminal adapter errors. | `AgentSupervisorService.send_input` should only stamp `user_input` after adapter acceptance and should evict failed starts/sends. For terminal `ConnectionError`, avoid adding duplicate durable user turns. |
| Restored ACP provider session is poisoned | The adapter can `session/load` or `session/resume`, but prompt delivery closes repeatedly on the restored session. | `AcpAdapter._recover_connection` and `_start_fresh_session_after_restore`. Confirm a new `session_established` row appears after fallback; otherwise future reconnects will reuse the poisoned id. |
| Provider process is alive but not producing events | `codex-acp`/provider subprocess exists, transcript is unchanged, and no WS close occurs. | Whether the adapter pump is waiting forever inside `prompt`, whether there is a timeout/heartbeat gap, and whether `stop_agent` can close the transport/process group. |
| ACP stdio line exceeds asyncio's default reader limit | Backend logs show `LimitOverrunError` or `ValueError: Separator is not found, and chunk exceed the limit` from `acp.connection._receive_loop`; transcript does not move and the UI just reconnects. | `AcpAdapter._connect` must pass a large `limit` to `asyncio.create_subprocess_exec`. ACP JSON-RPC frames can be much larger than the default 64KB line buffer, especially after replay or multimodal context. |

## Backend checklist

- Read the transcript tail first. Durable events are the arbiter: if there are no new rows, do not start by debugging React rendering.
- Compare `agent.json`, SQLite, and the latest `session_established` transcript row. Runtime fields may be mirrored, but SQLite is what resume uses.
- Determine whether the supervisor has live state. `connect.execute` only calls `resume.execute` when `supervisor.is_registered(slug)` is false. A stale registered state can make every browser reconnect attach to the same bad adapter.
- Check whether the provider process tree is still running. ACP providers often involve `npm exec`, `node`, the provider binary, and the Atelier MCP server.
- Check backend logs for `LimitOverrunError` / `chunk exceed the limit`. That points to the subprocess stdout reader limit, not session restore or frontend state.
- Check whether the WS route sends `client_error` and closes with `4409` on `AgentTerminated`. If it only sends an advisory frame without closing, the frontend may never enter the rebuild path.
- If fixing provider restore, verify the transcript gains a new `session_established` row and SQLite `agents.session_id` changes. Without that, the fix works once at most.

## Frontend checklist

- Confirm whether the UI has newer events than disk. If disk is unchanged, the frontend can only be showing replayed or optimistic state.
- Inspect `useAgentStream` status transitions: `connecting`, `connected`, `reconnecting`, `stopped`.
- Verify non-transcript advisory frames (`client_error`) do not advance the server replay cursor. They need local seqs for React rendering but must not change `lastSeqRef`.
- Check `isAgentActive(events)`. Active-looking replay tails older than the stale threshold should not keep the composer in working mode.
- For chat surfaces, activity should be gated by both stream connection state and event activity. A disconnected stream with an old `message_delta` tail should not look live.

## Known lesson from `agt-22`

The first plausible fix was ACP fresh-session fallback after repeated `Connection closed` errors. That was necessary, but it was not enough to explain the observed unresponsive UI when the transcript stopped at an old seq and no fresh rows appeared after sending input.

Before diving into adapter restore logic, prove whether the send path is reaching the adapter and whether the supervisor is being evicted. If the backend still has a registered stale state or a live provider subprocess that never emits/terminates, reconnecting the frontend will not hit `resume.execute`, so a resume/fallback fix will not run.
