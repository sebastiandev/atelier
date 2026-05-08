<p align="center">
  <img src="scripts/launchers/icons/atelier.png" alt="Atelier" width="120">
</p>

<h1 align="center">Atelier</h1>

<p align="center">
  Run a small team of AI agents on a real task — across providers, with full
  history, without losing your place when you switch to the terminal.
</p>

<p align="center">
  <img src="docs/screenshots/home.png" alt="Atelier workspace — projects and latest work" width="1100">
</p>

---

## Why

You probably already use Claude Code, Amp, or a similar CLI. They're great. But:

- Each agent lives in its own terminal. Tracking five concurrent conversations
  means five tabs and a memory of which one was doing what.
- Providers don't talk to each other. If today's task wants Claude's reasoning
  *and* Amp's tooling, you bounce between two apps.
- The CLI is great for deep work — a UI is better for *"what are all my agents
  doing right now?"*
- The moment you exit the CLI, that conversation is over. Resuming usually
  means losing the live thread.

Atelier is a workspace that wraps these tools instead of replacing them.

## What you get

A quick tour of the surface area:

- [One workspace, many agents](#one-workspace-many-agents) — Work units with live tiles
- [Multi-provider](#multi-provider) — Claude and Amp side-by-side
- [Detach to CLI](#detach-to-cli-come-back-seamlessly) — terminal handoff with full resume
- [Hand off to a new agent](#hand-off-to-a-new-agent) — checkpoint doc + forked worktree
- [Artifact detection](#artifact-detection) — PRs, Jira tickets, docs surface on a rail
- [Re-organize the canvas](#re-organize-the-canvas) — drag tiles into the order you want
- [Maximize a tile](#maximize-a-tile) — zoom one agent to fill the canvas
- [Keyboard shortcuts](#keyboard-shortcuts) — one-key flows for the common moves
- [Source-backed context](#source-backed-context) — Jira / Sentry / Honeycomb on first turn
- [Per-agent git worktrees](#per-agent-git-worktrees) — automatic, isolated
- [Persistent everything](#persistent-everything) — transcripts on disk, conversations resume
- [Projects](#projects) — optional grouping with shared defaults

### One workspace, many agents

Group agents into a **Work unit** — a single goal like *"STORY-018 connections
page"* — and watch them in parallel. Each tile is one agent, streaming live.
Pin tiles to a rail when you want them out of the way; bring them back without
losing the conversation.

<p align="center">
  <img src="docs/screenshots/agents.png" alt="Two agents (Architect and Developer) streaming in parallel inside a Work unit" width="1100">
</p>

### Multi-provider

Claude (via the SDK) and Amp (via the CLI) are both first-class. Pick a
provider per agent — one persona on Claude, another on Amp — and they share
the same Work unit, the same context, the same transcript log.

### Detach to CLI, come back seamlessly

Sometimes the UI gets in the way. Click **Detach**: Atelier stops the SDK
process, opens your terminal with the provider's resume command, and hands
the agent over. Work in the CLI as long as you want. When you're done, the
agent reattaches in Atelier with the full transcript intact — including
everything that happened in the terminal.

<p align="center">
  <img src="docs/screenshots/agent_details.png" alt="Agent header — provider pill (claude · opus-4-7), CONNECTED status, worktree path, detach / maximize / CLI / close controls" width="1100">
</p>

<p align="center">
  <video src="https://github.com/user-attachments/assets/97d23025-be69-4fcd-94da-e7209c7e7423" controls muted width="900">
    Detach flow demo — your browser couldn't play this inline.
  </video>
</p>

### Hand off to a new agent

When an agent's done its piece — planning's complete, the bug's diagnosed,
the design doc's written — hand the work off to a fresh agent without
losing context. Atelier summarises the source's transcript into a Markdown
checkpoint (goal, decisions, open questions, key files, blockers), then
opens the new-agent dialog pre-filled with the doc as the next agent's
starting context. The new worktree is **forked** from the source's at its
current HEAD with all uncommitted work carried over — detached HEAD, no
auto-branch; you name one when you're ready.

### Artifact detection

Agents announce the things they produce — pull requests, Jira tickets,
brand-new documents — via dedicated tools that Atelier surfaces on the
work's artifact rail. Click a PR or ticket row to open the URL; click a
doc to reveal the file in your OS file browser. No more scrolling through
twenty turns of transcript to find the link the agent dropped earlier.

<p align="center">
  <video src="https://github.com/user-attachments/assets/559e75f2-ff56-42c9-9bd1-1d5c88caca42" controls muted width="900">
    Artifact detection demo — your browser couldn't play this inline.
  </video>
</p>

### Re-organize the canvas

Drag any tile by its header to put your agents in the order that makes
sense for the work. The arrangement is per-work, persisted across reloads,
and the rail mirrors the canvas so both stay in sync.

<p align="center">
  <video src="https://github.com/user-attachments/assets/5105d2e0-0325-41de-9bd0-01c1e4fe3b39" controls muted width="900">
    Re-organize agents demo — your browser couldn't play this inline.
  </video>
</p>

### Maximize a tile

Click the maximize control on a tile header to zoom that agent to fill the
canvas — handy when you want to read a long transcript or scan a tool's
full output without the other tiles competing for space. Shift+Esc /
Cmd+Esc restores the multi-tile layout.

<p align="center">
  <video src="https://github.com/user-attachments/assets/9851b463-20ac-49de-8039-ddf3e9bc3fda" controls muted width="900">
    Maximize tile demo — your browser couldn't play this inline.
  </video>
</p>

### Keyboard shortcuts

Atelier surfaces a small set of one-key shortcuts so you can drive the
common flows without reaching for the mouse. From the workspace, **W**
opens new work and **P** opens new project. Inside a Work unit, **N**
opens new agent. Inside a live tile, **Esc** stops the agent's current
turn — same semantics as the CLI.

<p align="center">
  <video src="https://github.com/user-attachments/assets/636842d7-bf2e-49a7-ab70-338a6f5b4b2f" controls muted width="900">
    Keyboard shortcuts demo — your browser couldn't play this inline.
  </video>
</p>

### Source-backed context

Plug in your Jira, Sentry, or Honeycomb credentials once. Pull a ticket, an
error, or a trace into an agent's starting context — Atelier fetches the full
payload and renders it as a context file the agent reads on its first turn.
Add more context mid-session without restarting.

<p align="center">
  <img src="docs/screenshots/new_agent.png" alt="Launch new agent dialog — persona picker, provider picker (Claude / Amp), initial goal, context attachments (Text / URL / File / Jira / Sentry)" width="700">
</p>

### Per-agent git worktrees

Atelier provisions a separate `git worktree` per agent automatically. Two
agents on the same repo don't step on each other's branches. When you're done,
the worktrees are still there for review.

### Persistent everything

Close a tile, restart the backend, reboot your machine — the transcripts are
on disk (one NDJSON per agent), the SQL index gets reconciled against them on
startup, and the next time you open the agent the conversation picks up
exactly where it left off.

### Projects

Long-running effort? Wrap a set of Work units in an optional **Project**
with a glyph and a hue. Per-project default connections, filtered work
feeds, and a project home page that scopes everything to that effort.

<p align="center">
  <img src="docs/screenshots/project.png" alt="Project page — Atelier project header with active/completed counts and a Support Codex work card" width="1100">
</p>

<p align="center">
  <img src="docs/screenshots/new_project.png" alt="New project dialog — name, glyph, color picker, optional default Jira / Sentry connections" width="540">
</p>

## Quick start

You'll need **Python 3.11+**, **Node 18+**, and [`uv`](https://docs.astral.sh/uv/)
for the backend env.

```sh
git clone https://github.com/sebastiandev/atelier.git
cd atelier
./scripts/dev.sh
```

The frontend serves at `http://127.0.0.1:4173`, the backend API at
`http://127.0.0.1:8001`.

### One-click desktop launcher

Prefer double-clicking an app icon over typing a script?

```sh
# macOS / Linux
./scripts/install-launcher.sh

# Windows (PowerShell, requires Git Bash on PATH)
powershell -ExecutionPolicy Bypass -File scripts\install-launcher.ps1
```

This drops an `Atelier.app` (macOS), `atelier.desktop` entry (Linux), or
Start Menu + Desktop shortcut (Windows) that launches the dev servers in a
terminal window.

## Status

Atelier is early. The core loops — multi-agent Work units, multi-provider,
detach/resume, connection-backed context — are working. Lots of polish and
feature surface still ahead; expect rough edges, but the workflow is real.

## Going deeper

The codebase has its own developer docs in [`docs/`](docs/):

| Doc | Scope |
| --- | --- |
| [`architecture.md`](docs/architecture.md) | Clean architecture layers, the command pattern |
| [`backend.md`](docs/backend.md) | Supervisor model, persistence, WS protocol |
| [`frontend.md`](docs/frontend.md) | Routing, state, the agent stream hook |
| [`design-system.md`](docs/design-system.md) | Tokens, brand mark, visual conventions |
| [`api-flows.md`](docs/api-flows.md) | Sequence diagrams per endpoint |

For AI assistants working on the codebase, see [`CLAUDE.md`](CLAUDE.md).

## License

[MIT](LICENSE) — use it, fork it, ship it. Attribution appreciated, not required beyond what the license itself asks for.
