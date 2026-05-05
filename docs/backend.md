# Backend

Python 3.11, `uv`-managed venv at `backend/.venv`, FastAPI on `127.0.0.1:8001`.

> Read [`architecture.md`](architecture.md) first for the layer + dependency rules.

## Layout

```
backend/src/
├── application/        # FastAPI: routes, ws, lifespan
│   ├── http/routes/    # works, agents, connections, providers, health
│   ├── http/schemas.py # Pydantic wire types
│   └── ws/agents.py    # /api/agents/{slug}/stream
├── domain/             # framework-free
│   ├── agents/         # configs, specs, ports, events, system_prompt
│   ├── commands/       # works/, agents/, connections/ — execute() per use case
│   ├── connections/    # ConnectionStore + service + ports
│   ├── supervisor/     # AgentSupervisorService — async, single-subscriber
│   ├── worktrees/      # WorktreeManager port
│   ├── workstore/      # WorkStore + reconcile
│   └── models.py       # plain dataclasses
└── infrastructure/     # SA mapping, filesystem, agent SDK adapters, git worktrees, keyring, HTTP verifier
```

## Provider abstraction (Spec / Config / Adapter)

Three roles, one unifying registry:

1. **Config** (`domain/agents/configs.py`) — typed runtime instance. `CommonAgentConfig` carries the cross-provider bits (workdir, system_prompt). Each provider gets a frozen dataclass that wraps `common: CommonAgentConfig` and adds its own knobs (`ClaudeAgentConfig` has `model: ClaudeModel`, `thinking_effort: ClaudeEffort`, ...). Composition over inheritance — frozen dataclasses + ABC + defaults play badly together.

2. **Spec** (`domain/agents/specs.py`) — descriptor + builder. `Spec.describe()` returns a `ProviderDescriptor` that the new-agent dialog renders into form fields. `Spec.build(common, model, options)` validates the wire-format dict into a typed `AgentConfig`. Same Spec instance powers `GET /api/providers` and `POST /api/works/<slug>/agents`, so descriptor and validator can't drift. The `SPECS` registry maps `provider name → Spec`.

3. **Adapter** (`infrastructure/agents/`) — implements `AgentAdapter` Protocol from `domain/agents/ports.py`. Selected via singledispatch on the AgentConfig type:

   ```python
   adapter = build_adapter(config, settings)  # routes to ClaudeAdapter, AmpAdapter, …
   ```

**Adding a new provider** is five local steps, none of which modify existing files:

1. Define a `<P>Model` enum and a `<P>AgentConfig` frozen dataclass with `common: CommonAgentConfig`.
2. Write a `<P>Spec` implementing `describe()` + `build()`. Register it in `SPECS`.
3. Write a `<P>Adapter` implementing `AgentAdapter`. Register a `@build_adapter.register` handler.
4. Add the literal value to `Provider` in `domain/models.py`.
5. Add `_convert` unit tests covering the SDK → AgentEvent mapping, plus a manual smoke against the live SDK. (Adapters wrapping external SDKs — Claude, Amp — are exempt from the parametrised contract suite; the `StubAgentAdapter` keeps that suite hermetic. Integration tests that need a deterministic adapter for `provider="amp"` rely on the autouse fixture in `tests/integration/conftest.py`.)

## AgentEvent union

Frozen variants in `domain/agents/events.py`: `MessageDelta`, `MessageComplete`, `ThinkingDelta`, `ThinkingComplete`, `ToolCall`, `ToolResult`, `StatusChange`, `ArtifactMarker`, `Error`, `TurnMetrics`, `SessionEstablished`, plus `UserInput` (originating from the WS input channel, not the adapter).

Each has a `Literal` `type` discriminator; the frontend pattern-matches on it.

**`ts` is set by the adapter; `seq` is set by the supervisor.** The adapter contract test asserts monotonic `ts`. The supervisor stamps the monotonic `seq` when it appends to the transcript log — so consumers can resume from `?cursor=N`.

## AgentSupervisorService

