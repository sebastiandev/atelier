# Design system

Visual conventions for the Atelier UI. The *what* (tokens, components) lives
in `frontend/src/styles.css`; this is the *why* and the *when*.

> Pair with [`frontend.md`](frontend.md) — that doc covers routing, state,
> and structural seams. This one covers the look.

## Tokens

All colors, fonts, radii, and shadows are CSS custom properties on `:root`
in `frontend/src/styles.css`. Theme variants override the same names under
`[data-theme="light"]` / `[data-theme="ansi"]`. **Never hardcode a hex or
oklch literal in a component** — pick the matching token, or add one to
`:root` if a use case warrants it.

The accent (`--accent`) is user-tunable via `TweaksPanel` (slider drives
`--accent-h`); `--accent-soft` / `--accent-line` derive from it via
`oklch()`. Status hues (`--good`, `--warn`, `--danger`, `--info`) are
theme-stable and each owns a `-soft` and `-line` ramp. `--hint` adjusts
soft/line intensity per theme without component-specific color mixing. Full
list in [`frontend.md` → Design tokens](frontend.md#design-tokens).

## Tonal hierarchy

Direction D is borderless and tonal. Surfaces move one step at a time:

- `--bg`: page/canvas
- `--bg-1`: rails, topbar, cards, framed tools
- `--bg-2`: controls, inputs, selected rows
- `--bg-3`: hover and nested control state

Cards and controls do not draw borders. A line is reserved for a real seam,
tree connector, resize handle, or focus/selection state. Floating layers use
`--bg-2`, `--radius-pop`, and `--shadow-pop`; ordinary cards never cast a
shadow.

Search and plan-reference pickers use that floating-layer recipe. Tool
approval is the exception: it grows the existing `--bg-2` composer dock
upward, joined by a 2px accent seam, rather than introducing another card.

## Brand mark — Constellation

Four-node graph with one off-axis focal node and a halo ("spark"). The
asymmetry is intentional; don't recenter or "balance" it.

- **Master**: `scripts/launchers/icons/atelier-app-icon.svg` (1024×1024 with
  tile + sheen baked in). Re-render binaries via
  `scripts/launchers/icons/build-icons.sh` — produces `Atelier.icns`,
  `atelier.png`, and `Atelier.ico` for the desktop launchers.
- **Topbar pip**: `.brand-mark` is an 18×18 tile rendered via CSS mask. The
  glyph URL lives in `:root` as `--brand-mark-glyph` (inline
  `data:image/svg+xml`). Tile color tracks `--accent`; the mark stays white
  (background paints through the mask). Don't redraw the geometry.
- **Topbar variants**: when on the accent tile, mark is white. When inline
  without a tile (e.g. monochrome surfaces), mark inherits `currentColor`.

The original handoff with full design rationale is at
`design/design_handoff_atelier_icon/` (gitignored).

## Chrome and headers

Application chrome uses `--font-mono` at 11px, weight 500, uppercase, and
`0.05em` tracking (`--chrome-*`). This applies to rail section labels, mode
labels, compact counts, and control labels. Prose, document headings, card
titles, and explanatory copy remain Inter with sentence case.

`ShellTopbar.tsx` is the stable 48px app header. Its origin lane matches the
active rail width and holds the Atelier wordmark plus project/work
breadcrumbs; an optional view lane holds full-view identity, inline metadata,
and status; primary and utility actions stay at the right edge. Full-view
surfaces such as the Loop library/editor use this lane instead of drawing a
second chrome bar. Content-column titles such as Settings sections and run
headers remain in the canvas as Inter content voice. Mode identity still
belongs in the rail (`Planning`, `Loop`, etc.), and a rail must not repeat the
wordmark or breadcrumbs. Dense tile toolbars remain local because they control
a tool rather than identify the route.

Breadcrumbs are navigation, not decoration. The Atelier wordmark is the root
link, so do not add a `workspace` crumb. Every routed or stateful ancestor is
clickable; only the current leaf is plain text with `aria-current="page"`.
Nested views keep the view they came from as the final clickable ancestor
(`settings / loops / create loop`, `work / plan overview / ST-04 / run`) and do not
repeat that navigation with a back arrow. Route ancestors use links; in-place
views use breadcrumb buttons that restore the parent view.

Section headers are unframed. Counts use a compact `--bg-2` tag. Add a line
only where the header is also a structural seam; do not add decorative
dividers to make unframed content feel like a card.

## Responsive shells

The 48px topbar remains a stable first row at every width. View detail hides
before the title, and the origin lane yields to the view title on narrow
screens. Below 700px,
Settings turns its vertical rail into a horizontal scrolling navigation row,
and standalone Loop mode hides the definition rail and resize handle so setup
and run content use the full viewport. Dense secondary metadata may disappear,
but primary names, status, and actions must remain available. The update banner
moves to the bottom edge on narrow screens so it never covers breadcrumbs or
topbar controls.

Loop-editor stage rows use the same internal person icon as Manual work for an
agent task. Keep a visible gap between the kind node and stage card, and keep
file/folder context selection behind the shared picker rather than a free-text
path control. The editor topbar carries breadcrumbs and save state only; loop
name and storage location sit above Description in the canvas.

## Chat surfaces

Exploratory chat uses the design handoff's production variants only:
section placement, spotlight launch, and summary promotion. There are no
in-app mode pickers.

- **Chat sections** use the existing section-header rhythm and `.v3-chat-row` rows: small chat bubble, title, grounding, age, and promoted-work pill when present. Home shows only unassigned chats, Project shows only project-level chats that are not in a work, and Work shows work-grounded/promoted chats. Project chat rows keep the associated work/project context directly under the chat title so wide rows do not push that association to the far right. Dense Work rail rows show the chat icon + title only, reserve left padding for the focused accent, and expose rename/delete through the same double-click/kebab pattern as agent rows.
- **New chat** is a spotlight composer (`.chat-bar-scrim` / `.chat-bar`) instead of a full modal. It should feel quick: one large textarea, compact provider/model/permission controls, grounding and working-folder chips, and one primary Start action.
- **Chat view** follows `shell-v3 narrow-left`: grounding and model in the rail, centered transcript in the main column. It consumes the runtime websocket stream and reuses the AgentTile transcript units and metrics bar so message, tool, compaction, and context-progress treatment stays consistent with agents.
- **Work chat tiles** use the agent-tile frame but not agent persona colors. Anything with `data-chat="true"` sets `--p-color` / `--p-soft` from the fixed chat token family (`--chat-color`, `--chat-soft`, `--chat-line`). Chat tiles expose maximize, close, and start-agent-from-chat controls, but never IDE, console, reveal-worktree, detach, persona controls, or a duplicate header compact button. Work-grounded chat tiles do not repeat the current work as a grounding pill.
- **Chat compaction** is fixed-accent, not persona/project colored. Compact actions are neutral local controls; `context_compacted` uses the shared AgentTile compaction boundary with a chat summary loader. Provider-side auto compaction uses the softer informational boundary and never shows a summary action.
- **Work hero actions** keep the Work action labeled but neutral (`Mark done` / `Reopen` via `.work-hero .pills .btn`) and make secondary chat/move actions icon-only 28px buttons (`.btn.icon.sm`) with tooltips. Keep the colored primary treatment for canvas-level creation actions such as `New agent`.
- **Work rail scrolling** keeps Shared folders, Active agents, Chats, and Artifacts as separate scroll bodies when they overflow. Rail scrollbars use a subtle neutral thumb from the current theme rather than browser defaults or project/persona accents. The Artifacts header can open a compact local filter for title, slug, type, status, source, and path metadata.
- **Promotion** uses the summary modal shape (`.promote-summary-modal`) with the chat provenance card at the top. The modal is about confirming the work seed, not choosing promotion modes.
- **Context docs** use a document viewer modal (`.context-doc-modal`) with the generated `context.md` rendered as simple headings, bullets, and paragraphs, plus a direct link back to the source chat.

## Documents and loops

Rendered Markdown uses the `.doc` recipe: Inter 15px/1.7, 720px measure,
sentence-case headings with visibly stronger hierarchy, and mono code. Inline
editing is section-based; an edit icon appears on hover, Save/Cancel are
explicit, and Esc returns to preview without changing the stored document.

Loop stages use semantic kind tints independent of agent persona: task =
warn, review = review-magenta, deterministic check = good, human approval =
info. Selector, library, editor, Planning artifact runs, and standalone Loop
mode all consume the same definition and stage components. A selected stage
highlights its existing connector; selection never changes connector width or
layout. Run-level provider/context overrides do not mutate the definition;
editing stages or transitions opens the structure editor and forks a built-in
to the repository.

## Shape and cards

The shape scale is fixed: tag/control/card 2px, tile 3px, input 6px, popover
8px. Text pills and fully rounded rectangles are retired. Status, scope, and
count labels use the 2px tag recipe; icon buttons use the control recipe.

Cards are for repeated records, modals, and genuine tools. They use `--bg-1`
or, inside an existing `--bg-1` surface, `--bg-2`; no border and no shadow.
Page sections remain unframed. Never put a card inside another card. Agent
tiles are the deliberate richer exception: a persona-colored 2px top edge
identifies ownership while the rest of the tile remains tonal.

Semantic status tags keep their ramp tone in every view: ready/done use good,
draft/review/pending use warn, running uses info, and blocked/gated/failed use
danger. Planning's `.pm-spill` follows this mapping through the shared tag
recipe; generic tag styling must not flatten those states to neutral.
Planning artifact rows pair the muted kind (`STORY`, `SPIKE`, `BUG`, `TASK`)
with the bright path basename; the descriptive title belongs in the document
view, not beside a filename that repeats it.

## Inline icons

Project-internal SVG icons (`GridIcon`, `ListIcon`, `AgentIcon`,
`ArtifactIcon`, `WorkStatusIcon`) follow a tight contract:

- **Size**: 11–14px box, `viewBox="0 0 12 12"`.
- **Fill / stroke**: `currentColor` so context drives color.
- **Accessibility**: `aria-hidden="true"` (decorative; the surrounding
  text or `title` carries the label).
- **Muted opacity**: `opacity: 0.75` when paired with mono numerals so the
  number reads first.

Don't import an icon library. The set is small, lifting per-icon keeps the
bundle lean and the visual language consistent.

## Time formatting

`formatDate(iso)` in `Home.tsx` does relative + absolute:

- < 1m: "just now"
- < 60m: "Nm ago"
- < 24h: "Nh ago"
- 1 day: "yesterday"
- < 7d: "Nd ago"
- < 30d: "Nw ago"
- ≥ 30d: absolute "Mon D" (adds year if different from current)

The 30-day cutoff is the design call: a month is the active-work window
where relative reads better than dates; beyond that, dates anchor.

`ProjectScreen.tsx` and `Connections.tsx` keep their own absolute-only
`formatDate` — appropriate there. Centralize into a shared module only
when a fourth caller needs the relative version.

## Stat badges

`.wc-stats` row + `.wc-stat` span is the canonical "icon + small number"
pattern (agent count, artifact count, …):

```css
.wc-stat {
  display: inline-flex;
  align-items: center;
  gap: 0.3rem;
  font-family: var(--font-mono);
  font-size: 11.5px;
  color: var(--fg-3);
}
```

Mono + `--fg-3` keeps badges visually subordinate to the title and
description. Always show the number even when zero — consistency reads
better than conditional empty states on a glanceable card.

## Agent Composer Status

Agent tiles use the composer chrome for two persistent signals. The
context budget is a 2px top-edge gauge on `.composer`, filled by
`--ctx-pct` and tinted with `--info`, `--warn`, or `--danger` through
`data-ctx-tone`. The agent activity signal is a separate 2px bottom-edge
rail that uses `--p-color` and only sweeps while `.composer.is-working`
is present. Keep those signals on opposite edges: top means context
capacity, bottom means live activity.

The compact action belongs in the mono status row above the composer,
not in a second alert strip. It appears at the warning threshold and
uses the same tone as the `ctx N%` label and top-edge gauge so the three
parts read as one compaction affordance.

## Persona / project tinting

Components that need per-persona or per-project hue declare it inline as a
custom property: `style={{ "--p-color": ... }}` (persona) or
`style={{ "--proj-h": ... }}` (project hue 0–360). The token system in
`styles.css` derives the rest of the ramp via `oklch()`. Don't pass full
color strings through props — pass the hue, let the cascade do the work.

- Persona tokens: `--p-color`, `--p-soft`. See
  [`frontend.md` → Persona theming](frontend.md#persona-theming).
- Project tokens: `--proj-bg`, `--proj-soft`, `--proj-line`. See
  [`frontend.md` → Per-project color tokens](frontend.md#per-project-color-tokens).

## One styles.css

`frontend/src/styles.css` is the only stylesheet. This is intentional —
co-located styles per component would duplicate token references and make
theme overrides harder to audit. Keep all rules here. If the file grows
past navigability, split by topic (tokens, layout, cards, …), not by
component.
