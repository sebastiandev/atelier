---
name: update-dependencies
description: Safely update Atelier dependencies and reconcile changed APIs, configuration options, protocols, fixtures, tests, and documentation. Use when asked to upgrade, bump, refresh, or check versions of Python, frontend npm, ACP runtime, SDK, CLI, or other project dependencies.
---

# Update Dependencies

Update only the requested dependency set. Treat a dependency update as a
contract change until its installed runtime proves otherwise.

## 1. Establish Ownership

1. Read `AGENTS.md`, the target manifest, lockfile, and launch/import path.
2. Inspect existing diffs before editing; preserve unrelated work.
3. Classify each dependency:
   - `backend/pyproject.toml` + `backend/uv.lock`: Atelier-managed Python.
   - `frontend/package.json` + lockfile: Atelier-managed frontend npm.
   - `backend/acp-runtime/package.json` + lockfile: Atelier-pinned ACP wrappers.
   - Executables spawned from `PATH`, such as `opencode`: user-managed unless
     the user explicitly asks Atelier to pin them.
4. Check the installed and requested versions from the official registry or
   release notes. Read breaking changes between them.

Do not silently convert user-managed tools into bundled dependencies or broaden
an exact update into a full lockfile refresh.

## 2. Prepare the Install

Before any install:

1. Tell the user which package and dependency directory will change.
2. Check that no sibling agent is installing. `backend/.venv` and
   `frontend/node_modules` are shared across Atelier worktrees.
3. Preserve the manifest's existing exact/range convention.

Use the native project manager:

- Python: `cd backend && uv lock --upgrade-package <name>`; edit
  `pyproject.toml` only when the declared constraint must change.
- Frontend: `npm install <name>@<version>` from `frontend/`.
- ACP runtime: update the exact version in `backend/acp-runtime/package.json`,
  then `npm install --prefix backend/acp-runtime`.

Never run force-upgrade or audit-fix commands that rewrite unrelated versions.

## 3. Reconcile Contracts

Trace every importer, factory, descriptor, serializer, and test fixture for the
updated dependency. Check:

- Public API and type changes.
- Configuration option IDs, allowed values, defaults, and labels.
- Protocol versions, capabilities, events, permission shapes, and session
  lifecycle behavior.
- Persisted and wire values. Keep legacy values readable or add a migration.
- CLI flags, executable paths, environment variables, and auth behavior.
- Model/provider metadata exposed to the frontend.

For runtime protocols such as ACP, run a no-prompt compatibility probe:
initialize, create a disposable session, capture advertised config/capabilities,
then close it. Do not send a work prompt. Update captured fixtures and reconcile
Atelier descriptors with the captured values; do not guess from package version
numbers.

## 4. Validate

Run the smallest checks that cover the changed contract:

1. Package integrity: `npm ls`, `uv lock --check`, or ecosystem equivalent.
2. Focused unit and integration tests for adapters, factories, descriptors,
   routes, persistence compatibility, and affected UI.
3. A live no-prompt probe when the dependency wraps an external runtime.
4. Ruff/type checks for changed backend code; frontend build for changed
   TypeScript or npm dependencies.
5. `git diff --check`.
6. Non-mutating security audit. Report unresolved advisories and why they were
   not auto-fixed.

Expand to the full suite only when the dependency is cross-cutting or focused
tests expose wider breakage.

## 5. Finish

Update the relevant `docs/` guide when ownership, runtime behavior, protocol, or
configuration changes. Report:

- Old and new direct/runtime versions.
- Contract changes and compatibility handling.
- Tests and probes run.
- Audit findings.
- Restart impact. Existing processes and provider sessions retain loaded code;
  say whether the backend/frontend must restart or sessions must be recreated.
