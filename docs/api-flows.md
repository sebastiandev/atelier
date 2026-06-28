# API flow diagrams

One sequence diagram per public endpoint. The diagrams show what the router, command, and ports actually do — not exhaustive — so a reader can spot the right files without reading every layer.

Conventions used in the diagrams:

- **Router** = `application/http/routes/*.py` or `application/ws/*.py` (thin glue per CLAUDE.md).
- **Command** = `domain/commands/**/*.py` (the orchestration unit each route delegates to).
- **WorkStore** = the SQL+FS port composed in `domain/workstore/service.py`.
- **Supervisor** = `AgentSupervisorService` (one task per running agent).
- **Adapter** = the per-provider `AgentAdapter` impl (Claude / Amp / Codex / Stub).
- A box with double border denotes an *external* boundary (the browser, the SDK CLI subprocess, the keychain, the filesystem).

Where a flow has notable concurrency or queueing, look for the side note at the end of the section.

---

## `GET /api/health`

```
Browser ──► Router (health.py)
                │
                └─► returns {"status":"ok"}
```

Bare liveness probe. No persistence touched.

---

## `POST /api/artifacts/refresh-pr-statuses`

```
Browser ──► Router (artifacts.py)
                │
                └─► app.state.pr_status_poller.refresh_now()
                        │
                        ├─► throttle check (30s window) → ran=false
                        │
                        └─► refresh_pr_statuses.execute(workstore, fetcher)
                                │
                                ├─► WorkStore.list_non_terminal_pr_artifacts()
                                ├─► GitHubPrStateFetcher(ref, if_none_match=etag)
                                │       │
                                │       └─► api.github.com (304s skip rate-limit budget)
                                └─► WorkStore.update_artifact_status / .update_pr_artifact_etag
                Returns {ran, checked, updated, skipped, not_modified}
```

Fired from `WorkView` on mount when at least one tracked PR is non-terminal (`open`/`draft`), so a freshly-opened tab doesn't sit on up-to-5-min-stale data. The poller throttles to one actual refresh per 30s — bouncing between work tabs returns `ran=false` after the first call, and the FE only refetches the artifact list when `ran=true && updated > 0`. The ETag stored on each PR row drives `If-None-Match` so unchanged PRs return 304 (no rate-limit cost).

---

## `GET /api/update-status`

```
Browser ──► Router (update_status.py)
                │
                └─► reads app.state.update_check_poller.status
                    returns {available, repo_path, current_sha, latest_sha}
```

Returns the last successful snapshot from `UpdateCheckPoller` — a 2h background loop in `infrastructure/update_check/` that runs `git fetch` + compares local `HEAD` to `origin/main`. The route does no git work itself; on hosts where the poller hasn't completed a cycle yet (just-started backend, no network, no `.git`), it returns `available=false` with `repo_path` populated from the checker.

The frontend's topbar `UpdateChip` polls this every 10 minutes; the chip is hidden when `available=false`, and clicking it reveals a popover that recommends running `/update` inside Claude from `repo_path`.

---

## `GET /api/settings` / `PUT /api/settings`

```
Browser ──► Router (settings.py)
                │
                ├─► UserSettingsRepository.get() / put()
                └─► returns persisted scalars + tool descriptors
```

`editor`, `terminal`, `layout`, `accent_hue`, and `theme` are persisted in the singleton DB row. `editor_options` and `terminal_options` are backend-owned descriptors (`value`, `label`, `command`, optional `url_template`) returned with every read/write response so the Settings UI renders selectable tools from the backend rather than from a local catalog. The descriptor fields are additive; older frontends ignore them.

---

## `POST /api/fs/uploads/images`

```
Browser ──► Router (fs.py)
                │
                ├─► validate multipart file is png/jpeg/gif/tiff/webp by MIME or filename and <= 20 MB
                ├─► choose workspace or work-scoped attachments/images dir
                └─► write paste-<timestamp>-<uuid>.<ext>
                    returns {path, filename, content_type, size}
```

Used by composer paste handling. Work-scoped callers pass `work_slug` and receive a path under `~/Atelier/works/<WRK>/attachments/images/`; unscoped chats use `~/Atelier/attachments/images/`. Agent composers turn the returned path into a normal `file` context, while chat composers insert the path into the draft because chat websocket context frames are intentionally rejected.

---

## `GET /api/providers`

