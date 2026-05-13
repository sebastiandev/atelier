---
name: wipe
description: Destructive cleanup of Atelier state — wraps scripts/wipe.sh. Use only when the user explicitly asks to wipe, reset, or start fresh. Requires the backend to be stopped first.
---

# Wipe — destructive reset of Atelier state

This is the nuclear option for getting back to a clean slate. The wrapped script (`scripts/wipe.sh` → `scripts/wipe.py`) handles the full cleanup including state outside the workspace dir (git worktree registry, Claude SDK transcript cache).

## What gets wiped

| Surface | Wiped? | Notes |
|---|---|---|
| `~/Atelier/works/<slug>/` (works DB rows + on-disk dirs) | ✅ | Cascades to agents, artifacts, handoffs, transcripts. |
| `~/Atelier/projects/<slug>/` (project DB rows + on-disk dirs) | ✅ | |
| Per-agent git worktrees | ✅ | `git worktree remove → --force → rmtree + prune` per agent. |
| Legacy `atelier/<work>/<agent>` branches in source repos | ✅ | Best-effort delete; custom user-named branches are NOT touched. |
| `~/.claude/projects/<munged-workdir>/` (Claude SDK session JSONL) | ✅ | Per agent's workdir, deleted after the work-dir rmtree. |
| Connections (DB rows + keychain entries) | ❌ — preserved | Re-running the wipe doesn't make you re-enter Jira/Sentry/Honeycomb tokens. |
| `schema_version` | ❌ — preserved | The DB stays at the current schema version; no re-migration needed on next start. |
| Amp threads on Sourcegraph's servers | ❌ — out of scope | Server-side; would need `amp threads delete` per session. |

## Steps

### 1. Confirm the backend is stopped

The supervisor's in-flight writes will race the wipe's deletes if it's running. Ask the user:

> Is the Atelier backend stopped? If it's running, kill it first (Ctrl+C in whichever terminal launched `dev.sh`/`dev-backend.sh`).

If they say no, ask them to stop it and come back. Don't try to kill processes we don't own.

### 2. Confirm the scope

The skill argument picks the scope:

- `/wipe` or `/wipe all` → every work, every project, plus FS dirs.
- `/wipe work WRK-001` → one work + its FS dir.
- `/wipe project PRJ-001` → a project + all its works + FS.

If the argument is missing or ambiguous, ask the user before running. Default to "all" only when they explicitly say so — never assume the broadest scope.

### 3. Run

```bash
./scripts/wipe.sh <scope> <slug?>
```

Pass `-y` to skip the script's own interactive confirmation **only** if the user explicitly says "yes, just do it" or similar. Otherwise let the script prompt — the safety net is worth one extra round-trip.

### 4. Surface the summary line

The script prints a final line like:

> Done. Pruned 3 worktree(s); deleted 5 Claude SDK transcript dir(s).

Pass that through verbatim — it's how the user knows the post-rmtree cleanup actually ran.

## What this skill is NOT

- Not for resetting connections (those are intentionally preserved). If the user wants to clear connections too, point them at the database directly or the Connections UI's delete buttons.
- Not for schema reset. To re-create the schema from scratch, delete `~/Atelier/atelier.db` outside this skill.
- Not for selective per-agent wipe. The script's smallest scope is one work; if you only want to remove one agent, use the Atelier UI's agent delete.

## Background context

The wipe script handles state outside the workspace dir because earlier versions of `wipe.py` only rmtreed `~/Atelier/works/`. That left dead `git worktree` pointers in source repos (`git worktree list` would show broken paths) and a growing `~/.claude/projects/` cache of stale session JSONL. The current script cleans both as part of every wipe, regardless of scope. See `scripts/wipe.py:_cleanup_worktrees` and `_cleanup_claude_sdk_transcripts`.
