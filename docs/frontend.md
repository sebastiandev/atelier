# Frontend

Vite + React 18 + TypeScript. Dev server on `127.0.0.1:4173` (5173 conflicts on the user's machine), proxying `/api/*` to the backend on `8001` with WS upgrade enabled — same-origin, no CORS.

> Read [`architecture.md`](architecture.md) for the cross-layer rules and the seam definitions.

## Layout

```
frontend/src/
├── App.tsx              # path-based router (no router lib)
├── Home.tsx             # /
├── WorkView.tsx         # /works/<slug>
├── AgentView.tsx        # /agents/<slug> — wraps AgentTile in page mode
├── AgentTile.tsx        # the unit; "page" or "tile" mode
├── NewWorkDialog.tsx    # POST /api/works
├── NewAgentDialog.tsx   # POST /api/works/<slug>/agents
├── MarkdownText.tsx     # react-markdown + remark-gfm + shiki wrapper
├── useAgentStream.ts    # WS hook with replay + reconnect backoff
├── state/               # narrow Zustand stores (frontend-local concerns)
│   └── cursors.ts       # per-agent WS resume cursor, persisted
├── api.ts               # typed fetch wrappers + types + persona constants
└── styles.css           # tokens + every component style (one file, by design)
```

State: Zustand for frontend-local presentation concerns (see [State](#state)).

## Routing

Hand-rolled in `App.tsx`. Path prefix → component. We don't ship a router because:

- Two route patterns total (`/agents/<slug>`, `/works/<slug>`) plus the home.
- No nested routes, no parameterized search, no transitions.
- Adding `react-router` would be more code than the router itself.

If routing grows beyond ~5 patterns, swap it in.

## AgentTile — modes

`AgentTile` is the same component in two contexts:

- `mode="page"` (`/agents/<slug>`): full-viewport, fixed `880px` max-width, no persona pip in header, no maximize control surface.
- `mode="tile"` (inside `WorkView`'s canvas): no fixed height, persona pip + agent name in header, persona-tinted top border via `--p-color`/`--p-soft`, maximize toggle that sets `position: fixed; inset: 1.5rem`.

The mode is a structural switch, not a theming switch. Splitting into two components would duplicate the streaming logic; one component with a mode prop is right while behavior is 95% shared. If divergence grows past the textarea + header, split.

## `useAgentStream`

`useAgentStream(agentSlug)` is the single point of contact with the WS at `/api/agents/<slug>/stream`. It returns `{ events, status, sendInput }` and handles:

**Cursor-based resume.** On reconnect it appends `?cursor=<lastSeq>` so the server replays the window we missed before going live. The server's replay-then-live semantics guarantee no duplicates and no gaps (see `backend.md` → WS protocol). The cursor is persisted to `localStorage` (via the Zustand `cursors` store), so a page refresh also resumes from the last seen seq instead of replaying from 0.

**Exponential reconnect backoff.** Schedule: `1s → 2s → 4s → 8s → 16s → 30s` (cap). Resets on a successful `onopen`, so a single transient blip costs one 1s retry — only consecutive failures walk the ladder.

**Terminal close on 4404.** When the backend closes with code 4404 (supervisor has no live adapter — typically after a backend restart), the hook sets `status: "stopped"` and exits the retry loop. The `AgentTile` shows a banner and disables the composer.

**Cursor persistence.** The hook seeds `lastSeqRef` from `useCursorStore.getState().getCursor(slug)` at mount and writes back on every event with a numeric `seq`. The store persists to `localStorage` under the key `atelier:cursors`, so a page refresh resumes the stream from the saved seq — the transcript starts empty, but no replay storm.

## Composer

Multi-line auto-growing textarea, capped at 200px before scrolling. Enter submits, Shift/Cmd/Ctrl+Enter inserts a newline.

**Optimistic "thinking" status on send**: the status dot flips to `thinking` the moment you click Send, instead of waiting for the next `status_change` event from the adapter. The optimistic flag clears as soon as a real `status_change` lands. This is a perceived-latency thing; the truth is always the latest event from the adapter.

## Streaming + grouping

`AgentTile.groupEvents()` collapses runs of `message_delta` into a single growing assistant `RenderUnit`, and runs of `thinking_delta` into a single thinking unit. This is what lets you see text accrete instead of seven separate chunks.

A non-delta event (`tool_call`, `status_change`, `user_input`, …) closes any pending delta unit and starts its own. Markdown is rendered into the assistant + thinking units only (tool args/results stay in `<pre>` because they're stdout-flavored).

## Design tokens

Lifted from `design_handoff_atelier/design_files/styles.css` (gitignored — keep it in sync if it changes upstream).

**Currently dark-only.** Light theme + `[data-theme="dark"]` toggle deferred — the nested `[data-theme="dark"] [data-persona="..."]` rule pattern is in the design source for when we need it.

**Token families** (all in `frontend/src/styles.css` `:root`):

- Background ramp: `--bg`, `--bg-1`, `--bg-2`, `--bg-3`, `--panel`
- Foreground ramp: `--fg`, `--fg-2`, `--fg-3`, `--fg-4`
- Lines: `--line`, `--line-soft`
- Status hues: `--good`, `--warn`, `--danger`, `--info`
- Accent: `--accent`, `--accent-soft` (focus glow), `--accent-line` (focus border), `--accent-fg`
- Radii: `--radius-sm`, `--radius`, `--radius-lg`
- Shadows: `--shadow-1`, `--shadow-2`, `--shadow-pop`
- Fonts: `--font-ui` (Inter w/ system fallback), `--font-mono` (JetBrains Mono w/ system fallback)

**Legacy aliases**: the older names (`--bg-elev`, `--muted`, `--border`, `--status-*`) point at the new tokens, so existing selectors keep working without churn. Prefer the new names in new code; don't go on a renaming spree.

## Persona theming

Each persona owns a hue. Components opt in by setting `data-persona="<id>"` on a wrapping element; descendants pick up `--p-color` and `--p-soft`:

```css
[data-persona="architect"] { --p-color: oklch(0.78 0.14 20); --p-soft: oklch(0.78 0.14 20 / 0.14); }
/* developer / product / ux / writer ... */
```

The five canonical personas are listed in `frontend/src/api.ts` (`PERSONAS`, `PERSONA_GLYPH`). Writing `var(--p-color, var(--accent))` gives a sensible fallback when no persona is in scope.

This is what powers: the `AgentTile` tile-mode top border, the `WorkView` rail row's tint + accent bar when focused, the canvas-cell ring when a tile is selected, the persona-card hover/active in `NewAgentDialog`.

## Dialogs — minimal-first pattern

Both dialogs (`NewWorkDialog`, `NewAgentDialog`) ship the **minimum** that wires the button to the API; advanced surfaces (context attachments, worktree base-branch picker) are deferred to the Sprint 3 stories that own connections + git worktrees. The dialogs render a hint inline pointing forward.

This is intentional: it avoids stub UI controls that lie about working, keeps surface area small, and lets each later story add a focused chunk without touching unrelated layout.

## Provider-driven `NewAgentDialog`

The dialog renders its provider/model fields from the descriptors at `GET /api/providers`. If you add a new provider on the backend (`SPECS` registry), the dialog picks it up with **no frontend code changes** — including the primary-field label ("Model" for Claude, "Mode" for Amp) and the dropdown values.

Advanced per-provider options (Claude's `thinking_effort`, `permission_mode`) aren't rendered yet — the descriptor exposes them under `options`, and a follow-up will add a collapsible "Advanced" section that maps them to selects. Wire format already supports it (`POST /api/works/<slug>/agents` accepts `options: dict`).

## State

Component-local `useState` + the WS hook is the default. Cross-component, frontend-only state lives in narrow Zustand stores under `frontend/src/state/`, persisted to `localStorage` via the `persist` middleware where it should survive reload.

Current stores:

- `cursors.ts` — `{ cursors: Record<agentSlug, number> }`, persisted under `atelier:cursors`. Used by `useAgentStream` for refresh-resume.

When adding a new store: keep it narrow (one concern per file), put presentation-only state here (per CLAUDE.md → "UI state is frontend-local"), and don't reach into it from outside React-tree code unless you have a reason — `useStore.getState()` is a synchronous read, fine inside a `useEffect`.

## Build / typecheck

```bash
cd frontend
npm run dev        # vite dev server with HMR + /api proxy
npm run typecheck  # tsc --noEmit
npm run build      # tsc + vite build (writes to dist/)
```

The build emits per-language Shiki chunks loaded on demand (only languages Claude actually emits get fetched).

## Deferred

Items still on the Phase B / future-sprint list (kept here as a quick checklist; the source of truth is `_bmad-output/sprint-plan-atelier-2026-04-30.md`):

- [x] Cursor persistence (Zustand + `localStorage`)
- [x] Slow-subscriber drop policy in supervisor (backend)
- [ ] Transcript virtualization (only when a transcript actually gets long)
- [ ] Light-theme tokens + theme toggle
- [ ] `NewAgentDialog`: collapsible Advanced section for provider `options`
- [ ] Inline context attachments in `NewWorkDialog` / `NewAgentDialog` (Sprint 3, depends on `ConnectionStore`)