```
Browser ──► Router (providers.py)
                │
                └─► for name in NEW_SESSION_PROVIDERS:
                        spec = SPECS[name]
                        spec.describe()  →  ProviderDescriptor
                    return list
```

Same `Spec` instances back the create-agent validator (`spec.build`) so the descriptor and validator can't drift. The public response is filtered to providers available for new sessions; legacy providers can stay in `SPECS` for existing agents without appearing in the modal. The response carries `primary_field` + `options` (enums) + `text_options` (free-form fields like Amp's custom allowlist) + `advanced_intro` (explainer copy).

The new-session order is `claude-acp`, `amp`, `codex-acp`, `opencode`; the ACP-backed Claude/Codex descriptors use the familiar user-facing labels. OpenCode's descriptor default is the sentinel `configured-default`; creation surfaces can call `GET /api/providers/opencode/models?refresh=true` to append the user's connected OpenCode models before launch, and once the ACP session starts advertised `model`/effort configOptions power the live selectors in agent/chat composers. See `docs/backend.md` → "ACP runtimes".

## `GET /api/providers/opencode/models`

```
Browser ──► Router (providers.py)
                └─► opencode models [--refresh]
                    parse provider/model lines
                    return [{ value, label }]
```

Used by New Agent and New Chat when OpenCode is selected. Failure returns `503` so the UI can keep the `OpenCode default` fallback rather than blocking creation.

---

## `GET /api/chats`

```
Browser ──► Router (chats.py)
                │
                ├─► ChatStore.list_chats()
                │       └─► SQL index + ~/Atelier/chats/<CHT>/transcript.ndjson
                └─► optional project_slug/work_slug filter
                        └─► scope by Project/Work link/provenance
                returns list[ChatSummary]
```

Unscoped listing powers Home and only returns chats not assigned to a project or work (`grounding` unset/folder and not promoted). Project scope returns chats linked at that project level (`grounding.kind == "project"`) that have not been promoted into a work. Work scope returns chats linked to the work plus chats promoted into that work. `working_directory` is independent and does not affect list scope.

---

## `PATCH /api/chats/{slug}` / `DELETE /api/chats/{slug}`

```
Browser ──► Router (chats.py)
                │
                ├─► PATCH title
                │       └─► commands.chats.rename.execute(...)
                │               └─► ChatStore.rename_chat()
                │                       ├─► SQL chats.title / updated_at
                │                       └─► ~/Atelier/chats/<CHT>/chat.json
                │
                └─► DELETE
                        └─► commands.chats.delete.execute(...)
                                ├─► chat_supervisor.stop_agent(CHT)
                                └─► ChatStore.delete_chat()
                                        ├─► remove ~/Atelier/chats/<CHT>/
                                        └─► delete SQL row
```

Rename is metadata-only and leaves provider sessions untouched. Delete is
irreversible and stops any live chat runtime before removing transcript files.

---

## `POST /api/chats` / `POST /api/chats/{slug}/messages`

```
Browser ──► Router (chats.py)
                │
                ├─► SPECS[provider].build(model, options)  ← 422 on bad config
                ├─► ChatStore.create_chat(req)
                │       ├─► repo.add_chat(chat)       ← assigns CHT-NNN
                │       ├─► files.write_chat_json(...)
                │       └─► files.append_transcript_event(first user message)
                └─► returns ChatDetail
```

Create-chat records only the modal's first user message plus optional Project/Work link (`grounding`), optional provider cwd (`working_directory`), and any non-default provider permission/mode options. The provider turn starts when the chat websocket opens and `chats/connect.py` claims that first prompt (see [WS `/api/chats/{slug}/stream`](#ws-apichatsslugstream)). `POST /api/chats/{slug}/messages` remains as a compatibility append route for older clients; the current frontend sends follow-up turns over the websocket.

---

## WS `/api/chats/{slug}/stream`

```
Browser ──► WS Router (application/ws/chats.py)
                │
                └─► chats/connect.execute(...)
                        │
                        ├─► ChatStore.get_chat(slug)          ← 4404 if missing
                        ├─► if not registered:
                        │       ├─► resolve cwd from working_directory/link defaults
                        │       ├─► SPECS[provider].build(...)
                        │       ├─► build_adapter(config)
                        │       └─► chat_supervisor.register_agent(..., lazy=True)
                        ├─► ChatStore.claim_initial_prompt()
                        │       └─► append chat_initial_prompt_delivered marker
                        ├─► chat_supervisor.send_input(first, record_user_input=False)
                        └─► async with chat_supervisor.subscribe(slug, cursor)
                                ├─► replay legacy rows as AgentEvent-shaped frames
                                └─► live provider events
```

