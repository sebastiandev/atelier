#!/usr/bin/env python3
"""The ``atelier`` command — for people running Atelier, not developing it.

Two commands:

``atelier update``
    Bring a checkout up to date end to end: pull, install whatever the pull
    changed, apply on-disk migrations, and say what needs restarting. It
    pulls itself, so this is the only command a user runs — not the last of
    four remembered steps.

``atelier launch``
    Start both dev servers and open Atelier in front of the user, in a
    chromeless window where the browser supports one.

Everything else — tests, wipes, scaffolding a migration, reconciling a
dependency bump — stays a Claude Code skill. Those serve someone developing
Atelier, and two of them are judgement rather than procedure.

**Standard library only.** This runs *before* ``uv sync`` finishes, so it
cannot import from ``backend/.venv`` — including anything under ``src``.
That constraint is the reason this file is plain and a little repetitive.
"""

from __future__ import annotations

import argparse
import itertools
import os
import platform
import shutil
import signal
import socket
import subprocess
import sys
import threading
import time
import webbrowser
from collections.abc import Callable, Iterable, Sequence
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent

OK = "✓"      # step did something
SKIP = "·"    # step had nothing to do
WARN = "!"         # needs the user


# -- output ---------------------------------------------------------------


def step(mark: str, message: str) -> None:
    print(f"  {mark} {message}")


class Working:
    """Spin while a slow step runs, then leave one line behind.

    `uv sync` and `npm install` can take a minute, and a command that prints
    nothing for a minute is indistinguishable from one that has hung. This
    is a spinner rather than a progress bar because neither tool reports
    progress we could believe; the honest signal is "still going".

    Off when stdout is not a terminal, so logs and CI keep one clean line
    per step instead of a smear of control characters.
    """

    FRAMES = "⠋⠙⠹⠸⠼⠴⠦⠧⠇⠏"

    def __init__(self, message: str, interval: float = 0.1) -> None:
        self.message = message
        self.interval = interval
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None
        self._live = sys.stdout.isatty()

    def __enter__(self) -> Working:
        if self._live:
            self._thread = threading.Thread(target=self._spin, daemon=True)
            self._thread.start()
        return self

    def __exit__(self, *exc: object) -> None:
        self._stop.set()
        if self._thread:
            self._thread.join(timeout=1)
        if self._live:
            # Wipe the spinner so the caller's ✓/! line lands on a clean row.
            sys.stdout.write("\r\033[K")
            sys.stdout.flush()

    def _spin(self) -> None:
        for i in itertools.count():
            if self._stop.wait(self.interval):
                return
            frame = self.FRAMES[i % len(self.FRAMES)]
            sys.stdout.write(f"\r  {frame} {self.message}")
            sys.stdout.flush()


def fail(message: str) -> int:
    print(f"\nerror: {message}", file=sys.stderr)
    return 1


# -- process helpers ------------------------------------------------------


def run(argv: Sequence[str], cwd: Path | None = None, quiet: bool = False) -> int:
    """Run a command, streaming its output unless ``quiet``."""
    out = subprocess.DEVNULL if quiet else None
    return subprocess.call(list(argv), cwd=str(cwd or REPO), stdout=out, stderr=out)


def capture(argv: Sequence[str], cwd: Path | None = None) -> str:
    """Run a command and return stripped stdout, or "" if it failed."""
    try:
        proc = subprocess.run(
            list(argv),
            cwd=str(cwd or REPO),
            capture_output=True,
            text=True,
            check=False,
        )
    except OSError:
        return ""
    return proc.stdout.strip() if proc.returncode == 0 else ""


def git(*args: str) -> str:
    return capture(["git", *args])


# -- consent --------------------------------------------------------------


