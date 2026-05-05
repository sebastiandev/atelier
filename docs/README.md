# Atelier — developer docs

These docs are the *why* behind the codebase. They cover the load-bearing
design decisions, the conventions you'll need to keep things coherent, and
the seams where backend and frontend agree on a contract.

The code itself is the *what*. When something here drifts from reality,
the code wins — but please update the doc.

## Layout

| File | Scope |
| --- | --- |
| [`architecture.md`](architecture.md) | Clean architecture layers, dependency rule, ports, command pattern, project-wide conventions |
| [`backend.md`](backend.md) | Backend-specific: provider abstraction, supervisor model, persistence layout, transcript log, WS protocol |
| [`api-flows.md`](api-flows.md) | One sequence diagram per HTTP/WS endpoint — what each route does, which command runs, which ports get touched |
| [`frontend.md`](frontend.md) | Frontend-specific: routing, `AgentTile` modes, `useAgentStream`, design tokens + persona theming, dialog conventions |

## Other authoritative sources

- `CLAUDE.md` (root) — instructions to AI agents working on the codebase. Architectural rules and the "where do I put X" decisions live there.
- `_bmad-output/architecture-atelier-2026-04-30.md` — the formal architecture spec. Some pivots have superseded it; see `_bmad-output/project-status.yaml` → `locked_pivots`.
- `_bmad-output/sprint-status.yaml` — story-level status and decisions log.
- `_bmad-output/project-status.yaml` — locked pivots, follow-ups, runtime essentials.
- `design_handoff_atelier/` — original UI design (gitignored). `frontend/src/styles.css` lifts the dark-theme tokens from `design_handoff_atelier/design_files/styles.css`.

## Doc maintenance discipline

When you change **behavior** or **design** — not when you fix a typo or rename a variable — check whether the corresponding doc needs to track it. Quick triage:

- Added or changed a port / Protocol → `architecture.md`
- New backend layer convention, persistence change, supervisor behavior, or WS contract → `backend.md`
- New frontend pattern (component prop, route, hook semantics, token), or a behavior change in an existing one → `frontend.md`
- A decision that supersedes something in `_bmad-output/` → add a one-liner to `project-status.yaml` → `locked_pivots`

Default to a short paragraph + a code-pointer (`backend/src/...py:N`). These docs are not a spec — they're a guidebook.