Inbound frames mirror the agent stream for the chat-safe subset: `input` writes a `user_input` line and forwards to the adapter, `stop` writes `user_stop` and calls `adapter.stop_turn()`, and `permission` resolves any pending provider permission. Context attachment frames are rejected with a `client_error` frame because chats do not own agent context folders. The separate `chat_supervisor` writes to `~/Atelier/chats/<CHT>/transcript.ndjson` through `FsChatTranscriptLog` and persists provider session ids to `chats.session_id`. If `working_directory` is set it is used as cwd/writable root; otherwise Work/Project links use their Atelier metadata folders, while legacy folder-grounded chats use that folder as cwd.

---

## `POST /api/chats/{slug}/promote`

```
Browser ──► Router (chats.py)
                │
                ├─► ChatStore.get_chat(slug)                 ← 404/409 guards
                ├─► build context.md from confirmed brief + transcript metadata
                ├─► WorkStore.create_work(from_chat, chat_context_folders)
                │       ├─► repo.add_work(work)
                │       ├─► files.write_work_json(... from_chat ...)
                │       ├─► files.write_brief(...)
                │       └─► files.write_work_chat_context_file(...)
                └─► ChatStore.mark_promoted(chat, work_slug)
                returns WorkDetail
```

The full transcript stays at `chat://CHT-NNN` / `~/Atelier/chats/<CHT>/transcript.ndjson`; the promoted Work receives only the summary/action context file under `~/Atelier/works/<WRK>/chat-contexts/<folder>/context.md`.

---

## `POST /api/works/{work}/chats/{chat}/context`

```
Browser ──► Router (chats.py)
                │
                ├─► ChatStore.get_chat(chat)                 ← 404 guard
                ├─► WorkStore.get_work(work)                 ← 404 guard
                ├─► build context.md from chat summary + transcript metadata
                └─► WorkStore.ensure_work_chat_context(...)
                        ├─► reuse existing chat_context_folders entry
                        └─► otherwise append metadata + write context.md
                returns WorkChatContextFolderSummary
```

Used by WorkView's chat tile "start agent from chat" path. The returned `absolute_path/context_filename` is passed into `NewAgentDialog` as a normal `file` context, so agent creation reuses the existing context renderer and first-message pointer.

---

## `GET /api/works`

```
Browser ──► Router (works.py)
                │
                ├─► WorkStore.list_works()
                │       │
                │       └─► WorkRepository.list_works()
                │
                └─► WorkStore.count_children_by_work_id()
                        │
                        └─► WorkRepository.count_children_by_work_id()
                            ╔═══════════╗
                            ║ atelier.db║
                            ╚═══════════╝
                Router joins each Work with counts.get(w.id) → WorkSummary
                                            (agent_count, artifact_count)
```

Soft-deleted works are filtered out at the service layer. SQL is the index; the canonical state is `work.json` on disk, but listing works only reads the index. Counts come from two `GROUP BY work_id` queries on the agents and artifacts tables — missing work ids default to zero on both axes; the router does the join in-memory.

---

## `POST /api/works`

```
Browser ──► Router (works.py) ──► WorkStore.create_work(req)
                                       │
                                       ├─► repo.add_work(work)        ← assigns id+slug
                                       ├─► files.ensure_work_dir(slug)
                                       ├─► files.write_work_json(slug, …) ← contexts go here
                                       └─► files.write_brief(slug, description)
                                       └─► returns WorkRecord(work, contexts)
                                Router formats WorkDetail (Pydantic)
```

DB-first ordering: the repo commits before the FS write. A crash between the two leaves an orphan DB row, which startup `reconcile` heals against the canonical `work.json`. **Contexts live FS-only** (in `work.json`); not on the SQL row.

---

## `GET /api/works/{slug}`

```
Browser ──► Router ──► WorkStore.get_work(slug)
                            │
                            ├─► repo.get_work_by_slug(slug)
                            └─► files.read_work_json(slug)  ← contexts come from here
                            └─► returns WorkRecord(work, contexts) | None
                        Router formats WorkDetail
```

Returns 404 when the work is missing or soft-deleted.

---

## `PATCH /api/works/{slug}`

```
Browser ──► Router ──► WorkStore.update_work(req)
                            │
                            ├─► repo.upsert_work(existing)        ← name/description/status
                            ├─► files.write_work_json(slug, …)    ← merged contexts
                            └─► files.write_brief(slug, …)        ← only if description changed
                       returns WorkRecord
```

