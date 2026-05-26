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
theme-stable. Full list in [`frontend.md` → Design tokens](frontend.md#design-tokens).

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

## Section headers

`.latest-title` is the shared treatment for **Projects** and **Latest work**
labels: 13px, uppercase, `letter-spacing: 0.08em`, `font-weight: 600`,
`color: var(--fg-2)`. The trailing count uses `var(--font-mono)` at the
same size, `var(--fg-3)`.

When a header carries pills/toggles in addition to the title, anchor it
with a hairline divider — `border-bottom: 1px solid var(--line)` plus
`padding-bottom: 0.7rem` on the header row, and ~1rem gap between header
and the content below. Without that, busy header rows visually float above
their cards. See `.latest-hd` for the canonical pattern.

## Card rhythm

Workspace cards favor breathing room over density:

- `.proj-card` — min-column 300px, min-height 160px, padding ~1.2rem,
  name 16px, count number 22px (mono).
- `.work-card` (tile view) — narrower, denser; meta line at top, then
  description, then `.wc-stats` row at the bottom.
- `.work-row` (list view) — single line, all meta inline.

If a card needs a variant, add a class on top of the base rather than
restyling the base — the base sets the visual contract for the workspace.

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
