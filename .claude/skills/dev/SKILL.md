---
name: dev
description: Start the Atelier dev servers (backend on 8001, frontend on 4173). Wraps scripts/dev.sh, scripts/dev-backend.sh, scripts/dev-frontend.sh. Use when the user wants to start, restart, or boot the local dev environment.
---

# Dev — start the local dev servers

Three scripts already exist; this skill is a thin wrapper that picks the right one and bakes in the project's port conventions so the user doesn't have to remember them.

## Port conventions

- **Backend** → `127.0.0.1:8001` (port `8000` conflicts with another app on the user's machine; the project default is `8001`).
- **Frontend** → `127.0.0.1:4173` (Vite default `5173` conflicts; the project uses `4173` and proxies `/api/*` to the backend).

Both are overridable via env vars: `ATELIER_BACKEND_PORT`, `ATELIER_FRONTEND_PORT`, `ATELIER_BACKEND_HOST`, `ATELIER_FRONTEND_HOST`. Pass these only when the user asks; never override silently.

## Usage

```
/dev          # both servers (default)
/dev be       # backend only
/dev fe       # frontend only
/dev both     # explicit both — same as no arg
```

## Steps

### 1. Pick the script

| Argument | Script | Behaviour |
|---|---|---|
| (none) or `both` | `./scripts/dev.sh` | Starts both servers in one terminal session, multiplexed. |
| `be` or `backend` | `./scripts/dev-backend.sh` | Backend only. |
| `fe` or `frontend` | `./scripts/dev-frontend.sh` | Frontend only. |

### 2. Check whether something is already on the port

Before launching:

```bash
lsof -nP -iTCP:8001 -sTCP:LISTEN 2>/dev/null
lsof -nP -iTCP:4173 -sTCP:LISTEN 2>/dev/null
```

If a process is already listening on the relevant port, **don't kill it** — that might be the user's existing dev server in another terminal. Surface the conflict and ask. The user can either stop the existing process themselves or specify an override port.

### 3. Run

```bash
./scripts/<script>.sh
```

Run in the **foreground** by default — the dev servers stream useful logs (request paths, error tracebacks) the user wants to see. If the user explicitly says "background it" or "I want to keep working in this conversation", run with `run_in_background: true` so the conversation stays usable; otherwise let it block.

### 4. Surface the URLs

Once started, print:

> Backend ready at http://127.0.0.1:8001
> Frontend ready at http://127.0.0.1:4173

Wait until the servers actually report ready (the dev scripts print a "ready" line) before claiming they're up — don't assume start = ready.

## Restart pattern

There's no `/dev restart` mode because we don't want this skill killing servers it didn't start. To restart:

1. Tell the user to Ctrl+C the running dev script in its original terminal.
2. Then re-invoke `/dev`.

If the user asks for restart-like behaviour repeatedly, that's a hint we should add a `--restart` flag to `dev.sh` itself rather than baking process-killing into this skill.

## What this skill is NOT

- Not for production deploys (Atelier is dev-tooling-first; no production launcher exists).
- Not for the desktop launcher install — that's `./scripts/install-launcher.sh` and the user runs it manually once.
- Not for managing the backend/frontend dependency install — that's `/update` (or first-time setup, which the README covers).