Partial update: any field left as `None` in the request is preserved.

---

## `DELETE /api/works/{slug}`

```
Browser ──► Router ──► WorkStore.soft_delete_work(slug)
                            │
                            ├─► repo.upsert_work(existing with status="deleted")
                            └─► files.write_work_json(slug, …)    ← status flips on disk too
```

Soft delete: the row + folder stay, status flips to `deleted`. List endpoints filter them out.

---

## `POST /api/works/{slug}/reveal`

```
Browser ──► Router (works.py)
                │
                ├─► WorkStore.get_work(slug)              ← 404 if missing
                ├─► paths.work_dir(slug).mkdir(exist_ok)  ← idempotent
                └─► open_in_file_browser(target)
                                            ╔════════════════╗
                                            ║ Finder/Files/… ║
                                            ╚════════════════╝
                204 No Content
```

Slug → path is server-computed (defends against path injection); the work must exist before we'll pop a Finder window. `mkdir(exist_ok=True)` makes reveal usable even on freshly-created works whose folder hasn't been written by anything yet. OS-level errors map to 500.

---

## `POST /api/works/{slug}/project`

```
Browser ──► Router (works.py) ──► commands.move_to_project.execute(workstore, projectstore, req)
                                       │
                                       ├─► WorkStore.get_work(slug)              ← 404 WorkNotFound
                                       ├─► if project_slug != null:
                                       │     ProjectStore.get_project(slug)      ← 422 ProjectNotFound
                                       └─► WorkStore.move_work_to_project(work, project)
                                              │
                                              ├─► repo.upsert_work(existing)  (project_slug field)
                                              └─► files.write_work_json(...)  (FS catches up)
                                       returns WorkRecord
                                  Router formats WorkDetail
```

Body: ``{"project_slug": "PRJ-NNN" | null}``. ``null`` re-parents to Loose (a first-class state, not a degenerate one). The route's a dedicated POST rather than a PATCH field because PATCH treats ``None`` as "leave alone" — which collides with the explicit "set to None for Loose" intent. Both DB and ``work.json`` are updated so reconcile sees them in sync on next startup.

---

## `POST /api/works/{slug}/complete`

```
Browser ──► Router (works.py) ──► commands.complete.execute(workstore, supervisor, worktrees, req)
                                       │
                                       ├─► WorkStore.get_work(slug)             ← 404 WorkNotFound
                                       ├─► validate status == "active"          ← 409 WorkNotActive
                                       ├─► WorkStore.list_agents_for_work(slug)
                                       ├─► for each agent:
                                       │     await supervisor.stop_agent(slug)  ← idempotent
                                       ├─► for each agent:
                                       │     worktree_manager.remove(work_slug, agent_slug)
                                       │                              ╔════════════╗
                                       │                              ║ git worktree║
                                       │                              ╚════════════╝
                                       └─► WorkStore.update_work(status="completed")
                                       returns CompleteWorkResult{work_slug, agent_count}
                                  Router formats CompleteWorkResponse
```

Status flips **last** so a crash mid-cleanup doesn't leave the work parading as "completed" while supervisor tasks or worktrees still hang on. Both `stop_agent` and `worktree.remove` are idempotent — replays after a partial run are safe. **Preserved**: `~/Atelier/works/<slug>/` (transcripts, agent.json, brief.md, handoff docs). **Removed**: per-agent git worktrees (scratch space). The completed work stays reachable through the Completed filter / project page.

---

## `GET /api/works/{slug}/agents`

```
Browser ──► Router (agents.py) ──► commands.list_for_work.execute(workstore, slug)
                                        │
                                        └─► WorkStore.list_agents_for_work(slug)
                                                │
                                                └─► repo.list_agents_for_work(slug)
                                        returns list[Agent]
                                   Router maps each to AgentSummary
```

404 if the work doesn't exist.

---

## `POST /api/works/{slug}/agents`

The fattest endpoint. Creates an agent row, provisions a worktree, renders contexts, builds a provider config, spawns the SDK adapter, registers it on the supervisor, and (if contexts were attached) injects the first-message context pointer.

