# README screenshots

Screenshots embedded in the top-level `README.md`. Filenames are stable —
overwriting an existing PNG keeps the README references intact.

## Current set

| File | Where it appears | What it shows |
| --- | --- | --- |
| `home.png` | Hero, top of README | Workspace home — projects + latest work tiles |
| `agents.png` | "One workspace, many agents" | A Work unit canvas with two agents streaming in parallel |
| `agent_details.png` | "Detach to CLI, come back seamlessly" | Agent header close-up — provider pill, CONNECTED status, worktree path, detach/maximize/CLI/close controls |
| _(detach demo video)_ | "Detach to CLI, come back seamlessly" | End-to-end detach flow video. **Hosted on GitHub's user-attachments CDN**, not in the repo — committing video files at relative paths doesn't render inline on github.com. To replace, drag a new `.mp4` (≤10 MB) into a GitHub issue comment, copy the resulting `https://github.com/user-attachments/assets/...` URL, paste it into the `<video>` tag in `README.md`. |
| `new_agent.png` | "Source-backed context" | Launch-new-agent dialog with persona/provider picker + context attachments |
| `project.png` | "Optional projects" | Project detail page (PRJ-001 Atelier) with active/completed counts |
| `new_project.png` | "Optional projects" | New-project dialog with name/glyph/color/default-connection fields |

## Format

- **PNG**, no JPEG (sharp text matters more than file size).
- **Hero / wide shots**: ~1600px wide source, the README scales to ~1100.
- **Dialogs / portrait shots**: ~1000px wide source, the README scales to
  540–700.
- **Theme**: dark is on-brand; light or ANSI fine if it shows the feature
  better.
- Crop tight — full-window chrome (browser title bar, tabs) just adds noise.

## Adding more

If you capture another shot you want in the README, drop it here under a
descriptive name and ask Claude to "wire `<filename>` into the README"
with the section it belongs in. The pattern is:

```md
<p align="center">
  <img src="docs/screenshots/<file>.png" alt="<descriptive>" width="1100">
</p>
```
