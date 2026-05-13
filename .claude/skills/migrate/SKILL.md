---
name: migrate
description: Run all pending FS-side state migrations. Use after pulling changes that may have rewritten on-disk shapes (transcripts, work.json, agent.json). Called by /update and runnable directly. Does NOT run DB schema migrations — those auto-apply on backend boot.
---

# Migrate — apply pending FS-side migrations

Atelier has two migration surfaces:

| Surface | Where | When applied |
|---|---|---|
| SQLite schema | `backend/src/infrastructure/database/migrations.py` | Auto, on backend boot (`initialize_database`) |
| FS state (transcripts, JSON files) | `scripts/migrate-*.py` | **Manual — this skill** |

This skill runs everything in the second column. It exists so a contributor can pull a change to logged event shape (or any on-disk schema) and get their existing works migrated without hunting for the right script.

## Steps

### 1. Discover migrations

```bash
ls scripts/migrate-*.py 2>/dev/null
```

If nothing matches, say "no FS-side migrations registered" and stop. There's nothing to do.

### 2. Run each, in alphabetical order

For each `scripts/migrate-*.py`:

```bash
cd backend && uv run python ../scripts/<name>.py
```

The `cd backend` step is intentional — every migration script does `sys.path.insert(0, "<repo>/backend")` so it can import `src.*`, but it still runs better inside the backend venv that owns the dependency tree. `uv run` activates the venv on the fly.

Migrations **must be idempotent** — re-running them on already-migrated data is a no-op. That's the contract; don't try to track "have I run this already" state. If a contributor adds a non-idempotent script, that's a bug in the script.

### 3. Surface what each script printed

Each script prints its own summary (e.g. "rewrote 3 transcripts; 12 already canonical"). Pass that through verbatim — don't summarise. The user wants the actual counts.

### 4. Stop on failure

If any script exits non-zero, **don't run the rest**. Surface the error and ask the user how to proceed. A failed migration usually means either the input is in an unexpected state or the script has a bug — both warrant inspection before piling on more changes.

## What this skill is NOT

- Not for DB migrations (those run on backend boot).
- Not for running a single specific script — just `uv run python scripts/<name>.py` directly. This skill is the "run everything pending" entry point.
- Not for creating new migrations — that's `/new-migration`.

## Adding a new migration

Use `/new-migration fs <slug>` to scaffold a new `scripts/migrate-<slug>.py` from the canonical template. The naming convention (`migrate-*.py`) is what makes this skill auto-discover it.
