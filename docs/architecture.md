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
- `AgentAdapter` (`backend/src/domain/agents/ports.py`) — the contract every provider (Claude, Amp, …) implements.

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

**Async only at the WS / supervisor / SDK boundary.** REST routes, commands, repos, and SQLAlchemy are all sync. When a sync command needs to invoke an async dependency, bridge with `asyncio.to_thread`. This keeps the bulk of the codebase simple and testable; only the streaming layer is asyncio-flavored.

**Int PK + slug TEXT UNIQUE on every public table.** SQL FKs use `id`, but every public-facing identifier (URLs, folder names, JSON cross-refs) uses `slug` (`WRK-001`, `agt-7`, `con-3`). Slugs are derived from the int PK at create time. See `infrastructure/database/tables.py`.

**SQLAlchemy with imperative mapping.** Domain entities are plain (mutable) dataclasses in `domain/models.py`. SA binding lives in `infrastructure/database/mapping.py`. The `domain/` layer never imports SA.

**FS-canonical persistence.** The filesystem at `~/Atelier/works/<slug>/` is the source of truth for work and agent metadata; SQLite is a queryable cache. `WorkStoreService` writes DB first then FS; a crash between the two leaves an orphan DB row, and startup `reconcile` (in `domain/workstore/reconcile.py`) repairs the divergence by deleting orphans and restoring rows from `work.json` if they're missing. FS wins on conflict.

**Soft-delete via the WorkStatus literal**, not a `deleted_at` column. Status `"deleted"` filters out from `_require_work` and the FS dir is preserved. No schema migration needed; reconciliation persists the deleted state across restarts.

**UI session state stays in the frontend.** Pinning, layout positions, focus state, glyph overrides — Zustand + localStorage, keyed by slug. The backend is presentation-agnostic.

## Where the seams are

These are the contracts the layers and process boundaries agree on. Changing one means coordinating all three sides.

| Seam | What flows | Defined in |
| --- | --- | --- |
| HTTP | Pydantic schemas | `backend/src/application/http/schemas.py` |
| WS frames | JSON event envelope (server→client) + `{type:"input",text:string}` (client→server) | `backend/src/application/ws/agents.py` + `frontend/src/useAgentStream.ts` |
| AgentEvent union | 7+ frozen dataclass variants with `Literal` discriminators | `backend/src/domain/agents/events.py` |
| Provider descriptors | `GET /api/providers` shape — drives the `NewAgentDialog` | `backend/src/domain/agents/specs.py`, `frontend/src/api.ts` |
| Workspace layout | `~/Atelier/works/<slug>/work.json`, `agents/<slug>/agent.json`, `transcript.ndjson` | `infrastructure/filesystem/` |

Anything else is internal.