```
Browser
   │  payload = {name, persona, role, provider, model, options, contexts,
   │             fork_from_agent?, branch_name?}
   ▼
Router (agents.py)
   │
   ▼
commands.start.execute(workstore, worktree_manager, settings, req)
   │
   ├─► WorkStore.get_work(slug)                        ← validate work exists + folder mkdir-able
   ├─► SPECS[provider].build(common, model, options)   ← validate options before allocating
   ├─► WorkStore.add_agent_to_work(req with contexts)  ← persists agent.json + contexts
   │       └─► repo.add_agent → assigns slug
   │       └─► files.write_agent_json(work, agent, … contexts …)
   ├─► WorkStore.render_agent_contexts(work, agent, contexts)
   │       └─► writes agents/<slug>/context/<files>.md
   │       └─► writes agents/<slug>/context.md  (index)
   │       returns abs_path | None
   ├─► WorktreeManager.ensure(work, agent, source, base_ref="master", branch_name=req.branch_name)
   │       └─► resolves base ref: `master` if present, otherwise `main` if present
   │       └─► branch_name=None  → `git worktree add --detach ... <base>`  (default)
   │       └─► branch_name="x"   → `git worktree add -b x ... <base>` with self-heal-on-collision
   │       └─► non-git folder    → returns folder unchanged
   │   (or WorktreeManager.ensure_forked(...) when fork_from_agent is set — source agent HEAD + working state)
   ├─► mount project shared folders and work chat-context folders into workdir
   │       └─► mounted roots flow into provider writable_roots / Codex --add-dir
   ├─► render_system_prompt(..., shares=mounted folders, is_detached_worktree=...)
   ├─► build_adapter(config, settings)                 ← singledispatch: Claude / Amp / Codex / Stub
   └─► returns StartAgentPlan(agent, adapter, context, first_message?)
                                     │
                                     ▼
Router ──► supervisor.start_agent(work, agent_slug, adapter, context, first_message)
                │
                ├─► seq = transcript_log.last_seq(...)        ← seed for monotonic resumes
                ├─► register _AgentState in self._states
                ├─► await adapter.start(context)              ← eager start only
                │                                                 Claude: connect SDK
                │                                                 Amp: open Unix permission socket
                │                                                 Codex: open app-server state
                ├─► if first_message is not None:
                │       supervisor.send_input(slug, first_message)  ← lands as user_input seq=1
                └─► task = asyncio.create_task(self._run_agent(state))
                              │
                              └─► async for event in adapter.events():
                                      _publish(state, event)  ← seq+fsync+queue under publish_lock

Router ──► returns AgentSummary
```

