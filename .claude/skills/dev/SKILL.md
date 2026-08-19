---
name: dev
description: Start the Atelier dev servers (backend on 8001, frontend on 4173) and open the app. Wraps `./atelier launch`, which wraps scripts/dev.sh. Use when the user wants to start, restart, or boot the local dev environment.
---

# Dev — start the local dev servers

```bash
./atelier launch              # both servers, then opens Atelier
./atelier launch --no-browser # servers only
./atelier launch --fe 5000 --be 9000
```

`launch` runs `scripts/dev.sh` — the same script the desktop launchers
invoke — waits for the frontend to actually serve, and then opens Atelier in
a chromeless window when a Chromium-family browser is installed, falling
back to an ordinary tab when one is not. Ctrl-C stops both servers.

`./atelier dev` is an undocumented alias, for muscle memory.

## Notes

- **Run it in the foreground and leave it there.** It holds both servers;
  backgrounding it hides the output the user needs when a port is taken.
- **Ports.** Frontend 4173 (5173 collides with something else on the user's
  machine), backend 8001 (8000 is taken). `ATELIER_FRONTEND_PORT`,
  `ATELIER_BACKEND_PORT`, and the matching `_HOST` variables still work.
- **Browser choice.** `ATELIER_BROWSER` points at a specific browser binary.
  The command names the alternatives it found rather than asking, because a
  question with the same answer every time is a tax on a command people run
  all day.
- **Do not restart the user's servers to "check" something.** If they are
  already running, ask.