def confirm(question: str, assume_yes: bool) -> bool:
    """Ask the user, unless they pre-agreed or there is nobody to ask.

    A non-interactive run refuses rather than guessing: this command pulls
    and installs, and picking a default for someone who cannot see the
    question is how a tool ends up doing something surprising to a working
    copy it does not own.
    """
    if assume_yes:
        return True
    if not sys.stdin.isatty():
        print(f"\n  {WARN} {question}")
        print("      Not a terminal, so nothing was asked. Re-run with --yes to accept.")
        return False
    try:
        return input(f"  {WARN} {question} [y/N] ").strip().lower() in {"y", "yes"}
    except (EOFError, KeyboardInterrupt):
        print()
        return False


# -- update: what a pull implies ------------------------------------------

# Each entry: the paths that trigger it, a label, and how to install it.
DEPENDENCY_JOBS: tuple[tuple[tuple[str, ...], str, tuple[str, ...], str], ...] = (
    (
        ("backend/uv.lock", "backend/pyproject.toml"),
        "uv sync",
        ("uv", "sync"),
        "backend",
    ),
    (
        ("backend/acp-runtime/package-lock.json", "backend/acp-runtime/package.json"),
        "npm install --prefix backend/acp-runtime",
        ("npm", "install", "--prefix", "backend/acp-runtime"),
        ".",
    ),
    (
        ("frontend/package-lock.json", "frontend/package.json"),
        "npm install (frontend)",
        ("npm", "install"),
        "frontend",
    ),
)


def pending_dependency_jobs(
    changed: Iterable[str],
) -> list[tuple[str, tuple[str, ...], str]]:
    """Which installs a set of changed paths implies, in declaration order."""
    touched = set(changed)
    jobs = []
    for triggers, label, argv, cwd in DEPENDENCY_JOBS:
        if touched.intersection(triggers):
            jobs.append((label, argv, cwd))
    return jobs


def restart_notices(changed: Iterable[str], installed: Iterable[str]) -> list[str]:
    """What the user must restart themselves, given what moved.

    We never restart anything: the servers and agent sessions belong to the
    user, are usually in another terminal, and a process this command did
    not start is not its to kill.
    """
    touched = set(changed)
    ran = set(installed)
    notices = []

    backend_deps = "uv sync" in ran
    migrations_changed = any(
        p == "backend/src/infrastructure/database/migrations.py" for p in touched
    )
    if backend_deps or migrations_changed:
        why = "DB migrations pending" if migrations_changed else "dependencies changed"
        notices.append(f"restart the backend — {why}")

    if "npm install --prefix backend/acp-runtime" in ran:
        notices.append(
            "restart agent and chat sessions — running ACP wrappers are the old ones"
        )

    frontend_deps = "npm install (frontend)" in ran
    frontend_code = any(
        p.startswith("frontend/src/") or p in {"frontend/index.html", "frontend/vite.config.ts"}
        for p in touched
    )
    if frontend_deps or frontend_code:
        notices.append("refresh the browser — frontend changed")

    return notices


def fs_migrations() -> list[Path]:
    """FS-side migrations, in the order ``/migrate`` runs them.

    DB migrations are deliberately absent: they apply on backend boot.
    """
    return sorted(REPO.joinpath("scripts").glob("migrate-*.py"))


# -- update ---------------------------------------------------------------


