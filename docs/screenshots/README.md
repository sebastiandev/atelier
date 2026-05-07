# README screenshots — capture checklist

This folder holds the screenshots embedded in the top-level `README.md`.

When ready, capture the shots below and save them under the names listed.
Once they're in this folder, ask Claude to "wire the README screenshots" —
it'll add the `<img>` markup with proper sizing/alt text and re-push.

## Format

- **PNG**, no JPEG (sharp text matters more than file size).
- **Hero shot**: ~1600px wide. **Inline shots**: ~1200px wide.
- **Theme**: dark is on-brand; light or ANSI fine if it shows the feature
  better.
- Crop tight — full-window chrome (browser title bar, tabs) just adds noise.
  The Atelier topbar is enough to anchor "this is Atelier".

## Shots

### 1. `workspace.png` — hero

Home (`/`) showing:

- A few **project cards** (more than one, ideally one with recent work and
  one Loose work card).
- The **Latest Work** section header with the divider, filter pills, and
  the Tiles/List view toggle.
- Some work cards or rows below, with **agent/artifact counters** visible
  on at least one of them.

This is the very first thing a reader sees — sells "one workspace for all
your agent activity" in one frame.

### 2. `work-canvas.png` — multi-agent

A WorkView (`/works/<slug>`) showing **2–3 agent tiles streaming in parallel**.

- Tiles should be live (status dots green / mid-turn).
- At least one tile mid-message so streaming text is visible.
- Different personas if possible (different pip colors).
- Rail on the side with at least one closed/pinned agent — that's the
  "close = pin to rail, restore later" story.

### 3. `agent-tile.png` — the goodies

Close-up of a single agent. Either page mode (`/agents/<slug>`) or one
focused tile from the canvas (zoom in if needed). The header should show:

- **Persona pip** + status dot + agent name (left).
- `agt-N` slug + **provider pill** (e.g. `amp · rush` or `claude · sonnet-4-6`)
  \+ `CONNECTED` + **folder pill** with a shortened path (center).
- **Detach / maximize / close** buttons (right).
- A few transcript turns visible below — including a tool call or a
  thinking block if you've got one — so the streaming model reads.

This shot earns "multi-provider" and "detach to CLI" simultaneously; it's
the one to spend the most care on.

### 4. `new-agent-dialog.png` — source-backed context

`NewAgentDialog` open with at least one **context attached** — a Jira
ticket row, a Sentry event, or a Honeycomb trace. Show the verified pill on
the connection if you have it.

If the **Advanced** section makes sense (Claude provider with thinking
effort / permission mode visible), expand it — drives home that providers
have real options exposed without code changes.

### 5. `detach-flow.png` — optional but distinctive

The moment after clicking **Detach**: Atelier on one half of the screen,
your terminal on the other half showing the resume command that was
auto-launched. The detached agent's tile in Atelier should show the
"detached" state.

This one's the most distinctive feature in the README. If you can stage it
cleanly (record a short flow if needed and screenshot the best frame), it
goes a long way.

## Integration target

Once the files are in this folder, the README will get image blocks roughly
like:

```md
<p align="center">
  <img src="docs/screenshots/workspace.png" alt="Atelier workspace" width="1100">
</p>
```

(Centered, alt text, sane width — Claude will handle the placement.)
