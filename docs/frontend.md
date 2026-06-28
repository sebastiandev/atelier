# Frontend

Vite + React 18 + TypeScript. Dev server on `127.0.0.1:4173` (5173 conflicts on the user's machine), proxying `/api/*` to the backend on `8001` with WS upgrade enabled — same-origin, no CORS.

> Read [`architecture.md`](architecture.md) for the cross-layer rules and the seam definitions.
> Read [`design-system.md`](design-system.md) for visual conventions (brand mark, section headers, cards, icons, time formats).

## Layout

```
frontend/src/
├── App.tsx              # path-based router (no router lib)
├── Home.tsx             # /
├── WorkView.tsx         # /works/<slug>
├── ProjectScreen.tsx    # /projects/<slug>
├── Chat.tsx             # /chats/<slug> + spotlight composer + context doc modal
├── AgentView.tsx        # /agents/<slug> — wraps AgentTile in page mode
├── Connections.tsx      # /connections — CRUD UI for ConnectionStore
├── AgentTile.tsx        # the unit; "page" or "tile" mode
├── NewWorkDialog.tsx    # POST /api/works (with optional project picker)
├── NewProjectDialog.tsx # POST /api/projects (name + glyph + 7-swatch hue + default conns)
├── NewAgentDialog.tsx   # POST /api/works/<slug>/agents
├── MarkdownText.tsx     # react-markdown + remark-gfm + shiki wrapper
├── useAgentStream.ts    # WS hook with replay + reconnect backoff
├── state/               # narrow Zustand stores (frontend-local concerns)
│   ├── theme.ts         # dark/light/ansi cycle, persisted
│   ├── tweaks.ts        # accent hue + layout choice
│   └── closed.ts        # per-work set of agents pinned to the rail (closed)
├── ThemeToggle.tsx      # sun/moon button driving useThemeStore
├── TweaksPanel.tsx      # accent hue slider + layout segmented control
├── connectionFields.ts  # per-source form schema (CONNECTION_FIELDS)
├── api.ts               # typed fetch wrappers + types + persona constants
└── styles.css           # tokens + every component style (one file, by design)
```

State: Zustand for frontend-local presentation concerns (see [State](#state)).

## Routing

Hand-rolled in `App.tsx`. Path prefix → component. We don't ship a router because:

- Five route patterns total (`/agents/<slug>`, `/works/<slug>`, `/projects/<slug>`, `/chats/<slug>`, `/connections`) plus the home.
- No nested routes, no parameterized search, no transitions.
- Adding `react-router` would be more code than the router itself.

If routing grows beyond ~5 patterns, swap it in.

## Exploratory chats

`Chat.tsx` owns the chat route and the reusable chat surfaces. Home, Project, and Work each render a Chats section and bind `C` to open the spotlight `ChatComposer`; Home starts unlinked, Project presets `grounding={kind:"project"}`, and Work presets `grounding={kind:"work"}`. `grounding` is the Project/Work link that decides where the chat appears; `working_directory` is the optional folder used as the provider cwd. Home exposes both controls, Work hides the link because the current Work is implicit, and Project only allows the current Project or one of its Works. Home only lists unassigned chats, Project only lists project-grounded chats that have not moved into a work, and Work lists work-grounded/promoted chats. After creation, project-grounded chats land on their Project list, while work-grounded chats navigate to `/works/<slug>?chat=<CHT>` and WorkView opens the chat tile in the canvas. Project chat rows use the subtitle grounding layout so the associated work/project label stays directly under the chat title on wide screens. The composer uses the existing provider descriptors from `GET /api/providers`; provider/model and any non-default permission, mode, or effort option are persisted with the chat. Model-specific effort values/defaults come from descriptor `model_meta`, matching `NewAgentDialog`.

`/chats/<slug>` uses the `shell-v3 narrow-left` two-column layout: left rail for grounding/model/provenance, right column for the transcript and composer. The page fetches REST metadata with `GET /api/chats/{slug}` but renders and sends turns through `useAgentStream(chatSlug, { resource: "chats" })`, which opens `WS /api/chats/{slug}/stream`. Promotion is the single summary modal path: the user confirms name, brief, and project, then `POST /api/chats/{slug}/promote` returns the new Work and the UI navigates there. WorkView renders promoted chat context folders before project shares; clicking one opens `ContextDocModal`, which reads `GET /api/works/{work}/chat-contexts/{folder}/{filename}` and links back to the source chat. The full chat rail also exposes a neutral **Compact context** action that calls `POST /api/chats/{slug}/compact`.

Inside WorkView, the left rail orders Shared folders first, Active agents second, and Chats third. Chat rows are rail controls rather than navigation links: clicking a work chat opens a fixed-accent `ChatTile` in the same sortable canvas as agent tiles; closing the tile only removes it from the canvas, and the chat remains in the Chats rail. Chat rows can be renamed by double-clicking the title and deleted through the kebab menu, matching agent row affordances. Chat tiles use the chat websocket stream for transcript/input/stop/permission behavior and reuse `AgentTile` transcript units plus `TurnMetricsBar`, but intentionally omit IDE, console, reveal-worktree, detach, persona controls, duplicate header compaction, and current-work grounding metadata. Full-page chats and chat tiles also show a compact composer context gauge from the latest `turn_metrics` snapshot so context pressure remains visible near the input. Their context-bar compact action uses the same chat compact endpoint as the full chat page. Their start-agent action calls `POST /api/works/{work}/chats/{chat}/context`, then opens `NewAgentDialog` with the returned `context.md` as a normal `file` context.

## Projects

Projects are an **optional** grouping above Work. The whole feature is data-light by design — Project is metadata (name, glyph, single OKLCH hue, optional default Jira/Sentry connection slugs), not a workspace.

- **Home** (`Home.tsx`) renders a Projects card grid above a flat Latest-work list. Each card shows a glyph, a big proj-color active-count, the 3 most-recent works (status icon + slug + title; row clicks navigate to the work and `e.stopPropagation()` so the card click doesn't fire), connection pills derived from the project's defaults, and an `Open ›` arrow. The card itself is a `<div role="button">` (not an `<a>`) because the recent rows inside are anchors and nested anchor is invalid HTML — clicking the card calls `window.location.assign("/projects/<slug>")` programmatically. A peer LooseCard (no glyph color, no conn pills, no arrow) shows works with no project; clicking it filters the latest list to loose-only.
- **Latest work** has its own filter pills (All / per-project tinted via `--proj-h` / Loose with count) and a Tiles/List view toggle (default **list**, persisted under `localStorage["atelier:home:view"]`). The pills are independent of card-click navigation — clicking a project pill scopes the list without leaving Home; clicking a project card opens the project page.
- **`ProjectScreen.tsx`** at `/projects/<slug>`: hero with a `linear-gradient(180deg, --proj-soft, transparent)` wash + 3px tinted top bar (`.proj-hero-bar`), 56×56 glyph (panel-bg + proj-line border + proj-color text), 24px name, slug + description, `+ New work in {Project}` CTA on the right. Below the hero: meta grid (ID, Default connections with conn-pills + a small ↗ to /connections, Active count in proj-color, Completed count in proj-color), `home-tabs`-style Active/Completed strip, right-aligned Tiles | List toggle (default **tiles**, persisted per-project under `atelier:project:{slug}:view`). Tiles renders the existing `.work-card` grid with a "Start new work in {Project}" tile first; List renders the flat `.work-list`.
- **`NewWorkDialog`** accepts `projects`, `presetProjectSlug`, `lockProjectSlug` props. On Home it seeds the picker from the active filter; on Project pages it sets `lockProjectSlug=true` so the picker is disabled. Selecting a project tints the modal via `--proj-h`.
- **`WorkView`** breadcrumb adds a project crumb when `work.project_slug` is set: `Workspace › [glyph] {Project} › WRK-...`. The project crumb is a real link to `/projects/{slug}`.

### Per-project color tokens

Cards / chips / pills set `--proj-h` (hue 0–360) inline via `style`. The token system in `styles.css` derives the rest:

```css
[style*="--proj-h"] {
  --proj-bg:   oklch(0.62 0.18 var(--proj-h));         /* solid: glyph + dot */
  --proj-soft: oklch(0.62 0.18 var(--proj-h) / 0.14);  /* card top bar wash, chip bg */
  --proj-line: oklch(0.62 0.18 var(--proj-h) / 0.4);   /* chip border, hover line */
}
[data-theme="dark"] [style*="--proj-h"],
[data-theme="ansi"] [style*="--proj-h"] {
  --proj-soft: oklch(0.62 0.18 var(--proj-h) / 0.20);
  --proj-line: oklch(0.62 0.18 var(--proj-h) / 0.5);
}
```

Loose work uses the neutral tokens (`--bg-2`, `--line`, `--fg-3`) so the same shell renders without project styling.

## Topbar shape (shared)

Home, Project, Work, Agent, and Connections all share the same chrome — brand on far-left, tools on far-right, divider at the bottom. WorkView's `.wv-topbar` is the canonical look. Home and Project sit inside `.home` (no max-width; `padding: 0 2.25rem 3rem`); the topbar uses `margin: 0 -2.25rem 1.5rem` to break out of the side padding and go edge-to-edge, picking up `.wv-topbar`'s elevated background. Same negative-margin trick on `.proj-hero` for the gradient bleed. The `:has(+ .proj-hero)` rule on the topbar drops its bottom margin so the project hero's tinted bar sits flush.

Project crumb pattern: `← Workspace` (`btn-ghost-sm` button, links to `/`) followed by a `crumbs` span with `/ [glyph chip] {Name}`. WorkView extends with another `/ {WRK-slug}` and a folder-pill on the right.

### UpdateChip

`UpdateChip.tsx` lives in every topbar (Home, ProjectScreen, WorkView) just before `TweaksToggle`. It polls `GET /api/update-status` every 10 minutes; when the backend reports `available=true` it renders a small accent-tinted pill ("Update available"). Clicking it reveals a popover with the repo path and a single primary action that copies `cd <repo> && claude` to the clipboard — the chip is a hint to run `/update` inside Claude, not a self-acting upgrader. Dismiss persists in `sessionStorage` keyed on the upstream SHA, so the chip reappears when a new upstream commit lands even if previously dismissed.

## AgentTile — modes

`AgentTile` is the same component in two contexts:

- `mode="page"` (`/agents/<slug>`): full-viewport, fixed `880px` max-width, no persona pip in header, no maximize control surface.
- `mode="tile"` (inside `WorkView`'s canvas): no fixed height, persona pip + agent name in header, persona-tinted top border via `--p-color`/`--p-soft`, maximize toggle that sets `position: fixed; inset: 1.5rem`.

The mode is a structural switch, not a theming switch. Splitting into two components would duplicate the streaming logic; one component with a mode prop is right while behavior is 95% shared. If divergence grows past the textarea + header, split.

### Header layout — 3-cell grid

`AgentTile` header is a CSS grid with `grid-template-columns: 1fr auto 1fr`:

- **Left** cell: persona pip + status dot + h2 title. h2 truncates with `text-overflow: ellipsis` so long names don't push the meta off-center.
- **Center** cell: `agent-slug` (mono) + `provider-pill` (`amp · rush`) + `conn-status` (`CONNECTED`) + a `folder-pill mono` showing `shortenPath(worktreePath)`. Left-click reveals the worktree in Finder; **right-click opens a small context menu** (`.folder-pill-menu`, anchored at cursor coords) with two options: *Open worktree* and *Open Atelier folder* (the per-agent dir under `~/Atelier/works/<work>/agents/<agent>/` — transcript, agent.json, contexts/). Backend dispatch via `POST /api/agents/{slug}/reveal?kind=worktree|atelier`. Center stays horizontally centered regardless of how wide the title or controls clusters get; the 1fr columns absorb the slack equally.
- **Right** cell: `tile-controls` wrapper with **open-in-IDE / handoff / maximize / detach / close** buttons. Buttons are 26×26 with 13×13 SVG glyphs; controlled by `.tile-controls .tile-ctl` so the meta/folder pill in the center stays at default sizing. The open-in-IDE button uses the selected editor descriptor from `GET /api/settings` (`url_template` plus path tokens such as `{path_uri}` / `{path_param}`) — browsers route unknown protocols to the OS handler without navigating, so the page stays.

The standalone worktree-icon button (formerly between conn-status and tile-controls) was removed — the folder pill is itself the reveal affordance. Path display shortening lives in `pathFormat.ts` for reuse by AgentTile/Chat surfaces without importing WorkView.

### Session Model And Effort Selectors

When the active provider advertises a mutable `model` session config option, `AgentTile` renders a compact `Model: <current>` selector in the composer action row next to `+ Add context`. Opening it sends `session_config_refresh` and shows a searchable popover because OpenCode model lists can be large; `session_config_options` seeds/refines the choices and current value, `session_config_changed` updates after a successful switch, and replay rebuilds the same state after reconnect. The selector is disabled while a turn is active so changes apply cleanly to future prompts. Providers without an advertised model option keep the existing static model display and do not show the composer control.

Agent and chat composers also render a compact effort selector when the provider advertises `thinking_effort`, `reasoning_effort`, or ACP's generic `effort` config option. It uses the advertised choices/current value rather than hard-coded frontend enums, is disabled while a turn is active, and persists accepted changes so future resume/detach paths keep the user's selection.

### Composer Image Paste

Agent and chat textareas accept pasted clipboard images. The shared frontend helper uploads png/jpeg/gif/tiff/webp files through `POST /api/fs/uploads/images`; work-scoped surfaces include the work slug so files land under that work's attachments folder. It first reads normal paste file items, then falls back to the async Clipboard API from the Cmd/Ctrl+V keydown path for macOS screenshot pasteboards that do not expose files on the paste event. Pasting inserts an immediate inline `[Image N]` marker in the textarea. Agent tiles append the returned paths as removable `file` contexts and allow sending with only the attached context. Chat surfaces keep the marker visible and append labeled file paths only to the sent message, because chat websocket frames do not currently accept context attachments.

### Context Compaction

`AgentTile` derives context pressure from the latest `turn_metrics.last_prompt_tokens` snapshot plus the provider/model context window (`frontend/src/AgentTile.tsx`). The status row above the composer shows the current git branch or `DETACHED HEAD` first, then latency, token usage, `ctx N%`, and the activity label. The composer carries the visual state: a 2px top-edge context gauge fills to the current percentage, and a 2px bottom-edge persona rail sweeps while the agent is working. At 75% the row shows a warn-colored inline **Compact** button; at 86% the context label, gauge, button, and modal primary action switch to the critical tone. Clicking **Compact** opens a blurred, outcome-led confirmation modal that snapshots the current context/tone, stays open while the async compaction runs, shows the current phase and elapsed time from `compaction_progress` events, then shows success or a retryable error. At 100% normal sends and tile actions are blocked: the modal opens automatically, can be dismissed so the user can inspect the last response, and reopens on the next attempted action; it can offer handoff when the parent supplied `onHandoff`.

This is intentionally not styled like a tool permission prompt. Permission prompts stay inline above the composer and use tool/security language; compaction uses context/cost language and composer-edge rails because it changes the provider session behind the same agent. The frontend calls `POST /api/agents/{slug}/compact` through `api.compactAgent`; the supervisor kicks the active websocket on session replacement so `useAgentStream` reconnects and replays the `context_compacted` transcript marker. Reconnects are generation-guarded so stale sockets from the compaction race cannot replace the current live subscription. `AgentTile` renders that marker as a session boundary: the previous transcript units collapse into a disclosure, and **View summary** lazy-loads the saved compaction doc via `GET /api/agents/{slug}/compactions/{filename}`. Provider-side automatic compaction uses `provider_context_compacted` instead; it renders as an informational boundary without a summary button because Atelier did not create a local compaction summary.

Chats do not become agents and do not use the AgentTile context-pressure modal.
`ChatView` and `ChatTile` reuse `TranscriptUnits` and `TurnMetricsBar`; when the
websocket replays a `context_compacted` event, the shared boundary lazy-loads
`GET /api/chats/{slug}/compactions/{filename}` through a chat-specific summary
loader.

## View-toggle pattern

`Tiles | List` segmented control used on both Project and Home. Implementation pattern:

- Two `.view-toggle-btn` siblings inside a `.view-toggle` flex container with a 1px border and a 1px divider between buttons.
- Default per surface: Project = **tiles**, Home = **list** (chronological feed reads faster as rows).
- Persistence keys: `atelier:project:{slug}:view` per project; `atelier:home:view` site-wide. Read on mount via a dedicated `readPersistedView(slug?)` helper that handles private-mode failures and falls back to the surface default.
- The `.proj-tabs` container variant adds `margin-left: auto; margin-bottom: 0.5rem` to keep the toggle aligned with the underlined tabs above; on Home (where neighbours are pills, not underlined tabs) the toggle uses the base `align-self: center` only.
- Button padding is tuned to `3px 9px` (matches `.filter-pill`) so toggle and pills sit on the same baseline when they share a row.

## `useAgentStream`

`useAgentStream(slug, { resource })` is the single point of contact with supervisor-backed websocket streams. The default resource is `"agents"` (`/api/agents/<slug>/stream`); chat surfaces pass `{ resource: "chats" }` for `/api/chats/<slug>/stream`. It returns `{ events, status, sendInput, sendStop, sendPermission, sendSessionConfig, pendingPermissions }` and handles:

**Cursor-based resume.** Within a session, on WS close + reconnect it appends `?cursor=<lastSeq>` so the server replays only the window we missed before going live. The server's replay-then-live semantics guarantee no duplicates and no gaps (see `backend.md` → WS protocol). The cursor lives in a closure-scoped `lastSeqRef` and resets to `0` on every fresh mount — the transcript itself isn't persisted client-side, so seeding non-zero on mount would yield an empty tile (the bug that retired the old `atelier:cursors` localStorage key).

**Paint-batched stream events.** Incoming WS frames are buffered for 50ms before appending to React state, with an immediate flush on socket close. `lastSeqRef` still advances as each frame arrives, so reconnect cursors stay exact while dense token streams avoid one React/Markdown/scroll pass per tiny delta.

**Exponential reconnect backoff.** Schedule: `1s → 2s → 4s → 8s → 16s → 30s` (cap). Resets on a successful `onopen`, so a single transient blip costs one 1s retry — only consecutive failures walk the ladder.

**Terminal close on 4404.** When the backend closes with code 4404 the slug is unknown to the server and the hook sets `status: "stopped"` and exits the retry loop. With the supervisor's resume path (see `backend.md` → WS protocol), a backend restart no longer surfaces 4404 — the WS handler rebuilds the adapter with the persisted `session_id` so the conversation resumes mid-stream. 4404 in practice means the slug doesn't exist (e.g. stale localStorage in the closed-rail state, or a deleted chat).

**Close = pin to rail.** The X on `AgentTile` is "close" — `WorkView` records the slug in `useClosedStore` and unmounts the tile. The WS connection ends; the agent row + `transcript.ndjson` + provider session ID stay on the server. Clicking the rail entry restores the tile, which mounts a fresh `AgentTile`, which opens a new WS, which resumes the same provider session by ID. There is no "delete" — closing is fully reversible by design.

**Esc = stop the current turn.** Inside the composer, plain Esc while the agent is producing a turn sends `{"type":"stop"}` over the WS. The supervisor records a `user_stop` transcript line and calls `adapter.stop_turn()`. Modifier+Esc combinations (Shift/Cmd/Ctrl+Esc) are reserved for "exit maximize" so they can't fire stop-turn. Esc on Amp agents currently no-ops at the adapter layer, but the user's intent still lands in the transcript.

## Connections page

`Connections.tsx` at `/connections`. One section per `ConnectionType` (`jira`, `sentry`, `honeycomb`), source-tinted via `data-source` (the same token system that drives context rows / connection chips). Each section lists existing connections as expandable cards with a Disconnect/Re-verify/Save footer, plus a "+ New" inline form.

**Form schema is declarative.** `connectionFields.ts` maps each source type to a `ConnectionSchema` of `{ id, label, placeholder?, required?, secret?, options? }` entries — same shape as the design source's `CONNECTION_FIELDS`. The grid renders selects for fields with `options`, password inputs (with reveal toggle) for `secret: true`, plain inputs otherwise.

**Verify flow.** New-connection Verify is the only network action that hits the server: first click POSTs the row + verifies; subsequent clicks PATCH the same `con-N` slug + re-verify. The Save button is just "close the form" — the primary action only enables once `verifyState === "ok"`, so the form can't close around an unverified row, and Save never re-POSTs. Re-verify on an existing card writes any pending token rotation via PATCH before calling `/verify` so the keychain has the latest.

**No token round-trip.** The `Connection` type returned by the API has no `token` field; the UI tracks token edits in form-local state only and sends them via POST/PATCH. The reveal toggle on the secret input shows whatever the user just typed, never anything fetched from the server.

## Composer

Multi-line auto-growing textarea, capped at 200px before scrolling. Enter submits, Shift/Cmd/Ctrl+Enter inserts a newline.

**Optimistic "thinking" status on send.** The status dot flips to `thinking` the moment you click Send, instead of waiting for the next `status_change` event from the adapter. Storage is `thinkingSinceSeq: number | null` — captures the latest event seq at Send time so a *new* progress event (any of `status_change`, `turn_metrics`, `error`, `message_delta`, `message_complete` with a later seq) clears it. The boolean precursor only watched for `status_change` as the *last* event, which got shadowed by intervening deltas — and Amp drops the trailing `status_change("idle")` on short turns, leaving the dot stuck. The seq-based gate plus the multi-event terminal set is the fix.

**Composer placeholder.** "Agent is working — Esc to stop" appears only when the agent is actively producing output, derived via `isAgentActive(events)` off the *last* event's nature (active = `message_delta` / `thinking_delta` / `tool_call` / `tool_result` / `user_input` / `status_change(thinking|live)`; everything else = inactive). Same fallback rationale: the cumulative `agentStatus` lies on Amp short turns; the last event tells the truth.

## Streaming + grouping

`AgentTile.groupEvents()` collapses runs of `message_delta` into a single growing assistant `RenderUnit`, and runs of `thinking_delta` into a single thinking unit. This is what lets you see text accrete instead of seven separate chunks. A non-delta event (`tool_call`, `status_change`, `user_input`, …) closes any pending delta unit and starts its own.

**`tool_call` and `tool_result` are paired.** When a `tool_result` event lands with a `tool_id` matching a `tool_call` already in the unit list, it's folded into the call's render unit (`unit.result`) rather than appearing as a sibling line. Orphan `tool_result`s (no matching call — replay races, suppressed-call edge cases) still render as standalone units.

**Per-tool renderers.** A `TOOL_RENDERERS` dispatch maps each canonical tool name (see `backend.md` → "Canonical tool shape") to a specialised view:

- **`Bash`** — collapsed summary echoes the command (`▸ Bash · git diff`); body has the description, `cwd`, the command in a syntax-highlighted code fence, and the paired result (auto-unwraps Amp's `{output, exitCode}` JSON).
- **`Edit` / `MultiEdit`** — collapsed summary shows `path · +N −N`; body renders a line-level diff via `diffLines` from the `diff` package, syntax-highlighted per side via `codeToTokensBase` from Shiki using the language inferred from the file path. Open by default since the diff *is* the body.
- **`Write`** — `path · N lines` summary; body shows the content fenced with the inferred language.
- **`Read` / `Grep` / `Glob`** — single-line view (`▸ Read · ~/…/foo.py · L1-100`) collapsing to `<details>` only when there's a result to fold in.
- **Fallback** — unknown tool names render the args dict as a JSON code fence (today's behaviour).

Folding policy: `<details>` is **closed by default**, opens automatically when the result content is a unified diff (`isUnifiedDiff` matches `diff --git` / `@@ -.. +.. @@`). `Edit` / `MultiEdit` always open since their *call* body is a diff.

**Result rendering.** `ToolResultBody` parses Bash results as `{output, exitCode}` (Amp's wrapper), surfaces `exit N` next to "result" when non-zero, and detects unified-diff content to render with `<UnifiedDiffView>` — which extracts the file path from `diff --git a/PATH b/PATH` so the lines also pick up syntax highlighting via Shiki. Falls back to plain `<pre>` for other content.

**ACP event granularity (STORY-033).** ACP providers stream richer events; all handled in `groupEvents`/`renderUnitFor` with zero provider branches: `plan_update` reuses the todo-list row (`parsePlanEntries` — full-replacement semantics, each update pushes a fresh snapshot like TodoWrite does); `tool_call_update` folds a live `status` into the matching tool card (never a row of its own); `tool_result.diff` is a structured `{path, old_text, new_text}` that `DefaultToolCallView` renders through the real `DiffView` even when the tool name isn't canonical (codex-acp's opaque args land here); `mode_change` renders as a status line. `session_config_options` / `session_config_changed` are transcript events but intentionally not visible transcript rows; `AgentTile` consumes them to render mutable controls such as OpenCode's refresh-on-open, searchable live model selector. `PermissionApprovalDialog` renders the `pendingPermissions` queue from `useAgentStream`: one pending request shows the single approval card, 2+ requests switch to the grouped queue with Allow all / Reject all convenience actions, and every bulk action still sends one `permission` WS frame per request. Permission buttons stay generic (`Allow`, `Always allow`, `Reject`) while ACP option kinds drive the actual response; agent-named option labels are only used as fallbacks/tooltips and to infer the capability name when older events carry a human action title as `tool_name`. Session cost prefers the provider-reported cumulative `turn_metrics.cost_usd` (latest wins — never summed) over token-math estimates (`computeSessionCost`). Unknown event types still fall through `renderUnitFor`'s `default: null` — older builds ignore newer events by construction.

## Design tokens

Lifted from `design/design_handoff_atelier/design_files/styles.css` (gitignored — keep it in sync if it changes upstream).

**Dark is the default at `:root`; light is opt-in via `[data-theme="light"]`.** `App.tsx` mirrors `useThemeStore.theme` onto `<html data-theme=...>`, and the override block in `styles.css` swaps the background ramp, foreground ramp, lines, status hues, and shadow stack to light values. Persona tints are intentionally untouched — the same hue reads on both themes. The `<ThemeToggle>` component sits in the Home + WorkView topbars and flips the store; the choice persists under `atelier:theme` in `localStorage`.

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

Both dialogs (`NewWorkDialog`, `NewAgentDialog`) keep the surface small. Advanced controls land as the stories that own them ship; the dialogs grow rather than carrying stub UI ahead of time.

Both dialogs accept an optional `contexts` array of `ContextEntry`. `NewWorkDialog` exposes one button per `ConnectionType`; `NewAgentDialog` exposes the connection-backed types **plus** simple `text` / `url` / `file` types. Connection-backed entries render through `ContextRow` (see below); the simple types render through `SimpleContextRow.tsx` — a stripped-down card with a textarea (text) or input (url, file) and a remove button.

**Working folder + Branch row.** The two fields share one `.field-row` (folder grows; branch is fixed-width). The folder field opens a Finder-style `FolderPickerDialog`; the branch field has an inline `BranchPicker` popup (click-outside dismiss, autofocused filter input, Enter-to-pick when one match remains, Esc to close). The picker calls `GET /api/git/branches?path=<folder>` lazily on first open and caches the result until the folder value changes. Blank branch name = detached HEAD from the repo integration branch (`master` when present, otherwise `main`; see `backend.md` → WorktreeManager); typing or picking a name creates that branch from the same resolved base on agent start. Disabled in fork mode because forks inherit the source agent's current HEAD plus working state.

## ContextRow

`ContextRow.tsx` — the inline connection picker / creator used inside dialogs. Two modes:

- **pick**: dropdown of existing connections of the row's type + per-context value input (ticket key, event ID, etc). Sentinel `+ New <type> connection…` flips into "new".
- **new**: inline form with the same field schema as the Connections page (`connectionFields.ts`). Verify is the only network action; first click POSTs the row + verifies, subsequent clicks PATCH the same `con-N` slug + re-verify. Save commits the row to the parent dialog's connections list and the context's `conn_id`. Cancel from "new" with no fallback connections **and** nothing yet committed removes the row entirely — the headline cancel-removes-half-state interaction.

Parents own the connections array and pass it down. `ContextRow` calls `onConnectionSaved(connection)` once on save, so the dialog can dedupe and show the new connection in subsequent picker dropdowns. Verified state is reflected on the dropdown (`name · ✓`) so the user can tell at a glance which creds are confirmed.

## Provider-driven `NewAgentDialog`

The dialog renders its provider/model fields from the descriptors at `GET /api/providers`. The provider list is the backend's new-session surface (`NEW_SESSION_PROVIDERS`), so legacy runtime ids can remain resumable without appearing in creation. The primary field renders through a fixed-height searchable `ModelPicker`, which avoids native-select growth for long model lists and supports keyboard navigation. OpenCode starts with the `configured-default` fallback, then calls `GET /api/providers/opencode/models?refresh=true` when selected so connected provider models can be chosen before launch. New Chat uses the same picker/OpenCode refresh path and exposes permission/effort options inline from the shared descriptor helpers.

Advanced per-provider options (Claude's `thinking_effort`, `permission_mode`) render in a collapsible "Advanced" `<details>` block when the descriptor's `options` map is non-empty. Each entry becomes a labeled `<select>`; defaults seed from the descriptor and reset on provider change. Shared helpers in `providerDescriptors.ts` narrow matching effort selectors when `model_meta` publishes model-specific option values/defaults, and only coerce the current value if the selected model no longer supports it. New Chat only sends non-default option entries; New Agent keeps its existing behavior of sending the populated option dict when options exist.

## State

Component-local `useState` + the WS hook is the default. Cross-component, frontend-only state lives in narrow Zustand stores under `frontend/src/state/`, persisted to `localStorage` via the `persist` middleware where it should survive reload.

Current stores:

- `settings.ts` — `{ theme, editor, terminal, accentHue, editorOptions, terminalOptions }`, hydrated from backend `GET /api/settings` and written through with `PUT /api/settings`. The backend owns the selectable editor/console descriptors (`label`, `command`, `url_template`); the FE only renders them and interpolates path tokens for editor URL handlers. Legacy `atelier:tweaks` / `atelier:theme` localStorage blobs are migrated into the backend once on boot.
- `closed.ts` — `{ byWork: Record<workSlug, agentSlug[]> }`, persisted under `atelier:closed`. `WorkView` filters closed agents out of the canvas; clicking a closed rail entry restores the tile (which reopens its WS and resumes the provider session). Replaces the prior "minimized" model — there is no "delete".

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
- [x] Light-theme tokens + theme toggle
- [x] `NewAgentDialog`: collapsible Advanced section for provider `options`
- [x] Inline context attachments in `NewWorkDialog` / `NewAgentDialog` (per-agent contexts: `text`/`url`/`file` plus connection-backed)
