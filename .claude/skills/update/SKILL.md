---
name: update
description: Pull latest main, install dependencies, run any pending FS-side migrations, and remind the user to restart dev. Use when the user says "update", "pull latest", or "sync with main".
---

# Update — sync the working copy with main

Brings a contributor's checkout up to date end-to-end: code, deps, on-disk state, plus a clear pointer for whatever needs a manual restart. Designed to be safe to run with a backend currently up — but the user has to actually stop/restart it themselves (we don't kill processes we don't own).

## Steps

### 1. Confirm the working tree is safe to pull

Run:

```bash
git status --short
git rev-parse --abbrev-ref HEAD
```

- If the branch isn't `main`, ask the user whether to switch first or update the current branch (`git pull --rebase origin <branch>`).
- If the working tree has uncommitted changes, surface them and ask before pulling. Don't `git stash` automatically — the BC rule in `CLAUDE.md` covers user state, and a stash is one more thing the user has to remember to restore.

### 2. Pull

```bash
git pull --ff-only origin main
```

If that fails (diverged branch), fall back to `git pull --rebase origin main` and surface any merge conflicts to the user — don't try to resolve them blindly.

### 3. Install dependencies if lockfiles changed

Diff the pull range:

```bash
git diff --name-only HEAD@{1} HEAD -- backend/uv.lock backend/pyproject.toml frontend/package-lock.json frontend/package.json
```

- If `backend/uv.lock` or `backend/pyproject.toml` changed → `cd backend && uv sync` (use `uv sync` rather than activating a venv; it's faster and skips the activation dance).
- If `frontend/package-lock.json` or `frontend/package.json` changed → `cd frontend && npm install`.

Skip silently when nothing changed — this is the common case and shouldn't add 10 seconds of "nothing to do" output.

### 4. Run FS-side migrations

DB schema migrations auto-apply on backend boot, so we don't run them here. **FS-side state migrations (transcripts, on-disk JSON shape changes) need explicit invocation** — invoke the `/migrate` skill to do this, which globs `scripts/migrate-*.py` and runs each.

### 5. Tell the user what (if anything) needs a restart

- If backend dependencies changed OR there are commits touching `backend/src/infrastructure/database/migrations.py` in the pulled range → the backend needs a restart so DB migrations apply. Surface this; don't restart yourself.
- If frontend dependencies changed OR commits touched `frontend/src/`, `frontend/index.html`, or `frontend/vite.config.ts` → recommend a Vite refresh / restart. Same caveat — surface, don't act.
- If nothing changed in either layer, say so and stop.

### 6. Show the new HEAD

One line — `git log --oneline -1` — so the user sees what they just pulled.

## What this skill is NOT

- Not for first-time setup. That's covered by the README's "Getting started". This skill assumes deps are installed at least once.
- Not for branch switching, conflict resolution, or anything destructive. If the safe-to-pull check fails, hand control back to the user.
- Not for restarting the dev server — that's `/dev`. We deliberately keep update + dev separate so a user who's running `dev.sh` in another terminal can update without us touching their process tree.
