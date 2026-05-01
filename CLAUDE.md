# ATELIER Agent Notes

## General

- Backend Python work uses the `uv`-managed virtualenv at `backend/.venv`.
- Before running backend Python commands directly, activate it with `source .venv/bin/activate` from `backend/`, or prefer `uv run ...` / `uv sync`.
- Do not run backend Python tools against the system interpreter.
- The canonical continuation prompt for this project lives at `_bmad-output/ATELIER-continuation-prompt.md`.
- When resuming work, read `_bmad-output/ATELIER-continuation-prompt.md` first.
- When saving or updating continuation context for this repo, write it to `_bmad-output/ATELIER-continuation-prompt.md`.
- For quick project state, check `_bmad-output/project-status.yaml` right after the continuation prompt.

## Frontend



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

## Notes

- `docs/continuation-prompt.md` is only a compatibility pointer for workflows that expect that default location.
- Keep BMad and other artifacts for this repo under `.claude/docs/` unless the user asks for a different structure.
- After meaningful structural or workflow changes, update this file if a new canonical path or retrieval shortcut would help future sessions start without extra browsing.