def cmd_update(args: argparse.Namespace) -> int:
    if not REPO.joinpath(".git").exists():
        return fail(f"{REPO} is not a git checkout")

    branch = git("rev-parse", "--abbrev-ref", "HEAD")
    dirty = bool(git("status", "--porcelain"))

    if dirty:
        print("  Uncommitted changes in the working tree:")
        for line in git("status", "--short").splitlines()[:10]:
            print(f"      {line}")
        if not confirm("Pull on top of them?", args.yes):
            return fail("stopped with the working tree untouched")

    # Pull the branch you are on, never a different one. Updating a feature
    # branch from main would be a merge the user did not ask for, and
    # switching branches under them is worse.
    if branch and branch != "main":
        if not confirm(f"On '{branch}', not main. Pull origin/{branch}?", args.yes):
            return fail(f"stopped — switch to main, or re-run to update '{branch}'")
    target = branch or "main"

    before = git("rev-parse", "HEAD")
    with Working(f"pulling origin/{target}"):
        pull_failed = run(["git", "pull", "--ff-only", "origin", target], quiet=True) != 0
    if pull_failed:
        return fail(
            f"git pull --ff-only origin {target} failed. Your branch has probably "
            "diverged; resolve it yourself rather than letting this command guess."
        )
    after = git("rev-parse", "HEAD")

    if before == after:
        # Not a reason to stop. The user may have pulled by hand since the
        # backend last started, leaving migrations un-run.
        step(SKIP, f"already up to date ({after[:7]})")
        changed: list[str] = []
    else:
        count = git("rev-list", "--count", f"{before}..{after}")
        step(OK, f"pulled {count} commit{'' if count == '1' else 's'} ({before[:7]}..{after[:7]})")
        changed = git("diff", "--name-only", before, after).splitlines()

    installed: list[str] = []
    jobs = pending_dependency_jobs(changed)
    if not jobs:
        step(SKIP, "dependencies unchanged")
    for label, argv, cwd in jobs:
        if shutil.which(argv[0]) is None:
            step(WARN, f"{label} skipped — '{argv[0]}' is not installed")
            continue
        with Working(label):
            failed = run(argv, cwd=REPO / cwd, quiet=True) != 0
        if failed:
            return fail(f"{label} failed")
        installed.append(label)
        step(OK, label)

    migrations = fs_migrations()
    if not migrations:
        step(SKIP, "no FS migrations registered")
    else:
        for script in migrations:
            with Working(f"migrating: {script.stem}"):
                failed = (
                    run(["uv", "run", "python", str(script)], cwd=REPO / "backend", quiet=True)
                    != 0
                )
            if failed:
                return fail(f"{script.name} failed")
        step(OK, f"ran {len(migrations)} FS migration{'' if len(migrations) == 1 else 's'}")

    for notice in restart_notices(changed, installed):
        step(WARN, notice)

    if before == after:
        step(
            WARN,
            "if you pulled by hand since the backend started, restart it so DB "
            "migrations apply",
        )

    print(f"\n  {git('log', '--oneline', '-1')}")
    return 0


# -- launch ---------------------------------------------------------------

# Preferred first. Chromium-family only: these take --app=<url> and give a
# window with no tab strip or address bar.
MAC_BROWSERS = (
    "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome",
    "/Applications/Chromium.app/Contents/MacOS/Chromium",
    "/Applications/Microsoft Edge.app/Contents/MacOS/Microsoft Edge",
    "/Applications/Brave Browser.app/Contents/MacOS/Brave Browser",
    "/Applications/Vivaldi.app/Contents/MacOS/Vivaldi",
)
UNIX_BROWSERS = (
    "google-chrome",
    "google-chrome-stable",
    "chromium",
    "chromium-browser",
    "microsoft-edge",
    "brave-browser",
    "vivaldi",
    "vivaldi-stable",
)
WINDOWS_BROWSERS = ("chrome", "msedge", "brave", "vivaldi")


def find_app_browsers(
    system: str,
    which: Callable[[str], str | None] = shutil.which,
    exists: Callable[[str], bool] = os.path.exists,
    override: str | None = None,
) -> list[str]:
    """Every installed browser that can open a chromeless window, preferred first.

    ``ATELIER_BROWSER`` wins outright when set, matching how every other
    Atelier default is overridden. Firefox and Safari are absent on purpose:
    Firefox dropped site-specific browsers and Safari never had the flag, so
    for those users the caller falls back to an ordinary tab.

    We return the whole list rather than the winner because a machine with
    several of these has a choice worth surfacing — picking one silently is
    how you end up launching Chrome at someone whose Atelier lives in
    Vivaldi.
    """
    chosen = override if override is not None else os.environ.get("ATELIER_BROWSER")
    if chosen:
        resolved = chosen if exists(chosen) else which(chosen)
        return [resolved] if resolved else []
    if system == "Darwin":
        return [path for path in MAC_BROWSERS if exists(path)]
    names = WINDOWS_BROWSERS if system == "Windows" else UNIX_BROWSERS
    return [found for name in names if (found := which(name))]


