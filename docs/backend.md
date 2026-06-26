# Backend

Python 3.11, `uv`-managed venv at `backend/.venv`, FastAPI on `127.0.0.1:8001`.

> Read [`architecture.md`](architecture.md) first for the layer + dependency rules.

## Layout

```
backend/src/
├── application/        # FastAPI: routes, ws, lifespan
│   ├── http/routes/    # works, agents, chats, connections, providers, projects, health
│   ├── http/schemas.py # Pydantic wire types
│   └── ws/             # /api/agents/{slug}/stream, /api/chats/{slug}/stream
├── domain/             # framework-free
│   ├── agents/         # configs, specs, ports, events, system_prompt
│   ├── commands/       # works/, agents/, chats/, connections/, projects/ — execute() per use case
│   ├── chatstore/      # ChatStore + chat transcript metadata
│   ├── connections/    # ConnectionStore + service + ports
│   ├── supervisor/     # AgentSupervisorService — async, single-subscriber
│   ├── worktrees/      # WorktreeManager port
│   ├── workstore/      # WorkStore + reconcile
│   ├── projectstore/   # ProjectStore + reconcile (mirrors workstore shape)
│   └── models.py       # plain dataclasses
└── infrastructure/     # SA mapping, filesystem, agent SDK adapters, git worktrees, keyring, HTTP verifier
```

## Exploratory Chat Store

Exploratory chats are a lightweight sibling to Work, not an agent type. `domain/chatstore/` composes a `ChatRepository` SQL index with `ChatFiles` under `~/Atelier/chats/<CHT-NNN>/` (`chat.json` plus `transcript.ndjson`). Schema v13 adds the `chats` table and nullable `works.from_chat_slug` / `works.from_chat_title`; schema v14 adds nullable `chats.session_id` for provider-backed stream resume; schema v15 adds nullable `chats.working_directory` so the Project/Work link can stay separate from the provider cwd; schema v16 adds nullable `chats.options` for provider permission/mode/effort choices. Existing rows read as unlinked, session-less, default-working-folder, and provider-default options. The wire and filesystem shapes are additive: `work.json` may include optional `from_chat` and `chat_context_folders`, `chat.json` may include optional `working_directory` and `options`, and readers default both when absent.

Promotion runs through `application/http/routes/chats.py`: it summarizes the confirmed brief into a work-scoped `context.md`, creates the Work through `WorkStore`, marks `Chat.promoted_to_work_slug`, and stores the context folder under `~/Atelier/works/<WRK>/chat-contexts/<folder>/`. Work-grounded chat tiles can also call `POST /api/works/{work}/chats/{chat}/context`, which uses `WorkStore.ensure_work_chat_context` to reuse or append that same folder shape before seeding the New Agent dialog with the resulting file context. Agent start mounts those work-scoped chat context folders into the agent worktree alongside project shares and forwards their resolved roots through the normal writable-roots path; see `domain/commands/agents/start.py`.

Runtime-backed chats use a separate `AgentSupervisorService` instance (`app.state.chat_supervisor`) with `FsChatTranscriptLog`, so `CHT-NNN` gets the same replay cursor, input/stop/permission frames, provider session persistence, and adapter event pump as agents without sharing agent worktrees, artifacts, or counts. `domain/commands/chats/connect.py` resolves the chat, builds the provider adapter from its immutable provider/model/options, registers lazily, claims the first persisted user prompt exactly once, and then yields the normal supervisor subscription. When `working_directory` is set it becomes the cwd/writable root; otherwise Work/Project links fall back to their Atelier metadata folders, and legacy folder-grounded chats are treated as working-folder chats. Legacy `role/body` transcript rows are translated into `user_input` / `message_complete` stream events on replay, and REST reads map new supervisor events back into `ChatMessage` for promotion/context generation. The old `POST /api/chats/{slug}/messages` append route remains for compatibility but the frontend uses `WS /api/chats/{slug}/stream` for turns. Chat compaction uses the same `CompactionSessionClient` provider-session port as agents, but stores summaries under `chats/<CHT>/compactions/` and only swaps `chats.session_id`; there is no worktree or parent agent lineage.

## Provider abstraction (Spec / Config / Adapter)

Three roles, one unifying registry:

1. **Config** (`domain/agents/configs.py`) — typed runtime instance. `CommonAgentConfig` carries the cross-provider bits (workdir, system_prompt). Each provider gets a frozen dataclass that wraps `common: CommonAgentConfig` and adds its own knobs (`ClaudeAgentConfig` has `model: ClaudeModel`, `thinking_effort: ClaudeEffort`, ...). Composition over inheritance — frozen dataclasses + ABC + defaults play badly together.

2. **Spec** (`domain/agents/specs.py`) — descriptor + builder. `Spec.describe()` returns a `ProviderDescriptor` that the new-agent dialog renders into form fields. `Spec.build(common, model, options)` validates the wire-format dict into a typed `AgentConfig`. The `SPECS` registry maps every supported provider name → Spec, including legacy providers needed to resume existing agents. `GET /api/providers` filters that registry through `NEW_SESSION_PROVIDERS`, so new agents/chats use the ACP-backed Claude/Codex runtimes while old `claude-code` / `codex` rows still validate and resume.
   Claude and Codex descriptors also publish `model_meta` so agent tiles can estimate session cost and context usage from public per-model metadata. Context windows are only populated where the provider-side limit is documented, because API windows and CLI/runtime windows do not always match. `model_meta` may also carry optional per-model option constraints such as Claude Fable's effort values/default; clients that do not understand those fields can ignore them.

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

