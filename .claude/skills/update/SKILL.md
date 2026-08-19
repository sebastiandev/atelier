---
name: update
description: Bring the checkout up to date — pull, install changed dependencies, run FS migrations, and report what needs restarting. Wraps `./atelier update`. Use when the user says "update", "pull latest", or "sync with main".
---

# Update — sync the working copy with main

The procedure lives in `scripts/atelier.py` and runs as `./atelier update`.
This skill exists for the parts a script should not decide alone.

**Do not restate the steps here.** They were written twice once already —
prose and code drift, and a drifted procedure is worse than no script,
because the skill and the command then quietly do different things.

## Run it

```bash
./atelier update
```

That pulls `origin/<current branch>`, installs whatever the pull changed
(`uv sync`, the ACP runtime, the frontend), runs every
`scripts/migrate-*.py`, and prints what the user has to restart themselves.
It is safe to run with the servers up, and it never stops them.

Read the output back to the user rather than summarising it away — the
restart lines are the part they act on.

## Where you come in

The command asks before doing anything it cannot undo, and refuses to guess
when there is no terminal. When it stops, that is your cue:

- **Uncommitted changes.** It lists them and asks. Look at what is actually
  there before advising: a stray build artifact is not a reason to stop, an
  edited source file mid-refactor is. Never `git stash` on the user's
  behalf — that is one more thing for them to remember to restore, and the
  BC rule in `CLAUDE.md` covers user state.
- **On a branch other than `main`.** It offers to pull that branch's own
  upstream, never to merge `main` into it. If the user wanted `main`, say so
  and let them switch.
- **A pull that is not fast-forward.** It stops rather than rebasing. Read
  the divergence and explain it; do not resolve conflicts blind.
- **`--yes`** accepts those prompts. Use it only when the user has already
  said yes to the specific thing being asked.

## What this skill is NOT

- Not first-time setup — that is the README's "Getting started". This
  assumes dependencies have been installed at least once.
- Not branch switching, conflict resolution, or anything destructive.
- Not for starting the servers. That is `/dev` (`./atelier launch`), kept
  separate so a user running the servers in another terminal can update
  without anything touching their process tree.