def wait_for_port(host: str, port: int, deadline: float, alive: Callable[[], bool]) -> bool:
    """Block until something answers on the port, the child dies, or time is up."""
    while time.monotonic() < deadline:
        if not alive():
            return False
        try:
            with socket.create_connection((host, port), timeout=0.5):
                return True
        except OSError:
            time.sleep(0.25)
    return False


def browser_report(browsers: Sequence[str]) -> list[str]:
    """What to say about the browser choice — nothing, unless there was one.

    Deliberately not a prompt. ``launch`` is a command people run several
    times a day, and a question with the same answer every time is a tax.
    When no chromeless browser exists there is nothing to ask about anyway:
    every alternative renders the same ordinary tab.
    """
    if not browsers:
        return [
            "no chromeless browser found — set ATELIER_BROWSER to a "
            "Chrome/Chromium/Edge/Brave/Vivaldi path for an app window"
        ]
    if len(browsers) > 1:
        others = ", ".join(Path(b).name for b in browsers[1:])
        return [f"also installed: {others} — set ATELIER_BROWSER to prefer one"]
    return []


def open_app_window(url: str) -> tuple[str, list[str]]:
    """Open ``url``, chromeless if we can. Returns the outcome plus any advice."""
    browsers = find_app_browsers(platform.system())
    notes = browser_report(browsers)
    if browsers:
        subprocess.Popen(
            [browsers[0], f"--app={url}"],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            start_new_session=True,
        )
        return f"opened {url} in {Path(browsers[0]).name}", notes
    webbrowser.open(url)
    return f"opened {url} in your default browser", notes


def cmd_launch(args: argparse.Namespace) -> int:
    dev = REPO / "scripts" / "dev.sh"
    if not dev.exists():
        return fail(f"{dev} is missing")

    argv = [str(dev)]
    if args.fe:
        argv += ["--fe", str(args.fe)]
    if args.be:
        argv += ["--be", str(args.be)]

    host = os.environ.get("ATELIER_FRONTEND_HOST", "127.0.0.1")
    port = args.fe or int(os.environ.get("ATELIER_FRONTEND_PORT", "4173"))
    url = f"http://{host}:{port}"

    # Own process group, signalled as a group when we stop.
    #
    # A terminal Ctrl-C reaches dev.sh by itself, because the signal goes to
    # the whole foreground group. Nothing else does: a supervisor's TERM, an
    # IDE stop button, or a plain `kill` lands on this process only, and
    # dev.sh -- along with both servers -- would outlive us still holding
    # its ports. Giving the child its own group and signalling that group
    # makes one path handle every case, rather than working by accident in
    # the terminal and not at all anywhere else.
    child = subprocess.Popen(argv, cwd=str(REPO), start_new_session=True)

    # A supervisor's TERM should take the servers down the same way a
    # person's Ctrl-C does.
    signal.signal(signal.SIGTERM, _raise_interrupt)

    if args.no_browser:
        step(SKIP, f"not opening a browser — Atelier is at {url}")
    else:
        ready = wait_for_port(
            host, port, time.monotonic() + args.timeout, lambda: child.poll() is None
        )
        if ready:
            opened, notes = open_app_window(url)
            step(OK, opened)
            for note in notes:
                step(SKIP, note)
        elif child.poll() is not None:
            # Servers died. Opening a window now would show a connection error
            # and blame the browser for it.
            return child.returncode or 1
        else:
            step(WARN, f"{url} did not come up within {args.timeout:.0f}s — not opening")

    try:
        return child.wait()
    except KeyboardInterrupt:
        return stop_child(child)