`domain/supervisor/service.py`. The supervisor is the traffic cop sitting between the browser, the agent SDK, and the on-disk transcript. There is **one `asyncio.Task` per running agent** ("the agent task"), pumping that agent's adapter event stream. The supervisor is single-subscriber: at most one WS connection per agent; a second `subscribe()` replaces the slot.

### The big picture

```
   ┌─────────────┐   WebSocket    ┌──────────────┐  AgentAdapter   ┌────────────┐
   │   Browser   │ ◄────────────► │  Supervisor  │ ◄─────────────► │ Claude/Amp │
   │ (AgentTile) │                │              │                 │    SDK     │
   └─────────────┘                └───────┬──────┘                 └────────────┘
                                          │
                                          │ append + fsync
                                          ▼
                                  ┌───────────────┐
                                  │ transcript.   │  ← canonical, on disk
                                  │   ndjson      │
                                  └───────────────┘
```

Three concurrent things happen per agent:

1. **Agent task** — iterates `adapter.events()` and publishes each event.
2. **WS subscriber (0 or 1)** — drains a bounded queue of published events.
3. **Inbound user actions** — `send_input` / `stop_turn` / `resolve_permission`, called from any number of WS handlers.

A per-agent `publish_lock` (an `asyncio.Lock`) serialises every "publish a line" operation, so these three threads of execution never interleave a partial publish.

### What "publish" means

Every line that lands in the transcript — whether it originated from the SDK, from user input, or as a synthesised line — goes through `_publish(state, payload)`. Under the lock, in this order:

1. Stamp the next per-agent monotonic `seq` (1, 2, 3, …).
2. Append to `transcript.ndjson` with fsync (`asyncio.to_thread` because NDJSON I/O is sync).
3. Hand to the (at most one) live subscriber via `queue.put_nowait`.

The ORDER is the load-bearing invariant: **no event reaches a subscriber before it's already on disk.** A crash between step 2 and step 3 leaves the event durable; the browser hasn't seen it yet but picks it up on the next reconnect via replay-from-cursor.

`send_input` and `resolve_permission` use the same `_publish` for their outbound transcript lines (`user_input`, plus any adapter-emitted `permission_*` lines), so seqs interleave one canonical conversation regardless of who originated each line.

### Lifecycle: starting an agent

`start_agent(work_slug, agent_slug, adapter, context, first_message=None)`:

1. **Seed `seq`.** Tail-read `transcript.ndjson` via `transcript_log.last_seq` and seed `state.seq` from it, so a resume continues monotonically rather than colliding with existing history.
2. **Register state.** Under `_registry_lock`, build the `_AgentState` and put it in `_states[agent_slug]`.
3. **`await adapter.start(context)`** — the adapter connects to its SDK (Claude: spawns `claude` CLI subprocess; Amp: marks itself ready, spawns lazily on first input).
4. **Optional first-message injection.** When the start command synthesised one (typically because contexts were attached), `await self.send_input(agent_slug, first_message)` runs once — the line lands as `user_input` at `seq=1` with normal publish ordering.
5. **Spawn the agent task.** `asyncio.create_task(self._run_agent(state))`. From here on, every event the SDK emits flows through `_publish`.

`_run_agent` is the consumer side: `async for event in adapter.events()`. For `SessionEstablished` events it calls `_set_session_id` (the WorkStore hook) so the SDK's session/thread ID gets persisted to SQL — that's the resume handle. Every event gets `_publish`-ed.

### Lifecycle: stopping a turn / closing an agent

