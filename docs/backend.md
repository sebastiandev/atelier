# Backend

Python 3.11, `uv`-managed venv at `backend/.venv`, FastAPI on `127.0.0.1:8001`.

> Read [`architecture.md`](architecture.md) first for the layer + dependency rules.

## Layout

```
backend/src/
├── application/        # FastAPI: routes, ws, lifespan
│   ├── http/routes/    # works, agents, connections, providers, projects, health
│   ├── http/schemas.py # Pydantic wire types
│   └── ws/agents.py    # /api/agents/{slug}/stream
├── domain/             # framework-free
│   ├── agents/         # configs, specs, ports, events, system_prompt
│   ├── commands/       # works/, agents/, connections/, projects/ — execute() per use case
│   ├── connections/    # ConnectionStore + service + ports
│   ├── supervisor/     # AgentSupervisorService — async, single-subscriber
│   ├── worktrees/      # WorktreeManager port
│   ├── workstore/      # WorkStore + reconcile
│   ├── projectstore/   # ProjectStore + reconcile (mirrors workstore shape)
│   └── models.py       # plain dataclasses
└── infrastructure/     # SA mapping, filesystem, agent SDK adapters, git worktrees, keyring, HTTP verifier
```

## Provider abstraction (Spec / Config / Adapter)

Three roles, one unifying registry:

1. **Config** (`domain/agents/configs.py`) — typed runtime instance. `CommonAgentConfig` carries the cross-provider bits (workdir, system_prompt). Each provider gets a frozen dataclass that wraps `common: CommonAgentConfig` and adds its own knobs (`ClaudeAgentConfig` has `model: ClaudeModel`, `thinking_effort: ClaudeEffort`, ...). Composition over inheritance — frozen dataclasses + ABC + defaults play badly together.

2. **Spec** (`domain/agents/specs.py`) — descriptor + builder. `Spec.describe()` returns a `ProviderDescriptor` that the new-agent dialog renders into form fields. `Spec.build(common, model, options)` validates the wire-format dict into a typed `AgentConfig`. Same Spec instance powers `GET /api/providers` and `POST /api/works/<slug>/agents`, so descriptor and validator can't drift. The `SPECS` registry maps `provider name → Spec`.

3. **Adapter** (`infrastructure/agents/`) — implements `AgentAdapter` Protocol from `domain/agents/ports.py`. Selected via singledispatch on the AgentConfig type:

   ```python
   adapter = build_adapter(config, settings)  # routes to ClaudeAdapter, AmpAdapter, CodexAdapter, …
   ```

**Adding a new provider** is five local steps, none of which modify existing files:

1. Define a `<P>Model` enum and a `<P>AgentConfig` frozen dataclass with `common: CommonAgentConfig`.
2. Write a `<P>Spec` implementing `describe()` + `build()`. Register it in `SPECS`.
3. Write a `<P>Adapter` implementing `AgentAdapter`. Register a `@build_adapter.register` handler.
4. Add the literal value to `Provider` in `domain/models.py`.
5. Add `_convert` unit tests covering the SDK → AgentEvent mapping, plus a manual smoke against the live SDK. (Adapters wrapping external SDKs — Claude, Amp, Codex — are exempt from the parametrised contract suite; the `StubAgentAdapter` keeps that suite hermetic. Integration tests that need a deterministic adapter for `provider="amp"` rely on the autouse fixture in `tests/integration/conftest.py`.)

## AgentEvent union

Frozen variants in `domain/agents/events.py`: `MessageDelta`, `MessageComplete`, `ThinkingDelta`, `ThinkingComplete`, `ToolCall`, `ToolResult`, `StatusChange`, `ArtifactMarker`, `Error`, `TurnMetrics`, `SessionEstablished`, plus `UserInput` (originating from the WS input channel, not the adapter).

Each has a `Literal` `type` discriminator; the frontend pattern-matches on it.

**`ts` is set by the adapter; `seq` is set by the supervisor.** The adapter contract test asserts monotonic `ts`. The supervisor stamps the monotonic `seq` when it appends to the transcript log — so consumers can resume from `?cursor=N`.

### `TurnMetrics` token semantics

`TurnMetrics` carries two flavours of token counts, and they aren't interchangeable. `input_tokens` / `output_tokens` / `cache_read_input_tokens` / `cache_creation_input_tokens` are **cumulative** across every model sub-call in a turn — a turn that fires 20 tool-uses makes 20 API calls and the SDK's `ResultMessage` aggregates them. Summed across turns these equal what Anthropic billed, so they drive **session cost**.

