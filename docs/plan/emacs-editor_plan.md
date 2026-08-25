# Emacs editor integration plan

Branch: `emacs-editor`

## Goal

Add Emacs to Atelier's external-editor choices while preserving the current URL-scheme flow for VS Code, Cursor, Zed, JetBrains editors, and MacVim.

## Constraints

- Keep VS Code as the default editor.
- Preserve the existing settings wire format and SQLite schema.
- Resolve workspace paths on the backend from an agent slug; never accept arbitrary client paths.
- Launch subprocesses with a fixed argument vector and no shell.
- Support macOS and Linux initially; fail clearly on Windows.
- Require `emacsclient` on the backend process PATH and an existing default Emacs server.
- Do not start or manage an Emacs daemon automatically.
- Remove `ALTERNATE_EDITOR` only from the child environment so `emacsclient` cannot start a fallback editor.
- Limit the `emacsclient` subprocess to 10 seconds and surface timeout failures through the endpoint.

## Implementation

1. Add an `emacs` descriptor to the backend editor catalog and frontend fallback catalog:
   - label: `Emacs`
   - command: `emacsclient -n -c .`
   - URL template: `null`
2. Add a narrow infrastructure helper that launches:
   `emacsclient -n -c <workspace>`
   where `-n` prevents the HTTP request from waiting for the Emacs frame to close.
   Pass a copied environment without `ALTERNATE_EDITOR` and set `timeout=10`.
3. Add `POST /api/agents/{agent_slug}/open-in-editor`:
   - resolve the registered agent;
   - select its provisioned worktree or source-folder fallback;
   - invoke the Emacs helper;
   - return `204 No Content` on success and actionable errors otherwise.
4. Add a typed frontend API wrapper and a testable transport selector:
   - Emacs uses the backend endpoint;
   - every other editor keeps the existing URL navigation behavior.
5. Integrate the selector into both agent-tile and Loop/Planning-run entry points.
6. Update `docs/api-flows.md` and `docs/frontend.md`.

## Verification

- Settings integration tests cover descriptor order and persistence of `editor="emacs"`.
- Infrastructure unit tests cover macOS/Linux argv, special-character paths, subprocess errors, and explicit Windows rejection.
- Agent route integration tests cover source-folder/worktree resolution, unknown slugs, and launch failures.
- Frontend tests cover Emacs HTTP dispatch, unchanged URL dispatch for other editors, and error propagation.
- Run focused backend tests with `uv run --extra dev pytest`, backend lint, frontend tests/build, and `git diff --check`.

## Acceptance criteria

- Emacs is selectable and persisted without migrations.
- Both UI entry points open the correct registered workspace through `emacsclient`.
- No shell interpolation or client-controlled path reaches subprocess execution.
- Missing client/server and unsupported-platform failures are visible to users.
- Existing editors retain their current behavior.
