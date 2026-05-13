---
name: test
description: Run backend pytest with the right venv automatically. Optionally pass a path or test name to filter. Use when the user says "run tests", "test X", or after making changes that need verification.
---

# Test — run pytest under backend/.venv

The constant trip-up: the backend venv is at `backend/.venv` (not repo root). Activating from the wrong place silently uses the system interpreter and either errors on missing deps or — worse — runs against an outdated environment. This skill encodes the right invocation.

## Steps

### 1. Pick the test target

The skill takes an optional argument:

- `/test` (no arg) → run everything: `cd backend && uv run pytest -q`
- `/test tests/unit/domain/agents/` → run a directory
- `/test tests/unit/infrastructure/cli_launcher/test_build_resume_command.py::test_amp_includes_mode_flag` → run a single test by node id
- `/test -k "ctx_pct"` → pass through pytest's `-k` filter

Pass the argument straight to pytest after the path setup. Don't try to be clever about parsing; pytest's CLI handles it.

### 2. Run

```bash
cd backend && uv run pytest <args>
```

`uv run` activates the venv on the fly — faster than `source .venv/bin/activate && python -m pytest` and avoids polluting the user's shell. It also picks the right interpreter every time, even if the user has a different one on PATH.

If the user prefers verbose output or a specific format, pass through their preference. The default is `-q` (quiet) since most runs the user wants the pass count + any failures, not the full collection list.

### 3. Surface failures usefully

If pytest exits non-zero:

- Show the failed test name(s) — `pytest --tb=short` already does this; don't post-process.
- If only one or two tests failed, offer to re-run them in isolation with `-x -vv` for more detail.
- Don't try to "fix" failing tests automatically. Surface the diff between expected and actual; let the user decide.

If everything passed, surface the count + duration. One line: `720 passed in 11.94s`.

## Frontend tests

This skill is backend-only by default. If the user asks for "frontend tests" or "all tests", run:

```bash
cd frontend && npm test
```

Frontend test infra is conventional Vitest — no special venv handling needed.

## What this skill is NOT

- Not for type checking — that's `tsc` (frontend) or `mypy` (backend). Could be a separate `/typecheck` skill if it ever becomes worth it.
- Not for linting (`ruff` / `eslint`) — same.
- Not a CI runner. It runs the same pytest CI runs, but without the matrix dance.