def _raise_interrupt(signum: int, frame: object) -> None:
    raise KeyboardInterrupt


def signal_group(pid: int, sig: int) -> bool:
    """Signal a process group. False when it is already gone."""
    try:
        os.killpg(os.getpgid(pid), sig)
    except (OSError, ProcessLookupError):
        return False
    return True


def stop_child(child: subprocess.Popen[bytes], grace: float = 10.0) -> int:
    """Take the servers down, escalating only as far as needed.

    SIGINT first: it is what dev.sh traps, and it lets uvicorn and vite
    close their own sockets rather than having them yanked. TERM then KILL
    after that, so a wedged server cannot leave a port held behind it.

    Note when testing this by hand: a shell that backgrounds a job in a
    non-interactive context sets SIGINT to ignored, and Python keeps an
    inherited SIG_IGN -- so `kill -INT` at a backgrounded run looks like a
    leak that is really the harness. Use SIGTERM to exercise this path.
    """
    for sig in (signal.SIGINT, signal.SIGTERM, signal.SIGKILL):
        if child.poll() is not None:
            return 0
        if not signal_group(child.pid, sig):
            return 0
        try:
            child.wait(timeout=grace if sig != signal.SIGKILL else 5)
            return 0
        except (subprocess.TimeoutExpired, KeyboardInterrupt):
            continue
    return 0


# -- entry point ----------------------------------------------------------


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="atelier",
        description="Run Atelier: bring a checkout up to date, or start it.",
        epilog=(
            "examples:\n"
            "  atelier launch                     start Atelier and open it\n"
            "  atelier launch --no-browser        servers only\n"
            "  atelier launch --fe 4183 --be 8011 different ports\n"
            "  atelier update                     pull, install, migrate\n"
            "\n"
            "Run from the repo root as ./atelier, or symlink this onto your PATH:\n"
            "  ln -s \"$PWD/atelier\" ~/.local/bin/atelier"
        ),
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    sub = parser.add_subparsers(dest="command", metavar="{update,launch}")

    update = sub.add_parser(
        "update",
        help="pull, install, migrate, and report restarts",
        description=(
            "Bring this checkout up to date. Pulls the branch you are on, "
            "installs whatever the pull changed, applies on-disk migrations, "
            "and prints what needs restarting. It asks before pulling over "
            "uncommitted changes, and never stops your servers."
        ),
    )
    update.add_argument(
        "--yes",
        "-y",
        action="store_true",
        help="accept the prompts, for non-interactive runs",
    )
    update.set_defaults(func=cmd_update)

    # 'dev' is an alias for muscle memory; argparse renders it as "launch (dev)".
    launch = sub.add_parser(
        "launch",
        aliases=["dev"],
        help="start the servers and open Atelier",
        description=(
            "Start the backend and frontend, wait for the frontend to serve, "
            "then open Atelier in a chromeless window where the browser "
            "supports one. Ctrl-C stops both servers."
        ),
    )
    launch.add_argument(
        "--fe",
        "--frontend-port",
        type=int,
        metavar="PORT",
        help="frontend port (default 4173)",
    )
    launch.add_argument(
        "--be",
        "--backend-port",
        type=int,
        metavar="PORT",
        help="backend port (default 8001)",
    )
    launch.add_argument(
        "--no-browser",
        "--headless",
        action="store_true",
        help="start the servers without opening a window",
    )
    launch.add_argument("--timeout", type=float, default=60.0, help=argparse.SUPPRESS)
    launch.set_defaults(func=cmd_launch)

    return parser


def main(argv: Sequence[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(list(argv) if argv is not None else None)
    if not getattr(args, "func", None):
        parser.print_help()
        return 0
    try:
        return int(args.func(args))
    except KeyboardInterrupt:
        return 130


if __name__ == "__main__":
    sys.exit(main())