- **`stop_turn(agent_slug)`** writes a `user_stop` transcript line (so the user's intent is durable even if the SDK call fails), then `await state.adapter.stop_turn()`. Claude calls `interrupt()` over the SDK control protocol; Amp no-ops today (its CLI exposes no per-turn cancel).
- **`stop_agent(agent_slug)`** pops the state from the registry, cancels the agent task, awaits it (suppressing `CancelledError`), and calls `adapter.close()`. Idempotent on the slug.
- **`shutdown()`** stops every running agent — called from the FastAPI lifespan teardown.

### Resume after a backend restart

If the WS handler can't find live state (`get_work_slug_for(slug)` returns `None`), it doesn't 4404 immediately — it tries `resume_agent(work_slug, agent_slug)`:

1. The command reads the persisted agent row + its `session_id` (the SDK's resume handle, captured earlier).
2. It builds a fresh adapter through the spec registry — same persona/role/provider/model — and passes `session_id` into the new `AgentStartContext`.
3. The Claude adapter forwards it as `resume=<id>` to the SDK; the Amp adapter forwards it as `continue_thread`. The provider's own session storage takes over from there.
4. The supervisor calls `start_agent(...)` with `first_message=None` — the SDK session already contains the original turn, so re-injecting context would double-prompt.
5. The WS subscribes; the client replays from its `?cursor=N` cursor (events that were on disk but not seen) and then drains live events.

The 4404 close code is reserved for agents that don't exist on disk at all.

### How the SDK adapters fit in

Each adapter implements the `AgentAdapter` Protocol from `domain/agents/ports.py`:

| Method | When called | What it does |
|---|---|---|
| `start(context)` | once, by `start_agent` | Connect to the SDK with the right options (model, system_prompt, allowed_tools, …). Wires per-adapter state (resume id, can_use_tool callback). |
| `events()` | once, by `_run_agent` | Async generator. Emits normalised `AgentEvent`s in the order the SDK produces them. |
| `send_input(text)` | by `send_input` (and once by `start_agent` for first-message) | Pushes a turn into the adapter's input channel. |
| `stop_turn()` | by `stop_turn` | Cancel the in-flight turn without tearing down the session. |
| `resolve_permission(rid, decision)` | by `resolve_permission` | Answer a `PermissionRequest` the adapter previously emitted. |
| `close()` | by `stop_agent` / `shutdown` | Disconnect from the SDK. Idempotent. |

Adapters whose SDK doesn't expose a feature no-op the corresponding method (Amp's `stop_turn`, `resolve_permission`; the stub's everything-but-events). The supervisor calls them uniformly so its own code doesn't branch on provider.

### Tool permissions: the `can_use_tool` callback flow

The Claude adapter is the interesting case. The Claude SDK takes a `can_use_tool: async (tool_name, tool_input, ctx) → PermissionResult` option; for every tool the model wants to use that isn't in `allowed_tools` (Atelier's default: `["Read", "Grep", "Glob"]`), the SDK awaits the callback before invoking the tool. The callback's return value (`Allow` / `Deny`) is what the SDK acts on.

Naive wiring would deadlock the supervisor: the callback runs *inside* the SDK's response iterator, so if `events()` were `async for msg in receive_response(): yield convert(msg)`, the `PermissionRequest` event the callback emits would never reach the supervisor — `events()` is blocked at the `__anext__()` waiting for the next SDK message, which won't come until the callback returns, which won't return until the user responds, which the user can't because nothing reached the WS.

The fix decouples production from consumption with an internal queue:

```
        SDK                 _can_use_tool             _outgoing             events()
   ─────────────────────────────────────────────────────────────────────────────────
                            (called inline)
                            ↓
   ToolUse →  callback
                            put: PermissionRequest  ──►          ──►  yield  →  publish
                            await future ⏳
                                                                                  ▼
   ◄──── allow (or deny) ── future.set_result(...)  ◄── resolve_permission ← WS frame
                            ↓
                            put: PermissionDecision  ──►         ──►  yield  →  publish
                            return PermissionResult
   ToolResult →             ...
```

- A side **pump task** (`_run_input_pump`) owns `async for msg in receive_response()` and forwards converted events into `_outgoing`.
- `events()` only drains `_outgoing` — never directly reads from the SDK.
- The callback `_can_use_tool` runs on the pump task, generates a `request_id`, creates a future, parks in `self._pending[rid]`, puts a `PermissionRequest` event on `_outgoing`, and `await fut`.
- The supervisor's `_run_agent` loop drains `_outgoing` via `events()`, publishes the `PermissionRequest` (so it lands in the transcript and reaches the WS).
- The user clicks Allow / Allow always / Deny → `{"type":"permission",...}` over WS → `supervisor.resolve_permission(slug, rid, decision)` → `adapter.resolve_permission(rid, decision)` → `fut.set_result(decision)`.
- The callback unparks, emits a `PermissionDecision` event for the transcript, and returns the SDK result. The SDK proceeds.

Two safety details: `stop_turn` and `close` walk every pending future and `set_result("deny")` so the SDK callback can return cleanly before `interrupt()` / `disconnect()` is called — otherwise the callback would hang forever and disconnect would block. `allow_always` is a session-only `set[str]` on the adapter — the user clicks "always allow Bash" once and the next Bash invocation skips the callback entirely without emitting any permission event.

### Slow-subscriber drop

`subscribe()` returns an `AgentSubscription { queue, kicked }`. The queue caps at `SUBSCRIBER_QUEUE_MAX` (256). If publishing overflows it, the supervisor catches `QueueFull`, sets `kicked`, and drops the subscriber slot — bounding memory growth without blocking the publish path. The WS handler watches `kicked` alongside the queue and closes with code 4408 when it fires; the client retries with backoff and resumes from `?cursor=N`. Events published after the drop still land on disk, so nothing is lost.

### Subscribe atomicity (replay vs live)

`subscribe()` snapshots `from_seq = state.seq` *under the publish lock*, then registers the queue *under the same lock*. That atomicity is the trick that makes "replay-then-live" exactly-once: any event with `seq <= from_seq` is already on disk and in the replay window; any event with `seq > from_seq` lands in the queue and only there. No overlap, no gap. See [WS protocol](#ws-protocol) for how the handler stitches the two.

## Persistence model

```
~/Atelier/
├── atelier.db                  ← SQLite (queryable cache)
└── works/
    └── <work_slug>/            ← canonical metadata
        ├── work.json
        ├── brief.md
        └── agents/
            └── <agent_slug>/
                ├── agent.json
                ├── transcript.ndjson
                ├── context.md            ← index (sections per type, links to files)
                └── context/              ← per-source files
                    ├── text-1.md
                    ├── url-1.md
                    └── jira-ENG-3421.md
```

**Filesystem is canonical.** SQLite is treated as a derived index. The `WorkStoreService` writes DB first then FS within a service-level `threading.RLock`; a crash between the two leaves an orphan DB row, and startup `reconcile` (`domain/workstore/reconcile.py`) repairs it: delete DB rows whose FS dir is gone; restore DB rows from `work.json`/`agent.json` if the FS has them but DB doesn't; FS wins on any field conflict.

`reconcile(repo, files)` runs in the FastAPI lifespan startup hook. The reconcile invariant is the AC for STORY-005 — see `tests/integration/test_workstore_e2e.py` for the round-trip guarantees.

## Contexts pipeline

A `Context` (`domain/models.py`) is `(type, value, conn_id?)`. Both `Work` and `Agent` can carry a list — but **contexts are FS-only**: they live in `work.json` / `agent.json` next to the entity, never on the SQL row. The dataclasses themselves don't have a `contexts` field (SQLAlchemy populates instances via `__new__` + setattr on mapped columns and bypasses `__init__`, so a `field(default_factory=list)` default would never fire — `dataclass.__eq__` then crashes in `reconcile`'s `db_agent != fs_agent` check). Instead contexts travel as a sibling value: `serialize_work_record(work, contexts)` and `serialize_agent(agent, contexts)`.

**At agent-create time** (`domain/commands/agents/start.py`), the `WorkStore.render_agent_contexts(work_slug, agent_slug, contexts)` port is called once. `domain/agents/context_render.py` is the pure-domain renderer:

- For `text` / `url` / `file` it writes the value directly into `context/<type>-<n>.md`.
- For `jira` / `sentry` / `honeycomb` it writes a placeholder body — `"Not yet rendered — Phase 2 will fetch via the connector"` — at `context/<type>-<value>.md` when the value parses as a slug, else numbered. Phase 2 will replace the placeholder body via `infrastructure/connections/fetchers/`; the FS shape stays the same.
- Then it builds `context.md` — sections grouped by type, one bullet per file, linked relatively (`[text-1.md](context/text-1.md)`).

The renderer returns the absolute path of `context.md`, which becomes the `first_message` injected by the supervisor on first start (see [AgentSupervisorService](#agentsupervisorservice)). Token-budget-conscious by design: the index is the only thing sent to the model; the agent decides what to actually read.

**Why filesystem, not a DB column.** Contexts are user-curated reference material, not queryable state. Putting them in SQL means a join table or a JSON column, both of which fight reconcile's "FS is canonical" invariant. The agent's `Read` tool already handles the FS — adding a SQL relation buys nothing. If we ever need to filter agents by context-type or count them in a query, that's the trigger to revisit; today nothing reads them via SQL.

## Transcript log

`infrastructure/filesystem/ndjson.py`. Append-only NDJSON, one JSON object per line, fsync after each line.

Reads are crash-safe: if the file was truncated mid-line by a crash, the reader detects the partial trailing line and the next append repairs it (truncate + append). See `tests/integration/test_transcript_log.py`.

The cursor is a `seq` integer. `read_from_cursor(work_slug, agent_slug, cursor)` yields events with `seq > cursor`.

## WS protocol

`/api/agents/{agent_slug}/stream?cursor=N`

**Server → client**: each frame is one `AgentEvent` serialized as JSON. The supervisor maintains the seq monotonicity, so the client can persist the last seq and resume from there on reconnect.

**Client → server**:
- `{"type":"input","text":"..."}` — appends a `user_input` transcript line and forwards to the adapter.
- `{"type":"stop"}` — appends a `user_stop` transcript line and calls `adapter.stop_turn()`. Claude interrupts mid-turn via the SDK's control protocol; Amp's adapter no-ops for now (the SDK exposes no per-turn cancel — full per-turn cancel for Amp is a tracked follow-up). The user-facing intent is always recorded.

Anything else is ignored.

**Replay-then-live** semantics on connect:

1. Take a snapshot `from_seq` of the supervisor's current seq for this agent.
2. Replay the disk-side window `(cursor, from_seq]`.
3. Drain the per-subscription `asyncio.Queue` for `seq > from_seq`.

This is "no duplicates, no gaps" by construction — events with `seq <= cursor` are excluded from replay; events with `seq > from_seq` arrive only via the queue.

**Close codes the frontend cares about:**

| Code | Meaning |
| --- | --- |
| 4404 | Agent slug isn't in the supervisor *and* doesn't exist on disk. Terminal — frontend surfaces "stopped" (typically a stale localStorage reference). |
| 4408 | Slow subscriber — the per-subscription queue overflowed. Frontend retries with backoff and resumes from `?cursor=N`. |
| 1000/1001/etc. | Transient (network, server restart). Frontend retries with exponential backoff. |

**Resume on reconnect.** When the slug is in SQLite but the supervisor has no live state, the handler calls `resume_agent.execute(...)` to rebuild the adapter (passing through the persisted `session_id`) and `supervisor.start_agent` to attach. A normal replay-then-live then proceeds — the client doesn't need to know whether it's connecting to a fresh adapter, an in-flight one, or a freshly-resumed one.

## WorktreeManager

`domain/worktrees/`. Provisions a per-agent workdir so each agent gets its own branch + index without stepping on the user's main checkout. Three operations: `ensure`, `remove`, `sweep_orphans`. The implementation (`infrastructure/git/worktree_manager.py`) shells out to `git worktree` — three subprocess calls beat pulling in gitpython.

**Layout** mirrors the architecture: `<workspace_root>/works/<work_slug>/worktrees/<agent_slug>/`. Branch names are `atelier/<work_slug>/<agent_slug>` so multi-agent runs don't collide and the user can spot them in `git branch`.

**Pass-through for non-git folders.** If the work's `folder` is not a git repo, `ensure` returns the folder itself instead of trying (and failing) to create a worktree. The dialog hint already tells the user "If it's a git repo, agents will spawn worktrees here automatically." — non-repo folders keep working without forcing the user to convert them.

**Removal escalates.** `git worktree remove` first; on dirty/locked, retry with `--force`; on still-failing, recursive `rmtree` plus `git worktree prune` to clean up the source repo's registry. A wedged worktree never blocks provisioning a fresh one.

**Orphan sweep on startup.** `main.py` lifespan walks every work, asks the workstore for live agent slugs, and tells the manager to remove worktree directories that don't match. This is the cleanup path for crashed runs and soft-deleted works. It satisfies the AC "deleting a Work removes them" via reconcile-style sweep rather than coupling the soft-delete command to git ops directly.

**Wired into the start_agent command** (`domain/commands/agents/start.py`). The route stays thin: parse the request, call `start.execute(...)`, await `supervisor.start_agent`. The command validates the provider config first (via `Spec.build`) so a bad model can't allocate an agent row + worktree we'd have to roll back. Two domain errors: `WorkNotFound` → 404, `InvalidProviderConfig` → 422.

## ConnectionStore

`domain/connections/`. Source-system credentials (Jira, Sentry, Honeycomb) split across two stores by design:

- **SQLite** holds the metadata row only — `id`, `slug`, `type`, `name`, `created_at`, optional `url`/`org`/`region`/`env`/`team`/`email`, plus `verified` + `last_used`. **No token, no keyring reference**: the keychain key is the slug (`atelier:con-3`), so storing the reference would just duplicate state.
- **OS keychain** (via the Python `keyring` package) holds the token under `(service="atelier", username=<slug>)`.

`ConnectionStoreService` (the public port `ConnectionStore`) composes three narrower ports — `ConnectionRepository` for the SQL row, `SecretStore` for the keychain, `ConnectionVerifier` for the source's auth endpoint — same pattern as `WorkStoreService`. The verifier is a simple type-keyed dispatch (`infrastructure/connections/verifier.py`): Jira hits `/rest/api/3/myself` with Basic auth, Sentry hits `/api/0/` with a Bearer token, Honeycomb hits `/1/auth` with `X-Honeycomb-Team`. Network errors map to `VerifyResult(verified=False, error=...)`; the verifier never raises.

**Token never crosses the API surface.** `NewConnectionRequest` and `PatchConnectionRequest` accept `token`; `ConnectionRead` (the response model) has no `token` field at all. Tests assert this on every read path. On `verify` success the supervisor materialises the token in-memory, presents it to the verifier, then discards it — the `Connection` entity never carries it.

REST endpoints (`application/http/routes/connections.py`):

```
GET    /api/connections                   -> ConnectionRead[]
POST   /api/connections                   -> ConnectionRead   (writes keychain)
GET    /api/connections/{slug}            -> ConnectionRead
PATCH  /api/connections/{slug}            -> ConnectionRead   (token rotates keychain)
DELETE /api/connections/{slug}            -> 204              (removes row + keychain entry)
POST   /api/connections/{slug}/verify     -> VerifyResponse   (persists verified + last_used)
```

Integration tests swap `app.state.connection_store` after lifespan with a `ConnectionStoreService` backed by stub secrets + stub verifier, so the suite never prompts the real OS keychain or hits real Jira/Sentry/Honeycomb. The unit tests exercise the service directly with in-memory stubs.

## Settings

`backend/src/settings.py`. Single source of env-backed config; consumed via FastAPI `Depends`. Reads from `.env.local` (gitignored). `anthropic_api_key` is currently a placeholder — see the `anthropic-auth-as-connection` follow-up in `_bmad-output/project-status.yaml` for the planned promotion to a `ConnectionStore` entry.

## Tests

```
backend/tests/
├── unit/             # pure-domain — no DB, no HTTP, stubs for Protocols
├── integration/      # FastAPI TestClient + real SQLite + real FS
└── contract/         # AgentAdapter contract suite, parametrised per provider
```

Run with `uv run pytest -q` from `backend/`.