### ACP runtimes (preferred seam for new providers)

Since STORY-033 (2026-06-12), providers that speak the [Agent Client Protocol](https://agentclientprotocol.com) skip step 3 entirely: one `AcpAdapter` (`infrastructure/agents/acp/adapter.py`) serves every ACP agent. It spawns the agent binary as a subprocess (argv wired per-provider in `infrastructure/agents/acp/providers.py`), speaks JSON-RPC/ndjson over stdio via the official `agent-client-protocol` PyPI SDK, and maps `session/update` notifications onto the `AgentEvent` union in `infrastructure/agents/acp/mapping.py`. ACP/pydantic types never leave `infrastructure/`.

ACP subprocesses run in their own process group on POSIX so reconnect/close kills wrapper children too (`codex-acp` often runs under npm/node and launches the real binary plus MCP servers). This prevents orphaned provider subprocesses from surviving after transport recovery.

Current ACP providers: `claude-acp` (npx `@agentclientprotocol/claude-agent-acp`, maintained by Anthropic+Zed+JetBrains), `codex-acp` (npx `@zed-industries/codex-acp`), `opencode` (local `opencode acp`). The first two **coexist** with the bespoke `claude-code` / `codex` adapters until parity is validated; removing the legacy pair is a deliberate follow-up. Amp has no ACP path and stays bespoke.

Per-provider knobs travel as ACP *session config options*: each config subclasses `AcpAgentConfig` and implements `acp_config_values()` / `acp_mode_id()` (`domain/agents/configs.py`). Application is **tolerant** — option ids/values the agent doesn't advertise are skipped at debug level, never a start failure, because wrapper option surfaces drift faster than Atelier ships. Advertised mutable options are also emitted as `session_config_options` events and can be changed later through the `session_config` WS action; the adapter validates against the advertised values and emits `session_config_changed` on success. Successful agent model changes and agent/chat provider-option changes are persisted back to the row and JSON, with ACP's generic `effort` id mapped to the provider's stored `thinking_effort` or `reasoning_effort` option when applicable. The `session_config_refresh` WS action re-applies the current provider value and re-emits the returned full option list, so clients can refresh choices after external auth/config changes without starting a new agent. Captured real payloads live in `backend/tests/fixtures/acp/` and back the unit tests; re-capture them when bumping a pinned wrapper version.

Notable mechanics, with pointers:

- **Permissions** are protocol-native: `session/request_permission` carries agent-named options (`allow_once`/`allow_always`/`reject_once`/`reject_always`); the adapter maps Atelier's three-way decision onto them and answers `cancelled` after a stop (`adapter.py:request_permission`). Permission labels prefer the logical provider tool name remembered from `session/update`; ACP titles can be human action labels such as a search query, so they are only a fallback. No socket bridges or approval RPC shims.
- **System prompt**: ACP has no system-prompt parameter; the persona/brief is prepended to the first prompt of a fresh session as an `<atelier-context>` block (resumed sessions already carry it in-history).
- **Usage**: `usage_update` streams context fill (`used`/`size` → ctx %) and cumulative USD cost (→ `TurnMetrics.cost_usd`, authoritative over FE token-math); `PromptResponse.usage` supplies per-turn token splits. codex-acp reports fill but not splits/cost.
- **Restore**: `session/load` (replay suppressed — Atelier's transcript already has those turns) → `session/resume` → fresh session with a warning event, in that capability order.
- **Detach/catch-up**: claude-acp/codex-acp session ids are the native CLIs' ids, so `cli_launcher/claude_acp.py` + `codex_acp.py` just translate option vocabularies and the existing `cli_transcript` readers apply. OpenCode resumes via `opencode --session <sid>` and catches up through `opencode export` (`cli_transcript/opencode.py`) — no private DB parsing.
- **Compaction**: summary-only ACP sessions auto-reject every permission request; claude-acp additionally pins `plan` mode, codex-acp `read-only`, opencode `plan` (`compaction_sessions.py:_summary_config`).
- **OpenCode model behavior**: `OPENCODE_CONFIGURED_MODEL` remains the descriptor default so new-agent creation can use the user's OpenCode default. `GET /api/providers/opencode/models?refresh=true` shells out to `opencode models --refresh` and lets creation surfaces append the user's connected provider models before the session starts. Once the ACP session starts, OpenCode's advertised `model` config option drives the live picker; opening the picker sends `session_config_refresh`, selecting a value sends `session/set_config_option` for future turns, and the selected model is persisted on the agent row/`agent.json` so detach/resume stays aligned.
- **Known gaps** (smoke-verified 2026-06-12): codex-acp wraps tool args/results in internal envelopes, so canonical args degrade to title + kind + structured diff (the FE diff viewer still renders); codex-acp reports no token splits/cost.

## AgentEvent union

Frozen variants in `domain/agents/events.py`: `MessageDelta`, `MessageComplete`, `ThinkingDelta`, `ThinkingComplete`, `ToolCall`, `ToolCallUpdate`, `ToolResult`, `PlanUpdate`, `ModeChange`, `SessionConfigOptions`, `SessionConfigChanged`, `StatusChange`, `ArtifactMarker`, `Error`, `TurnMetrics`, `SessionEstablished`, `ProviderContextCompacted`, plus `UserInput` (originating from the WS input channel, not the adapter).

STORY-033 extended the union **additively** for ACP granularity: new variants (`PlanUpdate` — full-replacement plan entries; `ToolCallUpdate` — coalesced mid-flight tool status/locations; `ModeChange`; `SessionConfigOptions` / `SessionConfigChanged` — mutable provider controls such as OpenCode's model) and new optional fields (`ToolCall.kind/title/locations`, `ToolResult.diff`, `PermissionRequest.options/tool_id`, `TurnMetrics.cost_usd`). The serializer (`domain/supervisor/service.py:_OMIT_WHEN_NONE`) drops the optional keys when unset, so events from pre-ACP adapters stay byte-identical, legacy transcripts replay unchanged, and old frontend builds never see unknown keys (`tests/unit/domain/agents/test_events_compat.py` locks this in).

Each has a `Literal` `type` discriminator; the frontend pattern-matches on it.

**`ts` is set by the adapter; `seq` is set by the supervisor.** The adapter contract test asserts monotonic `ts`. The supervisor stamps the monotonic `seq` when it appends to the transcript log — so consumers can resume from `?cursor=N`.

### `TurnMetrics` token semantics

`TurnMetrics` carries two flavours of token counts, and they aren't interchangeable. `input_tokens` / `output_tokens` / `cache_read_input_tokens` / `cache_creation_input_tokens` are **cumulative** across every model sub-call in a turn — a turn that fires 20 tool-uses makes 20 API calls and the SDK's `ResultMessage` aggregates them. Summed across turns these equal what Anthropic billed, so they drive **session cost**.

`last_prompt_tokens` is named misleadingly: it's the prompt size of the *last* sub-call, but because each sub-call's prompt replays the entire conversation history (system + every prior user/assistant/tool-use/tool-result + this turn's new user msg + any in-turn tool round-trips), that value equals the **total context currently in the model's window** for that call. This is the "should I /clear?" number, used for the **ctx %** badge. Don't sum it across sub-calls or across turns; it's a snapshot. Most providers grow this value steadily during a session, but Codex app-server can compact provider-owned prompt context automatically; when that happens the Codex adapter emits `provider_context_compacted` and the next snapshot may drop sharply while the local Atelier transcript remains unchanged. If a provider reports an effective runtime `context_window` on the turn, the FE uses it over static `model_meta` because CLIs can reserve part of the API window. The supervisor also enriches live `turn_metrics` with optional `git_branch` / `git_head` / `git_detached` by calling the domain `WorktreeManager.describe_state(workdir)` port; adapters stay provider-focused and do not shell out to git. See `domain/agents/events.py:TurnMetrics` for the full docstring; the FE picks it up in `frontend/src/AgentTile.tsx` (`latestMetrics` → `TurnMetricsBar`).

### Canonical tool shape

`ToolCall.name` and `ToolCall.arguments` follow a single canonical shape regardless of which provider SDK emitted the call. Each adapter calls `infrastructure.agents.tool_canonical.canonicalize_tool(name, raw_input)` before yielding `ToolCall` and `PermissionRequest` so provider quirks (Amp's `cmd`/`edit_file`/`old_str` vs Claude Code's `command`/`Edit`/`old_string`) never leak into `domain/` or the frontend renderer. The canonical concepts are `Bash`, `Edit`, `MultiEdit`, `Read`, `Write`, `Grep`, `Glob` — see the `ToolCall` docstring for required/optional keys per concept. Tools without a canonical concept pass through with their raw shape; the frontend falls back to a generic JSON view. Existing on-disk transcripts can be migrated with `scripts/migrate-transcripts.py` (idempotent).

## AgentSupervisorService

`domain/supervisor/service.py`. The supervisor is the traffic cop sitting between the browser, the agent SDK, and the on-disk transcript. There is **one `asyncio.Task` per running agent** ("the agent task"), pumping that agent's adapter event stream. The supervisor is single-subscriber: at most one WS connection per agent; a second `subscribe()` replaces the slot and kicks the older subscription so stale sockets reconnect instead of accepting input without receiving live events.

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
3. **For eager starts, `await adapter.start(context)`** — per-adapter setup: Amp stands up the permission Unix socket; Claude connects its SDK control channel; Codex starts app-server state. The CLI/model turn is NOT spawned here.
4. **For eager starts, spawn the events-pump task** — `asyncio.create_task(self._run_agent(state))`. Iterating `adapter.events()` is what actually fires the underlying turn loop. **Skipped when `lazy=True`** — the first `send_input` starts the adapter and creates the task instead.

State is deliberately inserted before eager `adapter.start` finishes so StrictMode/two-tab/concurrent resume races converge on one registered adapter. The state carries a readiness gate: `subscribe`, `send_input`, `stop_turn`, `resolve_permission`, and lazy catch-up wait for registration-time setup to complete. Lazy reattach is view-only: it publishes stale permission denials and replays disk without touching provider transport. The first lazy `send_input` starts the adapter under a per-agent lock, then creates `_run_agent`, so no path can call `events()` while `start()` is still in flight.

`_run_agent` is the consumer side: `async for event in adapter.events()`. For `SessionEstablished` events it calls `_set_session_id` (the WorkStore hook) so the SDK's session/thread ID gets persisted to SQL — the canonical resume handle. For `TurnMetrics`, it adds the current worktree branch/head from the injected worktree-state reader before publishing, so each completed live turn carries the git prompt label without provider-specific adapter code. This supervisor hot path intentionally does not rewrite `agent.json`; command-driven session replacement flows opt into that filesystem mirror when they need user-visible lineage. Every event gets `_publish`-ed.

`is_registered(agent_slug) -> bool` lets callers (`connect`, `resume`) check whether to attach to existing state or rebuild the adapter; replaces an earlier side-channel that returned the work slug for the same purpose.

#### The four async commands feeding the supervisor

There is **one inward path** from the WS endpoint into domain logic:

- **`agents/start.execute`** — REST `POST /api/agents`. Allocates the row, builds the adapter, calls `register_agent` **eagerly** (default), and (if contexts produced a synthesised first message) sends it. Fresh agents have no prior provider session to fork from, so eager is fine.
- **`agents/connect.execute`** — WS `/api/agents/{slug}/stream`. `@asynccontextmanager` that resolves the agent (calling `resume.execute` if the supervisor has no live state, or a CLI catch-up pass if the agent is lazy-registered) and yields a `Subscription`. The WS handler is `async with connect.execute(...) as sub`.
- **`agents/resume.execute`** — Re-attach. Rebuilds the adapter from the persisted row, runs the detach catch-up merge if `status==DETACHED`, and calls `register_agent(..., lazy=True)`. **Lazy** because Amp's `--execute --stream-json` forks on resume — the SDK only spawns once the user actually types.
- **`agents/handle_user_action.execute`** — Inbound from the WS receive loop. Parses the JSON frame into a typed `UserAction` (`SendInput`, `StopTurn`, `ResolvePermission` from `domain/agents/user_actions.py`) and `match`-dispatches to the corresponding supervisor method. The WS receive loop is five lines.

#### Re-attach (resume) and lazy spawn

When `connect` finds the supervisor has no live state — backend restart, agent closed-to-rail, or `status=DETACHED` — it calls `resume.execute`. Resume runs `register_agent(..., lazy=True)`: the adapter and events pump are **not** started. The user sees the existing transcript via the replay window. Only when they type does `send_input` start the adapter, create the pump task, and spawn provider work. This is the fix for fork-on-resume and slow app-server reconnect bugs — a view-only re-attach no longer burns provider work.

If the agent is already lazy-registered, `connect` still runs a lightweight CLI catch-up before subscribing. This covers a user reopening Atelier once, continuing to type in the external CLI, then reopening again: new CLI transcript entries are appended to NDJSON and the supervisor's replay high-water mark is advanced before the WS replay window is computed.

### Lifecycle: stopping a turn / closing an agent

- **`stop_turn(agent_slug)`** writes a `user_stop` transcript line (so the user's intent is durable even if the SDK call fails), then `await state.adapter.stop_turn()`. Claude calls `interrupt()` over the SDK control protocol; Codex app-server sends `turn/interrupt` and then synthesizes an interrupted terminal turn if the server does not emit one, so queued follow-up prompts are not stranded behind the old turn; Amp no-ops today (its CLI exposes no per-turn cancel).
- **`stop_agent(agent_slug)`** pops the state from the registry, kicks any active subscriber so the browser reconnects/replays from disk, cancels the agent task, awaits it (suppressing `CancelledError`), and closes the adapter through the supervisor's bounded close helper (`ADAPTER_CLOSE_TIMEOUT_SECONDS`). Idempotent on the slug.
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

Detaching hands a running agent to a terminal CLI. `agents/detach.execute` stops the supervisor's task, flips status to `DETACHED`, writes a `user_detached` transcript marker carrying an `sdk_cursor` snapshot (Claude: timestamp; Amp: message count; Codex: JSONL line count), then shells out to the user's preferred terminal with the right resume command.

The resume command preserves the agent's selector + provider options so the CLI session keeps the user's choice instead of silently dropping to the local CLI default. `infrastructure/cli_launcher/build_resume_command` reads `agent.model` (Claude model id / Amp mode) and `agent.options` (the dict the Spec validated at create time, persisted on the row — see [Persisted provider options](#persisted-provider-options) below) and emits:

| Provider | Selector flag | Option flags |
|---|---|---|
| Claude | `--model <id>` | `--effort <level>` (skipped when `thinking_effort=="off"`); `--permission-mode <m>` (skipped when `permission_mode=="default"`, since the CLI applies that anyway) |
| Amp | `--mode <mode>` | `--dangerously-allow-all` when `permission_mode=="allow_all"`. Amp's other permission modes (`default`/`custom`) and `custom_allowed_tools` are Atelier-side constructs (the bridge) — they don't translate to CLI flags. |
| Codex | `--model <id>` | `--add-dir <resolved-share-root>` for mounted project shared folders when sandbox is `workspace-write`; `--sandbox <mode>` (skipped when `workspace-write`, the default); `--ask-for-approval <mode>` (skipped when `on-request`, the default); `-c model_reasoning_effort=<level>` (skipped when `medium`, the default) routed through Codex's TOML config override since the CLI has no dedicated reasoning-effort flag. The resume invocation is interactive `codex resume <sid>` plus the flags; `codex exec resume` is non-interactive and requires a prompt. |

Legacy agents whose `options` column is NULL (rows created before schema v9) detach with the bare `claude --resume <id>` / `amp threads continue <id>` / `codex resume <id>` shape — same behaviour as before the column existed. Unit tests in `tests/unit/infrastructure/cli_launcher/test_build_resume_command.py` pin every flag combination.

Re-attach runs through `resume.execute` → `_catch_up_detached_agent`:

- Read the SDK's transcript file/thread (Claude: `~/.claude/projects/<munged-cwd>/<sid>.jsonl`; Amp: `amp threads export <id>`; Codex: `~/.codex/sessions/YYYY/MM/DD/rollout-...-<sid>.jsonl`) starting from the `sdk_cursor`. Translate provider entries into `AgentEvent`-shaped dicts and append to NDJSON.
- If `agent.parent_session_id` is set and not already ingested, also export the parent session in full and emit a `sdk_session_merged` marker. Dedup is via `WorkStore.is_session_ingested`, which scans NDJSON for a `session_established` event (supervisor streamed the parent live) or a previous `sdk_session_merged` marker. Only depth-1 — the agent row stores one parent.
- Emit a `user_reattached` marker carrying the advanced `sdk_cursor`; flip status back to `IDLE`. Future catch-up passes start from that newer cursor, so late CLI output after an earlier view-only reattach does not duplicate already-imported entries.

Provider-specific CLI behavior is kept in provider modules, mirroring the adapter layout: `infrastructure/cli_transcript/{claude,amp,codex}.py` own cursor/path/merge details. Claude owns the Anthropic message/block translator; Amp imports it because `amp threads export` currently uses a Claude-compatible envelope (`role`, `content`, `text` / `thinking` / `tool_use` / `tool_result`) for both Anthropic-backed and GPT-backed modes. Provider identity shows up inside block metadata (`provider: "anthropic"` / `"openai"`) and usage fields, not as a different outer transcript schema. Resume command construction follows the same pattern under `infrastructure/cli_launcher/{claude,amp,codex}.py`; terminal-window launching is isolated in `cli_launcher/terminal.py`.

`agents.parent_session_id` (schema v6) is set atomically inside `set_agent_session_id`: when the new sid differs from the current, the previous sid is captured as parent. This linked-list lineage exists so providers that fork on resume (Amp) can recover the original transcript from the orphaned ancestor.

### Context compaction

`POST /api/agents/{slug}/compact` is a same-agent, same-worktree session replacement. The route delegates directly to `domain/commands/agents/compact.py`. The command stops the supervisor state, appends live `compaction_progress` markers for summarizing / starting a fresh session / linking the old session when a websocket is attached, summarizes the Atelier transcript plus `WorktreeManager.describe_state(workdir)`, writes `agents/<slug>/compactions/<timestamp>.md`, starts a fresh provider session through the domain `CompactionSessionClient` port, writes a breadcrumb into the old provider session best-effort, persists the new `agents.session_id` plus parent lineage through `WorkStore.set_agent_session_id(..., mirror_agent_json=True)`, and appends compaction boundary events to `transcript.ndjson`. Before re-registering, it stops the supervisor again to evict any lazy websocket reconnect that raced the long-running compaction window, so replay seeds from the final `context_compacted` marker. `GET /api/agents/{slug}/compactions/{filename}` delegates to `domain/commands/agents/read_compaction_summary.py` so the UI can show the saved seed summary without accepting arbitrary filesystem paths.

Provider mechanics stay behind `infrastructure/agents/compaction_sessions.py`, which uses the normal `AgentAdapter` factory for Claude, Amp, and Codex. The command does not mutate provider-owned history in place; Atelier's append-only transcript remains canonical, and `parent_session_id` captures the previous provider session when `set_agent_session_id` swaps to the new one.

Summary-only provider runs reject all tools. If a provider still emits an attempted `ToolCall` before recovering from the rejection, the private collector ignores that attempted call and keeps waiting for assistant text. Any non-empty provider summary is preserved as the seed summary; provider `Error` events, timeout, or empty summaries fall back to the app summarizer.

`POST /api/chats/{slug}/compact` follows the same provider maintenance pattern for exploratory chats. The route delegates to `domain/commands/chats/compact.py`, which stops `chat_supervisor`, appends the same `compaction_progress` phase markers when possible, summarizes the chat transcript, writes `chats/<slug>/compactions/<timestamp>.md`, starts a fresh provider session, writes a best-effort breadcrumb into the old session, updates `chats.session_id`, and appends a `context_compacted` boundary. It stops the chat supervisor again before returning so any websocket reconnect that raced compaction is evicted and the next stream replays from the final boundary. `GET /api/chats/{slug}/compactions/{filename}` reads the saved summary through `ChatStore` by scoped filename.

### How the SDK adapters fit in

Each adapter implements the `AgentAdapter` Protocol from `domain/agents/ports.py`:

| Method | When called | What it does |
|---|---|---|
| `start(context)` | once, by `register_agent` | Cheap per-adapter setup: Amp's permission socket, Claude's SDK control channel. Does **not** spawn the CLI subprocess. |
| `events()` | once, by `_run_agent` (the events-pump task) | Async generator. Emits normalised `AgentEvent`s in the order the SDK produces them. **First iteration** is what spawns the underlying CLI — so the lazy-resume path (which skips creating the pump task) keeps the SDK dormant. |
| `send_input(text)` | by `send_input` (also called by `start.execute` to inject a synthesised first message) | Pushes a turn into the adapter's input channel. |
| `stop_turn()` | by `stop_turn` | Cancel the in-flight turn without tearing down the session. |
| `resolve_permission(rid, decision)` | by `resolve_permission` | Answer a `PermissionRequest` the adapter previously emitted. |
| `close()` | by `stop_agent` / `shutdown` | Disconnect from the SDK. Idempotent; the supervisor bounds the await so a wedged provider transport cannot pin cleanup. |

Adapters whose SDK doesn't expose a feature no-op the corresponding method (Amp's `stop_turn`, `resolve_permission`; the stub's everything-but-events). The supervisor calls them uniformly so its own code doesn't branch on provider.

Amp `ErrorResultMessage` is treated as terminal for the adapter pump. The adapter still emits the provider `Error`, final `TurnMetrics`, and idle status, then ends `events()` so the supervisor evicts the registered process and kicks the websocket into the normal reconnect/resume path. This avoids accepting the next user input into an Amp CLI whose stream-json-input handler has already closed.

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
- Amp may attach ``discoveredGuidanceFiles`` to tool results when it follows repository guidance references. ``AmpAdapter`` preserves each file's URI, line count, and omitted byte count, but strips the file body before persisting the normalized ``ToolResult`` so transcripts and future compaction prompts do not balloon from provider-injected guidance payloads.

The bridge itself (``infrastructure/agents/amp_permission_bridge.py``) is stdlib-only — it ships in the source tree but runs as a detached child of the Amp CLI, so it must not import any Atelier modules (the CLI's invocation env doesn't carry our virtualenv).

### Tool permissions for Codex: app-server approval callbacks

Codex has a native approval-policy concept. Atelier runs live Codex agents through ``codex app-server --listen stdio://`` so Codex's JSON-RPC approval requests flow back into the same ``PermissionRequest`` UI Claude and Amp use. ``_CodexAppServerClient`` handles ``item/commandExecution/requestApproval``, ``item/fileChange/requestApproval``, and ``item/permissions/requestApproval``, maps them to domain-level tool names/input, waits for ``resolve_permission``, then replies to Codex with ``accept`` / ``acceptForSession`` / ``decline``-style decisions.

``CodexAdapter._handle_approval_request(request)`` is still the provider-neutral callback seam: it canonicalises the tool name + input, publishes a ``PermissionRequest``, waits for ``resolve_permission``, and returns Atelier's ``allow`` / ``allow_always`` / ``deny`` decision. The app-server client maps those domain decisions to Codex JSON-RPC responses. The legacy ``_CodexSdkClient`` remains for compatibility with tests/older SDK experiments, but its ``exec --experimental-json`` transport cannot surface approvals.

Codex app-server interrupts are also normalized at the adapter boundary. If ``turn/interrupt`` returns but the app-server does not send a terminal ``turn/completed`` notification (observed when stopping a long-running shell command), ``_CodexAppServerTurnHandle`` injects an interrupted terminal notification for the current turn. That lets the normal conversion path publish idle/metrics and lets the input pump consume the next queued user prompt without requiring a browser refresh.

Codex app-server can also compact its own prompt context automatically. Atelier maps the app-server ``thread/compacted`` notification and ``contextCompaction`` item into ``ProviderContextCompacted`` (``provider_context_compacted`` on the wire). This is informational only: it has no Atelier summary file, does not replace ``agents.session_id``, and exists so the UI can explain a sudden context-usage drop.

Two layers sit on top of Codex execution:

- **``CodexSandbox``** — OS-level filesystem gating (``read-only`` / ``workspace-write`` (default) / ``danger-full-access``). Forwarded as ``sandbox`` to app-server and ``--sandbox`` on detach-to-CLI; Codex enforces it before approval policy can help.
- **``CodexApprovalMode``** — Codex's own ask policy. ``on-request`` is the default, ``never`` auto-runs everything, and ``untrusted`` asks Codex to prompt on every non-trusted tool. In live Atelier sessions, those prompts now round-trip through the WS permission frame.

Atelier's worktree (``~/Atelier/works/<slug>/worktrees/<agent>/``) is the primary writable root for ``workspace-write``. Project shared folders are symlinks whose resolved targets live outside that worktree, so start/resume/detach collect mounted share targets into ``CommonAgentConfig.writable_roots`` and forward them as Codex app-server ``sandbox_workspace_write.writable_roots`` / CLI ``--add-dir`` values. Git worktrees also keep mutable branch/index metadata in the source repo's shared ``.git`` directory; ``WorktreeManager.sandbox_writable_roots(workdir)`` adds that git common dir when it lives outside the worktree so Codex agents can run normal branch commands such as ``git switch -c``. These narrow additions keep shared-folder writes and git branch creation working without switching the whole agent to ``danger-full-access``.

### Codex runtime seam

Production wires ``_CodexAppServerClient`` via ``_default_client_factory``. Tests inject a fake factory matching the local ``CodexClient`` / ``CodexThread`` / ``CodexTurnHandle`` Protocols — see ``tests/unit/infrastructure/agents/test_codex_adapter.py`` for the fixture set. Same shape as Amp's ``executor`` DI seam.

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
                ├── compactions/          ← same-agent provider-session summaries
                ├── context.md            ← index (sections per type, links to files)
                └── context/              ← per-source files
                    ├── text-1.md
                    ├── url-1.md
                    └── jira-ENG-3421.md
└── chats/
    └── <chat_slug>/
        ├── chat.json
        ├── transcript.ndjson
        └── compactions/                  ← chat provider-session summaries
```

**Filesystem is canonical.** SQLite is treated as a derived index. The `WorkStoreService` and `ProjectStoreService` each write DB first then FS within a service-level `threading.RLock`; a crash between the two leaves an orphan DB row, and startup `reconcile` (`domain/workstore/reconcile.py`, `domain/projectstore/reconcile.py`) repairs it: delete DB rows whose FS dir is gone; restore DB rows from `work.json` / `project.json` / `agent.json` if the FS has them but DB doesn't; FS wins on any field conflict.

**Reconcile order matters.** `reconcile_projects(repo, files)` runs **before** `reconcile_works` in the FastAPI lifespan startup hook because `works.project_slug` is a slug FK to `projects.slug`; if a work's project hasn't been inserted yet, the work upsert violates the FK. The order is fixed in `main.py`'s lifespan (look for the comment "Projects reconcile FIRST"). Same rule will apply to any future cross-store reference.

The reconcile invariant is the AC for STORY-005 — see `tests/integration/test_workstore_e2e.py` for the round-trip guarantees. Project-side reconcile is unit-tested with stubs in `domain/projectstore/reconcile.py`.

### Persisted provider options

The `agents.options` column (schema v9, JSON-encoded TEXT, NULLABLE) stores the dict that the provider Spec validated at create time — `{permission_mode, thinking_effort, custom_allowed_tools, …}` minus whatever the user didn't set. `chats.options` mirrors the same shape for exploratory chats as of schema v16, populated by the new-chat permission/mode/effort selectors and by accepted live `session_config` changes. Two agent consumers read it:

- `agents/resume.execute` (`backend/src/domain/commands/agents/resume.py`) calls `SPECS[provider].build(common, agent.model, dict(agent.options or {}))`. Before v9 the third arg was `{}` and re-attach silently reset every option to its provider default — that's the latent drift this column closes.
- `agents/detach.execute` forwards `agent.options` into `build_resume_command` (see [Detach to CLI + catch-up](#detach-to-cli--catch-up)) so the CLI session inherits the same flags.

Backward compatibility: existing rows have `options=NULL` and `Agent.options=None` after deserialisation. Both consumers normalise that to an empty dict, so the legacy path is byte-identical to pre-v9 behaviour. `serialize_agent` only emits the `options` key when set, so old `agent.json` files round-trip through reconcile unchanged.

The wire format already had `NewAgentRequest.options: dict[str, Any]`; persistence is just plumbing it into the row instead of dropping it after Spec validation. Later `session_config` changes use the same optional dict, so legacy rows with `NULL` still mean "provider defaults."

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

**Client → server**: the WS receive loop parses each frame into a typed `UserAction` (`domain/agents/user_actions.py`) and forwards to `handle_user_action.execute`. Four action types:

- `{"type":"input","text":"..."}` → `SendInput` — appends a `user_input` transcript line and forwards to the adapter (creates the lazy pump if not yet running).
- `{"type":"stop"}` → `StopTurn` — appends a `user_stop` transcript line and calls `adapter.stop_turn()`. Claude interrupts mid-turn via the SDK's control protocol; Amp's adapter no-ops for now (the SDK exposes no per-turn cancel — full per-turn cancel for Amp is a tracked follow-up). The user-facing intent is always recorded.
- `{"type":"permission","request_id":"...","decision":"allow|allow_always|deny"}` → `ResolvePermission` — answers a `PermissionRequest` the adapter previously emitted. The decision values come from `get_args(PermissionDecisionValue)` so the wire and the domain stay in lockstep.
- `{"type":"session_config","config_id":"model","value":"provider/model"}` → `SetSessionConfigOption` — applies a provider-advertised mutable session option, starting a lazy adapter if needed. Unsupported ids/values are rejected by the adapter; successful changes are replayable through `session_config_changed`.
- `{"type":"session_config_refresh","config_id":"model"}` → `RefreshSessionConfigOptions` — asks a provider-advertised option to re-emit its full choice list, starting a lazy adapter if needed. ACP adapters implement this by re-applying the current value and publishing the returned `session_config_options`.

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
| 4409 | Adapter pump terminated mid-session (upstream rate limit, subprocess died, provider stream EOF) and `send_input` hit a dead transport. Frontend retries; the resume path rebuilds the adapter. Same recovery shape as 4408. |
| 1000/1001/etc. | Transient (network, server restart). Frontend retries with exponential backoff. |

**Resume on reconnect.** When the slug is in SQLite but the supervisor has no live state, `connect.execute` calls `resume.execute(...)` to rebuild the adapter (passing through the persisted `session_id`) and `register_agent(..., lazy=True)` to attach without firing the SDK pump. A normal replay-then-live then proceeds — the client doesn't need to know whether it's connecting to a fresh adapter, an in-flight one, or a freshly-resumed (lazy) one.

**Auto-eviction on pump end.** When `_run_agent`'s event loop returns for any reason (subprocess died, upstream error, provider stream EOF, ...), the supervisor evicts the agent's state from `_states` and closes the adapter (`service.py` → `_evict_after_pump_end`). Any live subscription's `kicked` event is set so the WS handler closes with 4409; the FE reconnects and lands in the resume path, which rebuilds a fresh adapter against the same `session_id`. Without this, the dead adapter lingered and the next `send_input` wrote to a closed stdin transport — surfaced to users as the cryptic `WriteUnixTransport closed=True` chain triggered by upstream 429s.

## WorktreeManager

`domain/worktrees/`. Provisions a per-agent workdir so each agent gets its own checkout without stepping on the user's main one. Operations: `ensure`, `ensure_forked`, `is_detached`, `describe_state`, `remove`, `sweep_orphans`. The implementation (`infrastructure/git/worktree_manager.py`) shells out to `git worktree` — a few subprocess calls beat pulling in gitpython.

**Layout** mirrors the architecture: `<workspace_root>/works/<work_slug>/worktrees/<agent_slug>/`.

**Fresh agents start detached from master.** `start.execute` calls `ensure(..., base_ref="master", branch_name=None)` for regular new agents, so a clean agent starts from the integration branch rather than whatever branch the user's source checkout currently has selected. The agent (or user) names a branch later via `git switch -c <name>`. The system prompt rendered for detached worktrees includes a guard: don't `checkout`/`switch` to another branch without first creating one from current HEAD — that's the only path that orphans commits. `system_prompt.render_system_prompt(..., is_detached_worktree=True)` injects the block; both `start.execute` and `resume.execute` derive the flag by calling `worktree_manager.is_detached(workdir)` so the truth in the prompt always matches the truth on disk. Handoff/forked starts skip the fresh base and use `ensure_forked(...)`, which starts at the source agent's current HEAD and overlays its modified/untracked working state.

**Opt-in named branch.** Regular agent start passes `base_ref="master"` with `branch_name="my-feature"`, so the named branch is created from master with the existing self-heal-on-collision behaviour (branch already exists → attach; stale registry entry → prune + retry). Surfaced via the optional `branch_name` field on `NewAgentRequest` / the New Agent dialog's "Branch" input; the branch picker (see `GET /api/git/branches`) lets the user pick from the source repo's existing branches.

**Pass-through for non-git folders.** If the work's `folder` is not a git repo, `ensure` returns the folder itself instead of trying (and failing) to create a worktree. The dialog hint already tells the user "If it's a git repo, agents will spawn worktrees here automatically." — non-repo folders keep working without forcing the user to convert them. `is_detached` returns `False` for non-git folders so callers can use it as a soft hint without branching on the git-vs-not-git case.

**Removal escalates.** `git worktree remove` first; on dirty/locked, retry with `--force`; on still-failing, recursive `rmtree` plus `git worktree prune` to clean up the source repo's registry. A wedged worktree never blocks provisioning a fresh one. The teardown also best-effort-deletes the per-agent `atelier/<work>/<agent>` branch when one was created (no-op for detached worktrees).

**Orphan sweep on startup.** `main.py` lifespan walks every work, asks the workstore for live agent slugs, and tells the manager to remove worktree directories that don't match. This is the cleanup path for crashed runs and soft-deleted works. It satisfies the AC "deleting a Work removes them" via reconcile-style sweep rather than coupling the soft-delete command to git ops directly.

**Gitignored devtime artifacts get mirrored.** `git worktree add` only materialises tracked files, so a fresh worktree boots without `.venv` / `node_modules` and (load-bearing) without `.env*` files. `_symlink_devtime_artifacts` in `infrastructure/git/worktree_manager.py` symlinks two classes from the source repo into the worktree right after provisioning: **dirs** (`.venv` / `venv` / `node_modules`) and **files** (`.env`, `.env.local`, `.env.development[.local]`, `.env.production[.local]`). Top-level + one-level-deep scope, so monorepos with `backend/.env.local` + `frontend/.env.local` are covered. Symlinks (not copies) so source edits propagate live to every agent. Failures are logged and swallowed — a missing convenience symlink is a degradation, not a launch blocker. Without the env-file mirroring, pydantic raises on required fields at server start and Vite's compile-time `define` substitution silently bakes empty strings into the bundle.

**Wired into the `agents/start` command** (`domain/commands/agents/start.py`). The route stays thin: parse the request, `await start.execute(...)`. The command validates the provider config first (via `Spec.build`) so a bad model can't allocate an agent row + worktree we'd have to roll back, then pre-fetches connection-backed contexts before any side effect, then `register_agent` (eager) and an optional first-message send. Domain errors: `WorkNotFound` → 404; `InvalidProviderConfig` / `AgentFolderMissing` / `ContextFetchError` → 422.

## Artifact Recording

Adapters emit `ArtifactMarker` events for `record_pr`, `record_jira`, and `record_doc` tool calls, plus a fallback scan for `{"atelier_artifact": ...}` text markers. ACP-backed agents also echo that marker in the Atelier MCP acknowledgement so a resumed/generic `tool` frame with lost tool arguments can still be recovered from the completed tool output. The supervisor records those markers through `domain/agents/artifacts.py`, which validates the payload and calls `WorkStore.record_artifact`. Recording is idempotent by work-scoped artifact identity: PR/Jira artifacts use `url`; doc artifacts use resolved `doc_path`. `WorkStore.list_artifacts_for_work` applies the same de-dupe on read so legacy duplicate rows do not render twice in the left rail.

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

## Known upstream workarounds

Patches we ship against third-party libs because the upstream fix isn't in yet. **Check these periodically** (≈ every couple of `uv sync` bumps) — when upstream lands the fix, drop the patch + raise the floor in `pyproject.toml`.

| Patch | Lib + version | Symptom we worked around | Upstream status |
| --- | --- | --- | --- |
| `backend/src/infrastructure/agents/_amp_sdk_patch.py` | `amp-sdk == 0.1.5` | `amp_sdk.core._read_process_output` reads stdout via `proc.stdout.readline()` with the default 64 KiB `asyncio.StreamReader` buffer. A single tool-result JSON line >64 KiB (`rg -l` against a large tree, multi-MB `Read`, ...) raises `LimitOverrunError("Separator is not found, and chunk exceed the limit", ...)`, the SDK pump dies, and the agent's turn ends mid-flight. The patch bumps `proc.stdout._limit` to 64 MiB before the read loop. | _Report TBD_ — the package metadata only links `ampcode.com` (no public repo URL); file at `dev@ampcode.com` or via the Amp app's feedback channel. |
