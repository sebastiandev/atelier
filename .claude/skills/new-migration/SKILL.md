---
name: new-migration
description: Scaffold a new migration. Two flavours — db (a step in backend/src/infrastructure/database/migrations.py) or fs (a new scripts/migrate-<slug>.py). Use when changing a SQL schema, transcript shape, or any on-disk JSON shape. Invoke as /new-migration db <slug> or /new-migration fs <slug>.
---

# New migration — scaffold a forward-only migration step

Two flavours, picked by the first argument:

- **`db`** — adds a step to `backend/src/infrastructure/database/migrations.py`. For SQLite schema changes (new columns, ALTERs). Runs automatically on backend boot.
- **`fs`** — creates a new `scripts/migrate-<slug>.py`. For on-disk state (transcripts, work.json, agent.json shape changes). Runs via the `/migrate` skill.

Both produce **forward-only, idempotent** migrations — that's the project's convention. There's no "down".

## Usage

```
/new-migration db   options-column        # bumps SCHEMA_VERSION + adds an ALTER block
/new-migration fs   transcript-canonical  # creates scripts/migrate-transcript-canonical.py
```

## DB flavour

### 1. Read the current state

```bash
grep -n "CURRENT_SCHEMA_VERSION\|if existing ==" backend/src/infrastructure/database/migrations.py | tail -5
```

Find the current `CURRENT_SCHEMA_VERSION = N` and the most recent `if existing == N:` block (the last one before the unknown-version raise).

### 2. Bump the version + add the new step

Edit `backend/src/infrastructure/database/migrations.py`:

- Bump `CURRENT_SCHEMA_VERSION` from `N` to `N+1`.
- Insert a new block right after the existing `if existing == N-1:` ones, before the `if existing == CURRENT_SCHEMA_VERSION:` final block:

```python
if existing == N:
    # vN → vN+1: <one-line summary of the change>.
    #
    # <a paragraph or two on the WHY — what user-visible behaviour
    # changed, what data is affected, what the rollback story looks
    # like (usually "wipe.sh and re-create" since these are forward-only).>
    conn.execute(text("ALTER TABLE <table> ADD COLUMN <name> <TYPE>"))
    existing = N+1
```

### 3. **DO NOT call `<table>.create(conn)`**

Critical: `metadata.create_all(engine)` runs **before** the per-version step loop. New tables show up automatically; per-version steps must only `ALTER` existing tables. Calling `<new_table>.create(conn)` when the table already exists raises `sqlite3.OperationalError: table already exists`. If you're adding a wholly new table, just declare it in `tables.py` — `create_all` picks it up. The version step is a stamp-bump only in that case.

### 4. Update `tables.py` if adding a column

Mirror the ALTER in `agents_table = Table(...)` (or whichever table) so fresh installs (which skip migrations and run `create_all`) get the same shape. **The ALTER and the Table column declaration must match.**

### 5. Update the entity, mapping, and serializer if relevant

Checklist for a new column:

- `backend/src/domain/models.py` — add the field to the dataclass.
- `backend/src/infrastructure/database/mapping.py` — column name = attribute name; the existing `map_imperatively` covers it. Verify.
- `backend/src/domain/workstore/_serde.py` (or the relevant `_serde.py` if it's not a workstore entity) — serialize/deserialize the new field.
- Repo helpers (`work_repository.py`, `connection_repository.py`, etc.) — usually no change because they round-trip the entity directly.

### 6. Test the migration

`tests/integration/test_database.py` already has `test_schema_version_stamp_is_current` which fails when the constant moves but no migration step matches. Run the suite to confirm:

```bash
cd backend && uv run pytest tests/integration/test_database.py -q
```

If you want explicit per-version coverage, add a `test_v<N>_to_v<N+1>_migration_applies_alter` style test that stamps an older version, runs `initialize_database`, and asserts the new column exists.

## FS flavour

### 1. Create the script from the canonical template

```bash
cp scripts/migrate-transcripts.py scripts/migrate-<slug>.py
```

`migrate-transcripts.py` is the reference template: idempotent, atomic (write-temp + rename), `sys.path` injection so it runs from anywhere, prints a summary at the end. Adapt those bones; replace the per-record transformation with what you actually need.

### 2. Replace the template's payload

In the new file:

- Top docstring — describe the input shape, the output shape, and why the migration is needed.
- The walk function — change the glob if you're not iterating transcripts.
- The per-record transformation — this is the part you write from scratch. Keep it pure (input dict → output dict).
- The summary line — count what changed vs. what was already in the new shape.

### 3. Verify idempotency

The contract for FS migrations is that running them twice is a no-op. Test it:

```bash
cd backend && uv run python ../scripts/migrate-<slug>.py
cd backend && uv run python ../scripts/migrate-<slug>.py
```

The second run should report "0 changed; N already migrated" or similar. If it tries to "migrate" things it already migrated, the per-record transformation isn't recognising its own output — fix that before shipping.

### 4. Document the migration in the commit message

When you commit, mention what the script does and that `/migrate` will pick it up automatically on next contributor pull. The `/update` skill calls `/migrate`, so users who follow the standard update flow get migrated transparently.

## What this skill is NOT

- Not a code generator — it scaffolds the structure, not the actual ALTER or transformation logic. That's the migration's whole point and you have to write it.
- Not for renaming fields. Renames break backward compatibility; per `CLAUDE.md` § "Backward compatibility", stop and ask the user before going down that path.
- Not for dropping tables or columns (same reason).