After the response: events stream from the adapter into the transcript and any subscribed WS subscriber. See [WS: `/api/agents/{slug}/stream`](#ws-apiagentsslugstream) for the consumer side.

Edge cases: missing `work_slug` → 404; bad model / unknown options → 422 (`InvalidProviderConfig`); folder `mkdir` failure → 422 (`WorkFolderMissing`).

---

## WS `/api/agents/{slug}/stream`

The supervisor → browser fan-out, plus the inbound input/stop/permission frames. Sequence depends on whether the supervisor has live state for the slug.

### Case A — agent is live in the supervisor

```
Browser ─── connect ?cursor=N ───► Router (ws/agents.py)
                                       │
                                       ├─► supervisor.get_work_slug_for(slug) → work_slug
                                       ├─► await websocket.accept()
                                       │
                                       └─► async with supervisor.subscribe(slug):  (atomic)
                                              │            ──► (from_seq, AgentSubscription{queue, kicked})
                                              │
                                              ├─► REPLAY: transcript_log.read_from_cursor(work, slug, N)
                                              │           filter seq ≤ from_seq
                                              │           websocket.send_json(event) for each
                                              │
                                              └─► LIVE: race three tasks
                                                    drain: queue.get() → ws.send_json
                                                    recv:  ws.receive_text() → parse → dispatch
                                                    kick:  sub.kicked.wait() → close(4408)
```

Atomicity (`subscribe` snapshots `from_seq` *under the publish lock* and registers the queue under the same lock) gives "no overlap, no gap": every event with `seq ≤ from_seq` is on disk and replayable; every event with `seq > from_seq` flows only through the queue.

The `recv` task dispatches inbound frames:
- `{"type":"input","text":"…"}` → `supervisor.send_input(slug, text)` (writes `user_input` line, forwards to adapter).
- `{"type":"stop"}` → `supervisor.stop_turn(slug)` (writes `user_stop` line, calls `adapter.stop_turn()`).
- `{"type":"permission","request_id":"…","decision":"allow|allow_always|deny"}` → `supervisor.resolve_permission(slug, rid, decision)` (delegates to `adapter.resolve_permission`, which completes the open future).

Anything else is ignored.

### Case B — supervisor lost state (backend restart, agent closed-to-rail)

```
Browser ─── connect ?cursor=N ───► Router
                                       │
                                       ├─► supervisor.get_work_slug_for(slug) → None
                                       ├─► WorkStore.get_work_slug_for_agent(slug)
                                       │       │
                                       │       ├─ None  → ws.close(4404)  ← truly unknown slug
                                       │       └─ work_slug → continue
                                       │
                                       ├─► commands.resume.execute(workstore, worktree_manager, settings, req)
                                       │       └─► reads agent row + session_id from SQL
                                       │       └─► rebuilds adapter via SPECS[provider].build(...)
                                       │       └─► returns ResumeAgentPlan(agent, adapter, context.session_id)
                                       │
                                       ├─► supervisor.register_agent(..., lazy=True)
                                       │       (view-only: no adapter.start, no provider pump)
                                       │       └─► first input starts the adapter with session_id
                                       │           and then creates the pump
                                       │
                                       └─► (continues as Case A: subscribe → replay → live)
```

The agent's transcript on disk and the SDK session on the provider side keep the conversation continuous across the restart. The supervisor's per-task seq seed is taken from `transcript_log.last_seq` so new events keep climbing without colliding with replayed history.

### Adapter event pumping (inside the agent task)

```
adapter.events()                          supervisor._run_agent
   │                                            │
   │  ◄─── state.adapter ─────                  │
   │                                            │
   ├─► (Claude) async for msg in receive_response(): convert → _outgoing
   │   side task: ─────────► _outgoing.put(AgentEvent)
   │                                ▲
   │  can_use_tool callback ────────┤  (Permission flow — see backend.md)
   │
   ├─► (Amp) async for msg in executor(...): convert → _outgoing
   │   bridge socket connection ─────► _outgoing.put(PermissionRequest)
   │   resolve_permission(rid, …) ◄─── future.set_result(decision)
   │                                ─► writes back to bridge over socket
   │
   └─► drain: _outgoing.get() → yield to events() → _publish(state, event)
                                                       ├─ seq stamp
                                                       ├─ append+fsync transcript.ndjson
                                                       └─ queue.put_nowait if subscribed
```

All three adapters use the same outgoing-queue + pump pattern so provider-side callbacks/bridges can interleave events with the provider message stream without blocking. Claude uses `can_use_tool`; Amp gates Bash through the bridge; Codex uses `codex app-server` JSON-RPC approval requests and maps them into Atelier `PermissionRequest` frames. Codex's notification stream is a three-state lifecycle per item (`item/started` → `item/agentMessage/delta` / `item/reasoning/summaryTextDelta` → `item/completed`), wrapped by `turn/started` and `turn/completed` frames the adapter maps onto `StatusChange`/`TurnMetrics`. See `docs/backend.md` → "Tool permissions: the can_use_tool callback flow", "Tool permissions for Amp: the delegate-bridge", and "Tool permissions for Codex: app-server approval callbacks".

---

## `POST /api/agents/{slug}/detach`

```
Browser ──► Router (agents.py) ──► commands.detach.execute(workstore, supervisor, worktrees, req)
                                       │
                                       ├─► WorkStore.get_work_slug_for_agent(slug)   ← 404 AgentNotFound
                                       ├─► validate resumable (status + session_id)  ← 409 AgentNotResumable
                                       ├─► await supervisor.stop_agent(slug)         ← cancel SDK task, drain queue
                                       ├─► WorkStore.set_agent_status(slug, "detached")
                                       └─► spawn user terminal with the CLI resume command (best-effort)
                                                                              ╔══════════╗
                                                                              ║ terminal ║
                                                                              ╚══════════╝
                                       returns DetachResponse{command, launched}
```

The terminal launch is best-effort — when it can't fire (Linux without a detected emulator, sandboxing, etc.) `launched=False` and the response still carries the resume command string so the FE can copy-to-clipboard. After detach the agent's WS will close; reconnecting later picks the resume path (Case B) once the user re-attaches in-app.

---

## `POST /api/agents/{slug}/compact`

```
Browser ──► Router (agents.py) ──► commands.compact.execute(...)
                                       │
                                       ├─► WorkStore.get_work_slug_for_agent(slug)  ← 404
                                       ├─► reject missing session / mid-turn         ← 409
                                       ├─► WorktreeManager.ensure(...)
                                       ├─► WorktreeManager.describe_state(workdir)
                                       ├─► await supervisor.stop_agent(slug)
                                       ├─► append compaction_progress phase markers
                                       ├─► summarizer(transcript, work/agent context)
                                       ├─► WorkStore.write_agent_compaction_doc(...)
                                       ├─► CompactionSessionClient.start_fresh_session(...)
                                       │       ╔══════════════════════╗
                                       │       ║ provider SDK session ║
                                       │       ╚══════════════════════╝
                                       ├─► CompactionSessionClient.write_breadcrumb(old_sid)
                                      ├─► WorkStore.set_agent_session_id(new_sid, mirror_agent_json=True)
                                      ├─► append context_compacted transcript event
                                      └─► resume.execute(..., lazy=True)
                                  Router formats CompactAgentResponse
```

The HTTP handler is thin: all workflow decisions live in the domain command, and provider-specific seed/breadcrumb mechanics sit behind the domain `CompactionSessionClient` port. The summary file is additive metadata under `agents/<slug>/compactions/`; the agent worktree is reused, not recreated.

## `GET /api/agents/{slug}/compactions/{filename}`

The browser calls this from the `context_compacted` transcript boundary when the user opens **View summary**. The route delegates to `commands.read_compaction_summary.execute(...)`, which resolves the agent's work, reads only `agents/<slug>/compactions/<filename>` through `WorkStore`, and returns `{agent_slug, work_slug, filename, summary_path, content}`. The filename is path-segment scoped; callers do not send the absolute `summary_path` back as input.

---

## `POST /api/chats/{slug}/compact`

```
Browser ──► Router (chats.py) ──► commands.chats.compact.execute(...)
                                      │
                                      ├─► ChatStore.get_chat(slug)                 ← 404
                                      ├─► reject missing session / mid-turn        ← 409
                                      ├─► build chat provider config from working directory/link
                                      ├─► await chat_supervisor.stop_agent(slug)
                                      ├─► append compaction_progress phase markers
                                      ├─► summarize transcript
                                      ├─► ChatStore.write_chat_compaction_doc(...)
                                      ├─► CompactionSessionClient.start_fresh_session(...)
                                      ├─► CompactionSessionClient.write_breadcrumb(old_sid)
                                      ├─► ChatStore.set_chat_session_id(new_sid)
                                      ├─► append context_compacted transcript event
                                      └─► stop chat_supervisor again for reconnect races
                                 Router formats CompactChatResponse
```

Chat compaction keeps the `CHT-NNN` identity, Project/Work link, and working
folder. It only replaces the underlying provider session and writes additive
metadata under `chats/<slug>/compactions/`.

## `GET /api/chats/{slug}/compactions/{filename}`

The chat renderer calls this from a `context_compacted` boundary when the user
opens **Show summary**. The route reads only
`chats/<slug>/compactions/<filename>` through `ChatStore` and returns
`{chat_slug, filename, summary_path, content}`. As with agent compactions, the
caller sends only the scoped filename, never an absolute path.

---

## `POST /api/agents/{slug}/reveal`

```
Browser ──► Router (agents.py)
                │
                ├─► WorkStore.get_work_slug_for_agent(slug)        ← 404 if unknown
                ├─► WorkStore.list_agents_for_work(work_slug)      ← locate Agent for folder field
                ├─► resolve worktree path (or agent.folder fallback if no worktree was provisioned)
                └─► open_in_file_browser(target)
                204 No Content
```

Symmetric with the work-level reveal but targets the dir where the adapter's CLI actually runs — handy when poking at the agent's working tree. The 404 fires from either lookup (slug not registered, or registered but the agent row vanished mid-call). OS-level errors map to 500.

---

## `GET /api/git/branches`

```
Browser ──► Router (git.py)
                │
                ├─► validate path is absolute (or starts with ~)  ← 400 otherwise
                └─► list_branches(expanded_path)
                        │
                        └─► git for-each-ref --sort=-committerdate
                                   --format=%(refname:short) refs/heads/
                            ╔════════════╗
                            ║ git CLI    ║
                            ╚════════════╝
                returns BranchListing{path, branches: [...]}  ← [] for non-git / missing
```

Powers the New Agent dialog's branch picker. Branches arrive sorted by most-recent committer date so the user's likely target is first. Non-git folders, missing paths, and any subprocess failure all return `branches: []` — the FE renders a friendly "not a git repo" hint instead of branching on error codes.

---

## Connections

### `GET /api/connections/types`

```
Browser ──► Router (connections.py)
                │
                └─► return list(DESCRIPTORS.values())
```

Static descriptor list (per-source form fields, doc URL, glyph, `verifiable` / `context_fetchable` flags). No persistence touched. The FE uses `context_fetchable` to filter the agent-context picker — picking a non-fetchable type would 422 at agent creation.

### `GET /api/connections`

```
Browser ──► Router (connections.py) ──► ConnectionStore.list()
                                              └─► repo.list()  (SQLite)
                                        Router maps to ConnectionRead (no token field)
```

Tokens never leave the keychain over the API.

### `POST /api/connections`

```
Browser ──► Router ──► ConnectionStore.create(payload)
                            │
                            ├─► repo.add(connection)     ← assigns id+slug
                            └─► secret_store.set(slug, token)   ╔══════════╗
                                                                 ║ keyring  ║
                                                                 ╚══════════╝
                       returns ConnectionRead
```

The token only ever lives in the OS keychain (the `KeyringSecretStore` adapter). The DB row carries metadata only.

### `GET /api/connections/{slug}`

```
Browser ──► Router ──► repo.get_by_slug(slug) → 404 if missing
                       returns ConnectionRead
```

### `PATCH /api/connections/{slug}`

```
Browser ──► Router ──► ConnectionStore.update(slug, payload)
                            │
                            ├─► repo.upsert(connection)
                            └─► (if token in payload) secret_store.set(slug, new_token)
                       returns ConnectionRead
```

Token rotation happens iff `token` is in the payload; metadata-only patches don't touch the keychain.

### `DELETE /api/connections/{slug}`

```
Browser ──► Router ──► ConnectionStore.delete(slug)
                            │
                            ├─► repo.delete(slug)
                            └─► secret_store.delete(slug)
                       204 No Content
```

### `POST /api/connections/{slug}/verify`

```
Browser ──► Router ──► ConnectionStore.verify(slug)
                            │
                            ├─► repo.get_by_slug(slug)
                            ├─► secret_store.get(slug) → token
                            └─► verify(connection, token)   ← per-type adapter (jira, sentry, honeycomb)
                       returns VerifyResponse{verified, error?}
                       (also persists last_used + verified flag on the row)
```

`verify` is a per-type pure function (`infrastructure/connections/verify.py`); it lives behind a port so tests can stub it.

---

## Projects

Project is metadata-only — no filesystem state, no children. The store is a thin SQL repo behind the `ProjectStore` port. Connections are referenced by slug (`default_jira_conn`, `default_sentry_conn`), not by id, so the project payload is portable.

### `GET /api/projects`

```
Browser ──► Router (projects.py) ──► commands.list_all.execute(projectstore)
                                          │
                                          └─► ProjectStore.list_projects()  (SQLite)
                                     Router maps each to ProjectSummary
```

No soft-delete today; everything in the table is live. Pinned ordering is a presentation concern handled by the FE.

### `POST /api/projects`

```
Browser ──► Router ──► commands.create.execute(projectstore, req)
                            │
                            └─► ProjectStore.create_project(req)
                                    └─► repo.add_project(project)  ← assigns id + slug (PRJ-NNN)
                       Router formats ProjectDetail
```

SQL-only flow; no FS write. `default_jira_conn` / `default_sentry_conn` are validated at the connection-layer when a Work is later attached, not here — the project create accepts any slug strings.

### `GET /api/projects/{slug}`

```
Browser ──► Router ──► commands.get.execute(projectstore, slug)
                            │
                            └─► ProjectStore.get_project(slug) → ProjectRecord | None
                       Router formats ProjectDetail (or 404)
```

---

## Persistence ordering (cross-cutting)

Several flows write to both SQL and the filesystem. The convention everywhere:

1. **Persist to SQL first** (commits per call inside the repo).
2. **Then write to FS** (atomic via `<path>.tmp` + rename + fsync).

A crash between (1) and (2) leaves an orphan DB row. On startup, `reconcile` (`domain/workstore/reconcile.py`) walks the FS as canonical and brings the SQL index back into agreement: insert rows that exist on disk but not in DB, update rows whose DB state differs from disk, delete rows that no longer exist on disk.

This is why the FS layout (`work.json`, `agent.json`, `transcript.ndjson`, `context.md` + `context/`) is the source of truth — the DB is just an index that supports fast queries, and any mismatch is reconciled toward the disk.