`last_prompt_tokens` is named misleadingly: it's the prompt size of the *last* sub-call, but because each sub-call's prompt replays the entire conversation history (system + every prior user/assistant/tool-use/tool-result + this turn's new user msg + any in-turn tool round-trips), that value equals the **total context currently in the model's window** — the running total, growing monotonically across turns. This is the "should I /clear?" number, used for the **ctx %** badge. Don't sum it across sub-calls or across turns; it's a snapshot. See `domain/agents/events.py:TurnMetrics` for the full docstring; the FE picks it up in `frontend/src/AgentTile.tsx` (`latestMetrics` → `TurnMetricsBar`).

### Canonical tool shape

`ToolCall.name` and `ToolCall.arguments` follow a single canonical shape regardless of which provider SDK emitted the call. Each adapter calls `infrastructure.agents.tool_canonical.canonicalize_tool(name, raw_input)` before yielding `ToolCall` and `PermissionRequest` so provider quirks (Amp's `cmd`/`edit_file`/`old_str` vs Claude Code's `command`/`Edit`/`old_string`) never leak into `domain/` or the frontend renderer. The canonical concepts are `Bash`, `Edit`, `MultiEdit`, `Read`, `Write`, `Grep`, `Glob` — see the `ToolCall` docstring for required/optional keys per concept. Tools without a canonical concept pass through with their raw shape; the frontend falls back to a generic JSON view. Existing on-disk transcripts can be migrated with `scripts/migrate-transcripts.py` (idempotent).

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

### Lifecycle: register, start, resume

`register_agent(work_slug, agent_slug, adapter, context, *, lazy=False)`:

1. **Seed `seq`.** Tail-read `transcript.ndjson` via `transcript_log.last_seq` and seed `state.seq` from it, so a resume continues monotonically rather than colliding with existing history.
2. **Register state.** Under `_registry_lock`, build the `_AgentState` and put it in `_states[agent_slug]`. Concurrent registration of the same slug raises `RuntimeError`; the loser drops its adapter copy.
3. **`await adapter.start(context)`** — cheap per-adapter setup: Amp stands up the permission Unix socket; Claude connects its SDK control channel. The CLI subprocess is NOT spawned here.
4. **Spawn the events-pump task** — `asyncio.create_task(self._run_agent(state))`. Iterating `adapter.events()` is what actually fires the underlying CLI. **Skipped when `lazy=True`** — the first `send_input` creates the task instead.

`_run_agent` is the consumer side: `async for event in adapter.events()`. For `SessionEstablished` events it calls `_set_session_id` (the WorkStore hook) so the SDK's session/thread ID gets persisted to SQL — that's the resume handle. Every event gets `_publish`-ed.

`is_registered(agent_slug) -> bool` lets callers (`connect`, `resume`) check whether to attach to existing state or rebuild the adapter; replaces an earlier side-channel that returned the work slug for the same purpose.

#### The four async commands feeding the supervisor

There is **one inward path** from the WS endpoint into domain logic:

- **`agents/start.execute`** — REST `POST /api/agents`. Allocates the row, builds the adapter, calls `register_agent` **eagerly** (default), and (if contexts produced a synthesised first message) sends it. Fresh agents have no prior provider session to fork from, so eager is fine.
- **`agents/connect.execute`** — WS `/api/agents/{slug}/stream`. `@asynccontextmanager` that resolves the agent (calling `resume.execute` if the supervisor has no live state) and yields a `Subscription`. The WS handler is `async with connect.execute(...) as sub`.
- **`agents/resume.execute`** — Re-attach. Rebuilds the adapter from the persisted row, runs the detach catch-up merge if `status==DETACHED`, and calls `register_agent(..., lazy=True)`. **Lazy** because Amp's `--execute --stream-json` forks on resume — the SDK only spawns once the user actually types.
- **`agents/handle_user_action.execute`** — Inbound from the WS receive loop. Parses the JSON frame into a typed `UserAction` (`SendInput`, `StopTurn`, `ResolvePermission` from `domain/agents/user_actions.py`) and `match`-dispatches to the corresponding supervisor method. The WS receive loop is five lines.

#### Re-attach (resume) and lazy spawn

When `connect` finds the supervisor has no live state — backend restart, agent closed-to-rail, or `status=DETACHED` — it calls `resume.execute`. Resume runs `register_agent(..., lazy=True)`: the events pump is **not** started. The user sees the existing transcript via the replay window. Only when they type does `send_input` create the pump task and the SDK process spawn. This is the fix for Amp's fork-on-resume bug — a view-only re-attach no longer burns a fork.

### Lifecycle: stopping a turn / closing an agent

- **`stop_turn(agent_slug)`** writes a `user_stop` transcript line (so the user's intent is durable even if the SDK call fails), then `await state.adapter.stop_turn()`. Claude calls `interrupt()` over the SDK control protocol; Amp no-ops today (its CLI exposes no per-turn cancel).
- **`stop_agent(agent_slug)`** pops the state from the registry, cancels the agent task, awaits it (suppressing `CancelledError`), and calls `adapter.close()`. Idempotent on the slug.
- **`shutdown()`** stops every running agent — called from the FastAPI lifespan teardown.

### Resume after a backend restart

`connect.execute` is the inward path. If `is_registered(slug)` is False, it resolves the work slug via `workstore.get_work_slug_for_agent`, then calls `resume.execute`:

1. Read the persisted agent row + its `session_id` (the SDK's resume handle, captured earlier).
2. Build a fresh adapter through the spec registry — same persona/role/provider/model — and pass `session_id` into the new `AgentStartContext`. Claude forwards as `resume=<id>`; Amp as `continue_thread`.
3. If `status == DETACHED`, run the detach catch-up merge first (see [Detach to CLI + catch-up](#detach-to-cli--catch-up)).
4. `register_agent(..., lazy=True)` — registers state, runs `adapter.start` (cheap setup), but doesn't fire the events pump. Concurrent registration races (StrictMode double-mount, two tabs) raise `RuntimeError`; resume drops its adapter copy and verifies the agent IS now registered.
5. `connect` then yields the `Subscription`. The client replays from its `?cursor=N` cursor (events on disk past the cursor) and then drains live events from the queue.

The 4404 close code is reserved for agents that don't exist on disk at all.

### Detach to CLI + catch-up

Detaching hands a running agent to a terminal CLI. `agents/detach.execute` stops the supervisor's task, flips status to `DETACHED`, writes a `user_detached` transcript marker carrying an `sdk_cursor` snapshot (Claude: timestamp; Amp: message count), then shells out to the user's preferred terminal with the right resume command.

The resume command preserves the agent's selector + provider options so the CLI session keeps the user's choice instead of silently dropping to the local CLI default. `infrastructure/cli_launcher/build_resume_command` reads `agent.model` (Claude model id / Amp mode) and `agent.options` (the dict the Spec validated at create time, persisted on the row — see [Persisted provider options](#persisted-provider-options) below) and emits:

| Provider | Selector flag | Option flags |
|---|---|---|
| Claude | `--model <id>` | `--effort <level>` (skipped when `thinking_effort=="off"`); `--permission-mode <m>` (skipped when `permission_mode=="default"`, since the CLI applies that anyway) |
| Amp | `--mode <mode>` | `--dangerously-allow-all` when `permission_mode=="allow_all"`. Amp's other permission modes (`default`/`custom`) and `custom_allowed_tools` are Atelier-side constructs (the bridge) — they don't translate to CLI flags. |
| Codex | `--model <id>` | `--sandbox <mode>` (skipped when `workspace-write`, the default); `--ask-for-approval <mode>` (skipped when `on-request`, the default); `-c model_reasoning_effort=<level>` (skipped when `medium`, the default) routed through Codex's TOML config override since the CLI has no dedicated reasoning-effort flag. The resume invocation is `codex exec resume <sid>` plus the flags. |

Legacy agents whose `options` column is NULL (rows created before schema v9) detach with the bare `claude --resume <id>` / `amp threads continue <id>` / `codex exec resume <id>` shape — same behaviour as before the column existed. Unit tests in `tests/unit/infrastructure/cli_launcher/test_build_resume_command.py` pin every flag combination.

Re-attach runs through `resume.execute` → `_catch_up_detached_agent`:

- Read the SDK's transcript file/thread (Claude: `~/.claude/projects/<munged-cwd>/<sid>.jsonl`; Amp: `amp threads export <id>`) starting from the `sdk_cursor`. Translate Anthropic-shaped content blocks (`text` / `thinking` / `tool_use` / `tool_result`) into `AgentEvent`-shaped dicts and append to NDJSON.
- If `agent.parent_session_id` is set and not already ingested, also export the parent session in full and emit a `sdk_session_merged` marker. Dedup is via `WorkStore.is_session_ingested`, which scans NDJSON for a `session_established` event (supervisor streamed the parent live) or a previous `sdk_session_merged` marker. Only depth-1 — the agent row stores one parent.
- Emit a `user_reattached` marker; flip status back to `IDLE`.

`agents.parent_session_id` (schema v6) is set atomically inside `set_agent_session_id`: when the new sid differs from the current, the previous sid is captured as parent. This linked-list lineage exists so providers that fork on resume (Amp) can recover the original transcript from the orphaned ancestor.

### How the SDK adapters fit in

Each adapter implements the `AgentAdapter` Protocol from `domain/agents/ports.py`:

| Method | When called | What it does |
|---|---|---|
| `start(context)` | once, by `register_agent` | Cheap per-adapter setup: Amp's permission socket, Claude's SDK control channel. Does **not** spawn the CLI subprocess. |
| `events()` | once, by `_run_agent` (the events-pump task) | Async generator. Emits normalised `AgentEvent`s in the order the SDK produces them. **First iteration** is what spawns the underlying CLI — so the lazy-resume path (which skips creating the pump task) keeps the SDK dormant. |
| `send_input(text)` | by `send_input` (also called by `start.execute` to inject a synthesised first message) | Pushes a turn into the adapter's input channel. |
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

### Tool permissions for Amp: the delegate-bridge

Amp's SDK has no async permission callback — its CLI exposes a *declarative* permission system (per-tool ``allow|reject|ask|delegate`` rules in a settings file) and a ``--dangerously-allow-all`` flag. ``ask`` blocks the CLI on a TTY prompt, which we can't answer because we pipe stdin/stdout. So the only knob that lets us hold the model mid-call is ``delegate`` — substituting a custom command for the tool's native execution.

We use ``delegate`` to gate Bash specifically. The other tools (Read/Edit/Write/Grep/Glob/…) are Amp-internal; replacing them would mean reimplementing their semantics, which would drift fast. **So only Bash is gated on Amp.** That covers ``git commit/push``, ``gh pr create``, file deletes, ``sudo`` — the real footguns. Edit/Write to your own working tree is comparable risk to typing it yourself.

```
   Amp CLI ──► python amp_permission_bridge.py -c "<command>"
                     │
                     ├─ reads $ATELIER_PERMISSION_SOCKET
                     ├─ AF_UNIX connect → writes {tool:"Bash", argv:["-c","<cmd>"]}
                     ├─ blocks on socket read for {decision:"allow"|"allow_always"|"deny"}
                     ├─ on allow:  os.execvp("bash", ["bash","-c","<cmd>"])
                     │             ↑ replaces the bridge process; Amp sees the
                     │               real bash exit code, stdout, stderr.
                     └─ on deny:   print "atelier: denied by user" to stderr; exit 1
                                   ↑ Amp surfaces stderr as the tool result.

   AmpAdapter listens on the socket:
        on connect → reads request line
                   → calls _decide_permission(tool, argv)
                       → emits PermissionRequest(request_id, tool_name, tool_input)
                         into _outgoing → events() → supervisor → WS → tile prompt UI
                       → awaits self._pending[rid]
                   → user clicks → ws frame → supervisor.resolve_permission
                                            → adapter.resolve_permission(rid, decision)
                                            → fut.set_result(decision)
                       → emits PermissionDecision(request_id, decision)
                   → writes {decision} back to bridge socket
        bridge unblocks, exec/exit accordingly.
```

**Why a Unix socket** and not HTTP: parent-child IPC over a 0700 tmpdir, no network surface, no auth tokens — the random socket path *is* the secret. The path comes in via the env var ``ATELIER_PERMISSION_SOCKET`` that we set on ``AmpOptions.env`` for the CLI subprocess.

**Why ``execvp`` and not ``subprocess.run``**: the shim becomes the bash process. No double fork, stdout/stderr stream straight back to Amp, signals work naturally, exit code propagates. From Amp's perspective the delegate target IS bash — it can't tell we proxied.

**Permission modes** (``AmpAgentConfig.permission_mode``):
- ``DEFAULT`` — opens the socket, registers ``Bash → delegate`` plus an explicit allow-list for read tools / Edit / Write / etc.
- ``ALLOW_ALL`` — passes ``--dangerously-allow-all``, skips the socket entirely. Old pre-permission behaviour. Risky.
- ``CUSTOM`` — opens the socket, ``Bash → delegate``, allow-list comes from the user-supplied tool names. ``"Bash"`` in that list is silently dropped (the user isn't allowed to disable shell gating from the dialog).

**Limitations to keep in mind:**
- Only Bash is gated. Edit/Write to your repo by an agent you launched is auto-approved on Amp; if that's a concern, run those tasks under Claude.
- ``allow_always`` is per-tool, session-only. A "Allow always" click on Bash means *every* subsequent Bash invocation runs without asking. The session-only scope means restarting the agent restores the prompt.
- The CLI's default for un-listed tools is ``ask``, which would hang. So the adapter **enumerates** every tool the agent uses. If a brand-new Amp tool ships and isn't in our list, the agent will block; the fix is adding it to ``AMP_DEFAULT_AUTO_ALLOWED_TOOLS``. Failing closed beats silent auto-allow.
- The bridge is fail-closed. Missing socket, missing env var, malformed handshake → exits non-zero with a stderr message.

The bridge itself (``infrastructure/agents/amp_permission_bridge.py``) is stdlib-only — it ships in the source tree but runs as a detached child of the Amp CLI, so it must not import any Atelier modules (the CLI's invocation env doesn't carry our virtualenv).

### Tool permissions for Codex: native typed approvals

Codex is the best-instrumented of the three providers for fine-grained permissions. The Codex JSON-RPC protocol natively distinguishes ``commandExecution`` approvals from ``fileChange`` approvals, and the SDK routes each server-initiated request through a typed callback we register at thread-start. No bridge socket needed — the SDK handles framing, reconnect, and decision round-trip.

``CodexAdapter._handle_approval_request(request)`` is the entry point: canonicalises the tool name + input via ``tool_canonical``, short-circuits on the session-only ``_allow_always`` set, otherwise creates a future + publishes a ``PermissionRequest`` event onto ``_outgoing`` and blocks. The decision arrives via ``resolve_permission`` (same uniform path as Claude), flips ``allow``/``allow_always``/``deny`` to Codex's ``accept``/``decline`` at the boundary, and the SDK forwards the answer.

Two layers sit on top of the per-tool callback:

- **``CodexSandbox``** — OS-level filesystem gating (``read-only`` / ``workspace-write`` (default) / ``danger-full-access``). Forwarded as the ``sandbox`` param to ``thread_start``; the Codex subprocess enforces it. Orthogonal to the per-tool prompt: a tool inside the sandbox still routes through the approval callback if the approval mode says so.
- **``CodexApprovalMode``** — when to prompt. ``on-request`` (the default) is what routes Codex's prompts to Atelier's UI; ``never`` auto-runs everything, ``untrusted`` prompts on every tool.

Atelier's worktree (``~/Atelier/works/<slug>/worktrees/<agent>/``) is the right grain for ``workspace-write`` — Codex writes stay scoped to the agent's worktree with no extra config.

### SDK seam

Production wires the real ``openai-codex-sdk`` via a lazy ``_default_client_factory`` that only imports the SDK at first call (so the adapter module stays loadable on machines where the SDK isn't installed; the missing dep only fails the agent actually creating a Codex session). Tests inject a fake factory matching the local ``CodexClient`` / ``CodexThread`` / ``CodexTurnHandle`` Protocols — see ``tests/unit/infrastructure/agents/test_codex_adapter.py`` for the fixture set. Same shape as Amp's ``executor`` DI seam.

### Slow-subscriber drop

`subscribe()` returns an `AgentSubscription { queue, kicked }`. The queue caps at `SUBSCRIBER_QUEUE_MAX` (256). If publishing overflows it, the supervisor catches `QueueFull`, sets `kicked`, and drops the subscriber slot — bounding memory growth without blocking the publish path. The WS handler watches `kicked` alongside the queue and closes with code 4408 when it fires; the client retries with backoff and resumes from `?cursor=N`. Events published after the drop still land on disk, so nothing is lost.

### Subscribe atomicity (replay vs live)

`subscribe()` snapshots `from_seq = state.seq` *under the publish lock*, then registers the queue *under the same lock*. That atomicity is the trick that makes "replay-then-live" exactly-once: any event with `seq <= from_seq` is already on disk and in the replay window; any event with `seq > from_seq` lands in the queue and only there. No overlap, no gap. See [WS protocol](#ws-protocol) for how the handler stitches the two.

## Persistence model

```
~/Atelier/
├── atelier.db                  ← SQLite (queryable cache)
├── projects/
│   └── <project_slug>/         ← canonical project metadata
│       └── project.json
└── works/
    └── <work_slug>/            ← canonical work metadata
        ├── work.json           ← carries optional project_slug
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

**Filesystem is canonical.** SQLite is treated as a derived index. The `WorkStoreService` and `ProjectStoreService` each write DB first then FS within a service-level `threading.RLock`; a crash between the two leaves an orphan DB row, and startup `reconcile` (`domain/workstore/reconcile.py`, `domain/projectstore/reconcile.py`) repairs it: delete DB rows whose FS dir is gone; restore DB rows from `work.json` / `project.json` / `agent.json` if the FS has them but DB doesn't; FS wins on any field conflict.

**Reconcile order matters.** `reconcile_projects(repo, files)` runs **before** `reconcile_works` in the FastAPI lifespan startup hook because `works.project_slug` is a slug FK to `projects.slug`; if a work's project hasn't been inserted yet, the work upsert violates the FK. The order is fixed in `main.py`'s lifespan (look for the comment "Projects reconcile FIRST"). Same rule will apply to any future cross-store reference.

The reconcile invariant is the AC for STORY-005 — see `tests/integration/test_workstore_e2e.py` for the round-trip guarantees. Project-side reconcile is unit-tested with stubs in `domain/projectstore/reconcile.py`.

### Persisted provider options

The `agents.options` column (schema v9, JSON-encoded TEXT, NULLABLE) stores the dict that the provider Spec validated at create time — `{permission_mode, thinking_effort, custom_allowed_tools, …}` minus whatever the user didn't set. Two consumers read it:

- `agents/resume.execute` (`backend/src/domain/commands/agents/resume.py`) calls `SPECS[provider].build(common, agent.model, dict(agent.options or {}))`. Before v9 the third arg was `{}` and re-attach silently reset every option to its provider default — that's the latent drift this column closes.
- `agents/detach.execute` forwards `agent.options` into `build_resume_command` (see [Detach to CLI + catch-up](#detach-to-cli--catch-up)) so the CLI session inherits the same flags.

Backward compatibility: existing rows have `options=NULL` and `Agent.options=None` after deserialisation. Both consumers normalise that to an empty dict, so the legacy path is byte-identical to pre-v9 behaviour. `serialize_agent` only emits the `options` key when set, so old `agent.json` files round-trip through reconcile unchanged.

The wire format already had `NewAgentRequest.options: dict[str, Any]`; persistence is just plumbing it into the row instead of dropping it after Spec validation.

## ProjectStore

`domain/projectstore/` mirrors `workstore/`'s shape: `ProjectStore` is the public port; `ProjectRepository` and `ProjectFiles` decompose it into testable pieces. There is no transcript-log analogue because Projects own no children today — they're optional grouping metadata, not workspaces.

- **Slugs:** `PRJ-{id:03d}` allocated post-flush, same two-flush placeholder pattern as Work / Connection (`SqlProjectRepository.add_project`).
- **Work → Project link:** `Work.project_slug` is a nullable `str` on the dataclass and a TEXT FK column on the SQL side (`ON DELETE SET NULL`). Optional by design — works without a project are first-class "loose work", not a hidden bucket.
- **Defaults:** `Project.default_jira_conn` / `default_sentry_conn` hold connection slugs; FK to `connections.slug`. Read-through at use-time, not denormalised onto Works — editing a project's defaults later is reflected in any work created under it.
- **Routes:** `GET /api/projects`, `POST /api/projects`, `GET /api/projects/{slug}`. PATCH/DELETE are implemented at the service + DTO layer (`PatchProjectRequest`, `update_project`, `delete_project`) but not yet routed.

`POST /api/works` accepts an optional `project_slug`. The route validates it against `ProjectStore` and returns 422 if unknown — same shape as connection-context validation in agent-create.

## Contexts pipeline

A `Context` (`domain/models.py`) is `(type, value, conn_id?)`. Both `Work` and `Agent` can carry a list — but **contexts are FS-only**: they live in `work.json` / `agent.json` next to the entity, never on the SQL row. The dataclasses themselves don't have a `contexts` field (SQLAlchemy populates instances via `__new__` + setattr on mapped columns and bypasses `__init__`, so a `field(default_factory=list)` default would never fire — `dataclass.__eq__` then crashes in `reconcile`'s `db_agent != fs_agent` check). Instead contexts travel as a sibling value: `serialize_work_record(work, contexts)` and `serialize_agent(agent, contexts)`.

**At agent-create time** (`domain/commands/agents/start.py`), connection-backed contexts (`jira` / `sentry` / `honeycomb`) are pre-fetched **before** the agent row is allocated: the command iterates `req.contexts`, calls `connection_store.fetch_context_body(c)` for each connection-backed entry, and builds a `dict[int, str]` of resolved bodies keyed by index. Any `ContextFetchError` raised by the fetcher (missing connection, missing token, network/auth/HTTP failure) propagates straight out of `execute()` — the route maps it to 422. Halting here means a fetch failure leaves no agent row, no worktree, no context dir to clean up. Then the `WorkStore.render_agent_contexts(work_slug, agent_slug, contexts, fetched_bodies)` port is called once. `domain/agents/context_render.py` is the pure-domain renderer:

- For `text` / `url` / `file` / `agentout` it generates the body inline from `c.value`.
- For `jira` / `sentry` / `honeycomb` it writes the matching entry from `fetched_bodies` at `context/<type>-<value>.md` (when the value parses as a slug, else numbered). A connection-backed context with no entry in `fetched_bodies` raises `RuntimeError` — the boundary is responsible for resolving them.
- Then it builds `context.md` — sections grouped by type, one bullet per file, linked relatively (`[text-1.md](context/text-1.md)`).

Per-source fetchers live under `infrastructure/connections/fetchers/`, dispatched by `connection.type` like the verifier. Currently registered: `jira` (full Jira REST API v3 — ADF → markdown for description + chronologically-ordered comments) and `sentry` (two-call: org-scoped issue endpoint for the header + `events/latest/` for stacktrace, HTTP request, tags, contexts, and additional data — auth headers redacted; in-app frames preferred when capping; event-call failures degrade to header-only). `honeycomb` falls through to the singledispatch default which raises `ContextFetchError("not yet supported")` so the user sees an actionable message rather than a silent placeholder.

The renderer returns the absolute path of `context.md`, which becomes the `first_message` injected by the supervisor on first start (see [AgentSupervisorService](#agentsupervisorservice)). Token-budget-conscious by design: the index is the only thing sent to the model; the agent decides what to actually read.

**Why filesystem, not a DB column.** Contexts are user-curated reference material, not queryable state. Putting them in SQL means a join table or a JSON column, both of which fight reconcile's "FS is canonical" invariant. The agent's `Read` tool already handles the FS — adding a SQL relation buys nothing. If we ever need to filter agents by context-type or count them in a query, that's the trigger to revisit; today nothing reads them via SQL.

## Transcript log

`infrastructure/filesystem/ndjson.py`. Append-only NDJSON, one JSON object per line, fsync after each line.

Reads are crash-safe: if the file was truncated mid-line by a crash, the reader detects the partial trailing line and the next append repairs it (truncate + append). See `tests/integration/test_transcript_log.py`.

The cursor is a `seq` integer. `read_from_cursor(work_slug, agent_slug, cursor)` yields events with `seq > cursor`.

## WS protocol

`/api/agents/{agent_slug}/stream?cursor=N`

**Server → client**: each frame is one `AgentEvent` serialized as JSON. The supervisor maintains the seq monotonicity, so the client can persist the last seq and resume from there on reconnect.

**Client → server**: the WS receive loop parses each frame into a typed `UserAction` (`domain/agents/user_actions.py`) and forwards to `handle_user_action.execute`. Three action types:

- `{"type":"input","text":"..."}` → `SendInput` — appends a `user_input` transcript line and forwards to the adapter (creates the lazy pump if not yet running).
- `{"type":"stop"}` → `StopTurn` — appends a `user_stop` transcript line and calls `adapter.stop_turn()`. Claude interrupts mid-turn via the SDK's control protocol; Amp's adapter no-ops for now (the SDK exposes no per-turn cancel — full per-turn cancel for Amp is a tracked follow-up). The user-facing intent is always recorded.
- `{"type":"permission","request_id":"...","decision":"allow|allow_always|deny"}` → `ResolvePermission` — answers a `PermissionRequest` the adapter previously emitted. The decision values come from `get_args(PermissionDecisionValue)` so the wire and the domain stay in lockstep.

Frames that don't parse to a known action are ignored.

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

**Resume on reconnect.** When the slug is in SQLite but the supervisor has no live state, `connect.execute` calls `resume.execute(...)` to rebuild the adapter (passing through the persisted `session_id`) and `register_agent(..., lazy=True)` to attach without firing the SDK pump. A normal replay-then-live then proceeds — the client doesn't need to know whether it's connecting to a fresh adapter, an in-flight one, or a freshly-resumed (lazy) one.

## WorktreeManager

`domain/worktrees/`. Provisions a per-agent workdir so each agent gets its own checkout without stepping on the user's main one. Operations: `ensure`, `ensure_forked`, `is_detached`, `remove`, `sweep_orphans`. The implementation (`infrastructure/git/worktree_manager.py`) shells out to `git worktree` — a few subprocess calls beat pulling in gitpython.

**Layout** mirrors the architecture: `<workspace_root>/works/<work_slug>/worktrees/<agent_slug>/`.

**Default is detached HEAD.** `ensure(..., branch_name=None)` runs `git worktree add --detach`, matching `ensure_forked`'s long-standing shape. The agent (or user) names a branch later via `git switch -c <name>`. The system prompt rendered for detached worktrees includes a guard: don't `checkout`/`switch` to another branch without first creating one from current HEAD — that's the only path that orphans commits. `system_prompt.render_system_prompt(..., is_detached_worktree=True)` injects the block; both `start.execute` and `resume.execute` derive the flag by calling `worktree_manager.is_detached(workdir)` so the truth in the prompt always matches the truth on disk.

**Opt-in named branch.** `ensure(..., branch_name="my-feature")` keeps the legacy `git worktree add -b <name>` path with the existing self-heal-on-collision behaviour (branch already exists → attach; stale registry entry → prune + retry). Surfaced via the optional `branch_name` field on `NewAgentRequest` / the New Agent dialog's "Branch" input; the branch picker (see `GET /api/git/branches`) lets the user pick from the source repo's existing branches.

**Pass-through for non-git folders.** If the work's `folder` is not a git repo, `ensure` returns the folder itself instead of trying (and failing) to create a worktree. The dialog hint already tells the user "If it's a git repo, agents will spawn worktrees here automatically." — non-repo folders keep working without forcing the user to convert them. `is_detached` returns `False` for non-git folders so callers can use it as a soft hint without branching on the git-vs-not-git case.

**Removal escalates.** `git worktree remove` first; on dirty/locked, retry with `--force`; on still-failing, recursive `rmtree` plus `git worktree prune` to clean up the source repo's registry. A wedged worktree never blocks provisioning a fresh one. The teardown also best-effort-deletes the per-agent `atelier/<work>/<agent>` branch when one was created (no-op for detached worktrees).

**Orphan sweep on startup.** `main.py` lifespan walks every work, asks the workstore for live agent slugs, and tells the manager to remove worktree directories that don't match. This is the cleanup path for crashed runs and soft-deleted works. It satisfies the AC "deleting a Work removes them" via reconcile-style sweep rather than coupling the soft-delete command to git ops directly.

**Wired into the `agents/start` command** (`domain/commands/agents/start.py`). The route stays thin: parse the request, `await start.execute(...)`. The command validates the provider config first (via `Spec.build`) so a bad model can't allocate an agent row + worktree we'd have to roll back, then pre-fetches connection-backed contexts before any side effect, then `register_agent` (eager) and an optional first-message send. Domain errors: `WorkNotFound` → 404; `InvalidProviderConfig` / `AgentFolderMissing` / `ContextFetchError` → 422.

### Branch listing for the picker

`infrastructure/git/branches.py:list_branches(path)` shells out to `git for-each-ref --sort=-committerdate refs/heads/` so the New Agent dialog's branch picker can offer existing branches sorted by recency. Returns `[]` for non-git / missing paths so the FE renders a friendly "not a git repo" hint instead of branching on error codes. Surfaced via `GET /api/git/branches?path=<absolute>` (`application/http/routes/git.py`).

## PrStatusPoller

`infrastructure/artifacts/pr_status_poller.py` owns two refresh paths against the same `refresh_pr_statuses` command:

- **Scheduled loop** — every 5 minutes the loop calls the command against the shared `GitHubPrStateFetcher`. Lifecycle is owned by the FastAPI lifespan: `start()` spawns the task, `stop()` cancels and awaits.
- **On-demand refresh** — `refresh_now()` runs the same command out-of-band, triggered by `POST /api/artifacts/refresh-pr-statuses` when a `WorkView` mounts with non-terminal PRs. Throttled to one actual run per 30s; concurrent callers within the window get `None`. The scheduled loop and `refresh_now` share the same throttle clock, so a cycle that just ran satisfies the throttle for the next 30s of on-demand calls.

Each PR row carries a `pr_etag` column (added in schema v11; nullable). The fetcher sends it as `If-None-Match` on subsequent calls — GitHub answers 304 with no body, which doesn't count against the authenticated 5k/hr rate budget. On rotation the new ETag is persisted via the workstore's `update_pr_artifact_etag` (or via the same `update_artifact_status` write when the status itself changed).

## UpdateChecker

`domain/update_check/` defines a flat `UpdateStatus` dataclass and a single async `UpdateChecker` Protocol. `infrastructure/update_check/git_checker.py` implements it by shelling out to `git fetch <remote> <branch>` and comparing local `HEAD` to the fetched tip; the repo root is derived from this package's location (`Path(__file__).resolve().parents[4]`), so the backend always tracks its own checkout regardless of where the process was launched.

`UpdateCheckPoller` (`infrastructure/update_check/poller.py`) owns the cycle: it runs one check on start (so a user who reboots after pulling sees the chip immediately) then loops every 2h. The poller's `status` attribute is the canonical snapshot for the process; `GET /api/update-status` (see `api-flows.md`) reads it directly. Errors during fetch are swallowed and surfaced as `None` — the last successful status is retained so a flaky network doesn't flicker the chip off.

The checker is inert on hosts without git installed or without a `.git/` directory — `available` defaults to `false` and the chip stays hidden. There's no auth: the only network call is `git fetch origin main`, which works for any public/cloned repo without extra credentials.

## ConnectionStore

`domain/connections/`. Source-system credentials (Jira, Sentry, Honeycomb) split across two stores by design:

- **SQLite** holds the metadata row only — `id`, `slug`, `type`, `name`, `created_at`, optional `url`/`org`/`region`/`env`/`team`/`email`, plus `verified` + `last_used`. **No token, no keyring reference**: the keychain key is the slug (`atelier:con-3`), so storing the reference would just duplicate state.
- **OS keychain** (via the Python `keyring` package) holds the token under `(service="atelier", username=<slug>)`.

`ConnectionStoreService` (the public port `ConnectionStore`) composes four narrower ports — `ConnectionRepository` for the SQL row, `SecretStore` for the keychain, `ConnectionVerifier` for the source's auth endpoint, and `ContextFetcher` for pulling a context body (Jira ticket, etc.) — same pattern as `WorkStoreService`. The verifier is a simple type-keyed dispatch (`infrastructure/connections/verifier.py`): Jira hits `/rest/api/3/myself` with Basic auth, Sentry hits `/api/0/organizations/{org}/` with a Bearer token (validates token *and* org slug), Honeycomb hits `/1/auth` with `X-Honeycomb-Team`. Network errors map to `VerifyResult(verified=False, error=...)`; the verifier never raises.

`ContextFetcher` follows the same dispatch shape (`infrastructure/connections/fetchers/`). `ConnectionStoreService.fetch_context_body(context)` resolves the connection + token from the context's `conn_id`, calls the fetcher, and stamps `last_used` on success. Any failure — missing connection, no token in the keychain, fetcher raises — surfaces as `ContextFetchError`. Called by `agents/start` to pre-fetch agent contexts before allocating the row.

**Token never crosses the API surface.** `NewConnectionRequest` and `PatchConnectionRequest` accept `token`; `ConnectionRead` (the response model) has no `token` field at all. Tests assert this on every read path. On `verify` success the supervisor materialises the token in-memory, presents it to the verifier, then discards it — the `Connection` entity never carries it.

**Typed configs.** The wide nullable columns (url/org/region/env/team/email) collapsed into a single JSON ``config`` column. Each source owns a frozen dataclass (``JiraConfig``, ``SentryConfig``, ``HoneycombConfig``) in ``domain/connections/configs.py``; the repository serialises typed → dict at flush and dict → typed after load. The verifier and fetcher both dispatch on ``type(config)`` via ``functools.singledispatch`` — adding a new source = new config dataclass + register a handler, no schema migration. The wire format uses a Pydantic discriminated union: ``{"name": "...", "token": "...", "config": {"type": "jira", "url": "...", "email": "..."}}``.

**Type descriptors.** ``GET /api/connections/types`` returns a ``ConnectionDescriptor[]`` that the frontend renders into per-type forms — same pattern as ``GET /api/providers``. Each descriptor exposes ``label``, ``glyph``, ``docs`` URL, ``config_fields`` (id/label/placeholder/required/secret/options), and two capability flags: ``verifiable`` and ``context_fetchable``. The FE uses ``context_fetchable`` to filter the agent-context picker so users can't pick a source whose fetcher would 422 at agent creation.

REST endpoints (`application/http/routes/connections.py`):

```
GET    /api/connections/types             -> ConnectionDescriptor[]
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
