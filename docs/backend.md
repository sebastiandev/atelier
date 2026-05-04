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

1. **Config** (`domain/agents/configs.py`) — typed runtime instance. `CommonAgentConfig` carries the cross-provider bits (workdir, system_prompt, context_md). Each provider gets a frozen dataclass that wraps `common: CommonAgentConfig` and adds its own knobs (`ClaudeAgentConfig` has `model: ClaudeModel`, `thinking_effort: ClaudeEffort`, ...). Composition over inheritance — frozen dataclasses + ABC + defaults play badly together.

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

`domain/supervisor/service.py`. One `asyncio.Task` per running agent. Single-subscriber model: at most one WS subscriber per agent; a new connection replaces the old one cleanly.

Pipeline per event from the adapter:

1. Stamp `seq`.
2. Append to `transcript.ndjson` (with fsync — see [Transcript log](#transcript-log) below).
3. Fan out to the (at most one) live subscriber via a bounded `asyncio.Queue`.

The fsync-before-fanout ordering means a crash never leaves a subscriber having seen an event that isn't on disk.

**Slow-subscriber drop.** `subscribe()` returns an `AgentSubscription { queue, kicked }`. The queue caps at `SUBSCRIBER_QUEUE_MAX` (256). If the consumer falls that far behind, the supervisor catches `QueueFull`, sets `kicked`, and drops the subscriber from the slot — bounding memory growth without blocking the publish path. The WS handler watches `kicked` alongside the queue and closes with code 4408 when it fires; the client retries with backoff and resumes from `?cursor=N`. Events published after the drop still hit disk, so nothing is lost.

Input flow: the WS handler forwards `{"type":"input","text":"..."}` to `supervisor.send_input(slug, text)`, which writes a `UserInput` transcript line and forwards to the adapter's input channel.

`get_work_slug_for(agent_slug)` is the supervisor's in-memory map. Returning `None` means "no live adapter for this slug" — typically because the backend was restarted, or the user closed the agent to the rail. The WS handler resumes the provider session via the `resume_agent` command in that case (see [WS protocol](#ws-protocol)); 4404 is reserved for slugs that don't exist on disk at all.

**Provider session resume.** Each adapter captures the SDK's session/thread ID on its first message and emits a `SessionEstablished` event; the supervisor calls a `set_session_id` callback (wired to `WorkStore.set_agent_session_id`) so the ID is persisted on the agent row. On reconnect after the supervisor lost its in-memory state, `resume_agent` reads the row, builds a fresh adapter, and passes the stored ID back to the SDK as `resume` (Claude) or `continue_thread` (Amp). The conversation continues exactly where it left off — no transcript replay needed on the SDK side.

**Seq seeding on resume.** Because each `_AgentState` starts at `seq=0`, a naive resume would let new events collide with existing on-disk seqs. `start_agent` calls `transcript_log.last_seq(work, agent)` (a tail-read of the NDJSON) and seeds `state.seq` from that, so seqs continue monotonically across the resume boundary.

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
                └── transcript.ndjson
```

**Filesystem is canonical.** SQLite is treated as a derived index. The `WorkStoreService` writes DB first then FS within a service-level `threading.RLock`; a crash between the two leaves an orphan DB row, and startup `reconcile` (`domain/workstore/reconcile.py`) repairs it: delete DB rows whose FS dir is gone; restore DB rows from `work.json`/`agent.json` if the FS has them but DB doesn't; FS wins on any field conflict.

`reconcile(repo, files)` runs in the FastAPI lifespan startup hook. The reconcile invariant is the AC for STORY-005 — see `tests/integration/test_workstore_e2e.py` for the round-trip guarantees.

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
