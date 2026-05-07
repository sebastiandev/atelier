# ATELIER Agent Notes

## General

- Backend Python work uses the `uv`-managed virtualenv at `backend/.venv`.
- Before running backend Python commands directly, activate it with `source .venv/bin/activate` from `backend/`, or prefer `uv run ...` / `uv sync`.
- Do not run backend Python tools against the system interpreter.
- When resuming work, read `.claude/docs/checkpoint-xxxx.md` first. Each of them are different checkpoints that can be resumed using /recap xxxx
- When saving or updating checkpoint context for this repo, write it to `.claude/docs/checkpoint-xxxxx.md`.
- For quick project state, check `_bmad-output/project-status.yaml` right after the continuation prompt.

## Developer docs (`docs/`)

- `docs/README.md` is the index. It points to `architecture.md` (cross-cutting), `backend.md`, `frontend.md`, `api-flows.md`, and `design-system.md`.
- These docs capture the *why* — design decisions, conventions, seam definitions. The code captures the *what*.
- **When you change behavior or design** (not typos / renames), check whether the relevant doc needs an update. Triage:
  - New port / Protocol or convention shift → `docs/architecture.md`
  - Provider abstraction change, supervisor behavior, persistence model, WS protocol → `docs/backend.md`
  - New HTTP/WS endpoint or sequence → `docs/api-flows.md`
  - New frontend pattern, route, hook semantics, or behavior shift in an existing component → `docs/frontend.md`
  - New visual convention (token family, brand asset, header/card/icon pattern) → `docs/design-system.md`
  - Decision that supersedes `_bmad-output/architecture-atelier-2026-04-30.md` → add a one-liner to `_bmad-output/project-status.yaml` → `locked_pivots`
- Keep updates small: a paragraph plus a code pointer (`backend/src/...py:N`). These are guidebooks, not specs.

## Frontend

- Frontend lives at `frontend/`. Stack: Vite + React 18 + TypeScript. Dev server on `127.0.0.1:4173` (5173 conflicts on the user's machine), proxies `/api/*` to the backend on `8001` — same-origin, no CORS.
- Hand-rolled router in `App.tsx` (path-prefix → component). Don't pull in `react-router` unless we cross ~5 patterns.
- One stylesheet by design: `frontend/src/styles.css`. Tokens are CSS custom properties on `:root`; theme variants override them under `[data-theme="..."]`. **Never hardcode colors in components** — pick the matching token, or add one to `:root`.
- Persona/project tinting is hue-based: components set `style={{ "--p-color": ... }}` (persona) or `style={{ "--proj-h": ... }}` (project hue 0–360). The cascade derives the rest of the ramp via `oklch()`. Don't pass full color strings.
- UI/presentation state lives in narrow Zustand stores under `frontend/src/state/`, persisted to `localStorage` where it should survive reload. Domain state stays on the server (REST + WS). Pinned/x/y/glyph and similar concerns belong here, not in domain entities.
- Async-only-where-forced: REST is sync (typed `fetch` wrappers in `api.ts`); the WS lives behind `useAgentStream`, the only point of contact with the agent stream.
- Icons are project-internal SVG components (12px viewBox, `currentColor`, `aria-hidden`). Don't add an icon library.
- For routing / state / streaming / dialogs / connections details → `docs/frontend.md`.
- For visual conventions (brand mark, headers, card rhythm, time formats, stat badges) → `docs/design-system.md`.

## Backend

- Backend API entrypoints live at `backend/src/main.py` and `backend/src/application/http/routes/health.py`.
- Backend architecture rule: `application/` owns FastAPI routes, webserver schemas, and application-layer orchestration; `domain/` owns use cases, domain rules, entities, interfaces, and primitives; `infrastructure/` owns implementations of external dependencies such as persistence, provider clients, and secret storage.
- Backend dependency rule: `application/` may import `domain/` and, when needed, `infrastructure/`; `domain/` must never import `application/` or `infrastructure/`; `infrastructure/` may import `domain/` but must never import `application/`.
- Backend integration rule: prefer defining an interface in `domain/` for any infrastructure-backed capability, then implement it in `infrastructure/`. If an interface does not exist yet, first analyze whether it should be introduced; if that is unclear or would materially change scope, ask the user before skipping the interface.
- Backend settings entrypoint lives at `backend/src/settings.py`.
- Consolidate backend env-backed configuration through `backend/src/settings.py`; raw values should live in `.env.local` files rather than being redefined across scattered settings modules.

## Scripts

- Root launch scripts live at `scripts/dev.sh`, `scripts/dev-backend.sh`, and `scripts/dev-frontend.sh`.
- Root launch scripts support `ATELIER_FRONTEND_PORT`, `ATELIER_BACKEND_PORT`, `ATELIER_FRONTEND_HOST`, and `ATELIER_BACKEND_HOST` overrides.
- `scripts/install-launcher.sh` (macOS / Linux) and `scripts/install-launcher.ps1` (Windows) generate a thin desktop launcher that calls `dev.sh`. Templates and pre-built icons live under `scripts/launchers/`; generated artifacts (`Atelier.app`, `atelier.desktop`, `Atelier.bat` + `.lnk` shortcuts) install into the OS-standard locations (`~/Applications`, `~/.local/share/applications`, `%LOCALAPPDATA%\Atelier`). Re-run the installer after moving the repo.
- `scripts/launchers/icons/build-icons.sh` regenerates `.icns` / `.png` / `.ico` from `atelier-app-icon.svg` (qlmanage + iconutil + ImageMagick — macOS host). Re-run after the SVG changes.

## Notes

- `docs/continuation-prompt.md` is only a compatibility pointer for workflows that expect that default location.
- Keep BMad artifacts for this repo under `_bmad-output/` unless the user asks for a different structure.
- After meaningful structural or workflow changes, update this file if a new canonical path or retrieval shortcut would help future sessions start without extra browsing.
