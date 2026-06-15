# Architecture

Atelier follows clean architecture in spirit, with three layers.

## Layers

```
application/  ← FastAPI routes, WS handlers, lifespan glue
   ↓ depends on
domain/       ← models, ports (Protocols), commands, actions, services
   ↑ implemented by
infrastructure/ ← SQLAlchemy mapping, filesystem, agent SDKs, keyring
```

The dependency arrow always points **inward**. `domain/` never imports from `application/` or `infrastructure/`. `infrastructure/` may import `domain/` to implement its ports. `application/` may import `domain/` and `infrastructure/`.

This rule is what lets `domain/` be tested without a database, without HTTP, and without the LLM SDKs — every external dependency arrives as a Protocol parameter.

## Ports

Infrastructure boundaries are `Protocol` classes in `domain/`. They're *callable* signatures or thin services. Examples:

- `WorkStore` (`backend/src/domain/workstore/ports.py`) — the public persistence boundary. Composed of three narrow ports under the hood: `WorkRepository`, `WorkspaceFiles`, `TranscriptLog`.
- `AgentAdapter` (`backend/src/domain/agents/ports.py`) — the contract every provider (Claude, Amp, Codex, …) implements.

Tests pass stubs satisfying the Protocol; production wires real implementations in `application/lifespan` (or the equivalent FastAPI startup hook).

## Command pattern

Business logic lives in **commands** under `domain/commands/<resource>/`.

- A command is a module with a public `execute()` function.
- Routers are thin: `parse input → call command → format output`. They never `add`/`flush`/`commit` or hold business rules.
- Input is a frozen `@dataclass` DTO; output is a frozen DTO or a domain entity.
- Infrastructure deps (LLM, store, supervisor) arrive as parameters to `execute()`.

Example: `backend/src/domain/commands/works/create.py` is six lines — one call into `WorkStore`. The `application/` layer's job is to translate HTTP into a `CreateWorkRequest` DTO and back.

> **Convention** — commands live in `domain/commands/`, not `application/commands/`. (One of the locked pivots in `project-status.yaml`.)

## Actions and concept dispatch

When a domain operation has multiple implementations driven by context (e.g. "build examiner turn" branching by language and phase), prefer Python's `functools.singledispatch` keyed on a typed context class.

The codebase uses this for **provider adapters**:

```python
# backend/src/infrastructure/agents/factory.py
@singledispatch
def build_adapter(config, settings) -> AgentAdapter:
    raise NotImplementedError(...)

# backend/src/infrastructure/agents/claude_code_adapter.py
@build_adapter.register
def _(config: ClaudeAgentConfig, settings) -> AgentAdapter: ...
```

Adding a new provider = define `<P>AgentConfig`, register a handler. The dispatch happens at runtime via the type. No registry to maintain by hand.

## Project-wide conventions

These are non-obvious and load-bearing — break them at your peril.

**Async only where forced.** REST routes, repos, and SQLAlchemy are sync. The async surface is the WebSocket endpoint, the supervisor, the SDK adapters, and any command that awaits one of those (`agents/connect`, `agents/start`, `agents/resume`, `agents/handle_user_action`, `agents/detach`). Commands that touch only the workstore stay sync — when they need to invoke an async dependency, bridge with `asyncio.to_thread`. The boundary stays narrow on purpose.

**Int PK + slug TEXT UNIQUE on every public table.** SQL FKs use `id`, but every public-facing identifier (URLs, folder names, JSON cross-refs) uses `slug` (`WRK-001`, `PRJ-001`, `agt-7`, `con-3`). Slugs are derived from the int PK at create time. See `infrastructure/database/tables.py`.

**Cross-table FKs use slugs — not int PKs — when the relationship round-trips through FS-canonical JSON.** Examples: `works.project_slug → projects.slug`, `projects.default_jira_conn → connections.slug`, `Context.conn_id` (a string slug, not an int). Strict parent–child FKs that never surface in JSON (`agents.work_id`, `artifacts.work_id`) stay `INTEGER → <parent>.id`. Reason: `work.json` and `project.json` carry the cross-ref directly; reconcile rebuilds the SQLite cache from disk without needing an int↔slug remap when ids shift across DB rebuilds.

**SQLAlchemy with imperative mapping.** Domain entities are plain (mutable) dataclasses in `domain/models.py`. SA binding lives in `infrastructure/database/mapping.py`. The `domain/` layer never imports SA.

**FS-canonical persistence.** The filesystem at `~/Atelier/works/<slug>/` and `~/Atelier/projects/<slug>/` is the source of truth for work, agent, and project metadata; SQLite is a queryable cache. `WorkStoreService` and `ProjectStoreService` each write DB first then FS; a crash between the two leaves an orphan DB row, and startup reconcile repairs the divergence by deleting orphans and restoring rows from disk. FS wins on conflict. Reconcile order across stores matters — at lifespan, `reconcile_projects` runs **before** `reconcile_works` because `works.project_slug` FK requires projects rows to exist before the work upsert can succeed. Same rule will apply to any future cross-store reference.

**Migration steps don't `.create()` brand-new tables.** `metadata.create_all` runs at the top of `initialize_database`; a per-version migration step that calls `<new_table>.create(conn)` for a brand-new table will fail on the second startup with "table already exists". New tables are picked up by `create_all`; the per-version step only does the work `create_all` can't do on existing databases (`ALTER TABLE` for new columns, data backfills, drops/renames). See `infrastructure/database/migrations.py` (v6→v7 added `works.project_slug`).

**Soft-delete via the WorkStatus literal**, not a `deleted_at` column. Status `"deleted"` filters out from `_require_work` and the FS dir is preserved. No schema migration needed; reconciliation persists the deleted state across restarts.

**UI session state stays in the frontend.** Pinning, layout positions, focus state, glyph overrides — Zustand + localStorage, keyed by slug. The backend is presentation-agnostic.

## Where the seams are

These are the contracts the layers and process boundaries agree on. Changing one means coordinating all three sides.

| Seam | What flows | Defined in |
| --- | --- | --- |
| HTTP | Pydantic schemas | `backend/src/application/http/schemas.py` |
| WS frames | JSON event envelope (server→client) + a typed `UserAction` taxonomy (`SendInput` / `StopTurn` / `ResolvePermission`) parsed from `{type, ...}` frames (client→server) | `backend/src/application/ws/agents.py` + `backend/src/domain/agents/user_actions.py` + `frontend/src/useAgentStream.ts` |
| AgentEvent union | 7+ frozen dataclass variants with `Literal` discriminators | `backend/src/domain/agents/events.py` |
| Provider descriptors | `GET /api/providers` shape — drives the `NewAgentDialog` | `backend/src/domain/agents/specs.py`, `frontend/src/api.ts` |
| Workspace layout | `~/Atelier/works/<slug>/work.json`, `agents/<slug>/agent.json`, `transcript.ndjson` | `infrastructure/filesystem/` |

Anything else is internal.

**Preferred provider seam (STORY-033, 2026-06-12):** new agent runtimes integrate through the Agent Client Protocol — one `AcpAdapter` behind the existing `AgentAdapter` port instead of a bespoke SDK adapter per provider. The domain `AgentEvent` union stays canonical (extended additively); ACP types never leave `infrastructure/agents/acp/`. See `docs/backend.md` → "ACP runtimes".
