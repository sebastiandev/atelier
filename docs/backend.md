# Backend

Python 3.11, `uv`-managed venv at `backend/.venv`, FastAPI on `127.0.0.1:8001`.

> Read [`architecture.md`](architecture.md) first for the layer + dependency rules.

## Layout

```
backend/src/
├── application/        # FastAPI: routes, ws, lifespan
│   ├── http/routes/    # works, agents, providers, health
│   ├── http/schemas.py # Pydantic wire types
│   └── ws/agents.py    # /api/agents/{slug}/stream
├── domain/             # framework-free
│   ├── agents/         # configs, specs, ports, events, system_prompt
│   ├── commands/       # works/, agents/, ... — execute() per use case
│   ├── supervisor/     # AgentSupervisorService — async, single-subscriber
│   ├── workstore/      # WorkStore + reconcile
│   └── models.py       # plain dataclasses
└── infrastructure/     # SA mapping, filesystem, agent SDK adapters, keyring
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
5. Extend the contract test suite (`tests/contract/test_agent_adapter.py`) to parametrise on it.

## AgentEvent union

Seven frozen variants in `domain/agents/events.py`: `MessageDelta`, `MessageComplete`, `ThinkingDelta`, `ThinkingComplete`, `ToolCall`, `ToolResult`, `StatusChange`, `ArtifactMarker`, `Error`, plus `UserInput` (originating from the WS input channel, not the adapter).

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

`get_work_slug_for(agent_slug)` is the supervisor's in-memory map. Returning `None` means "no live adapter for this slug" — typically because the backend was restarted after the agent ran. The WS handler closes with code **4404** in that case (see [WS protocol](#ws-protocol)).

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

**Client → server**: `{"type":"input","text":"..."}`. Anything else is ignored.

**Replay-then-live** semantics on connect:

1. Take a snapshot `from_seq` of the supervisor's current seq for this agent.
2. Replay the disk-side window `(cursor, from_seq]`.
3. Drain the per-subscription `asyncio.Queue` for `seq > from_seq`.

This is "no duplicates, no gaps" by construction — events with `seq <= cursor` are excluded from replay; events with `seq > from_seq` arrive only via the queue.

**Close codes the frontend cares about:**

| Code | Meaning |
| --- | --- |
| 4404 | Supervisor has no live adapter for this slug. Terminal — frontend stops retrying and surfaces "stopped". |
| 4408 | Slow subscriber — the per-subscription queue overflowed. Frontend retries with backoff and resumes from `?cursor=N`. |
| 1000/1001/etc. | Transient (network, server restart). Frontend retries with exponential backoff. |

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
