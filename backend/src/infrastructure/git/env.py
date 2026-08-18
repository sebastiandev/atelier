"""Environment for every git subprocess Atelier runs.

A git that can ask a question is a git that can hang the backend, and
the question never reaches the person who could answer it. ``ssh`` reads
a passphrase straight from ``/dev/tty``, so piping stdout/stderr doesn't
capture the prompt — it appears on the terminal running the server,
which nobody using the app is watching. The calling thread then waits
on an answer that isn't coming.

That was a real failure: starting an agent against a repo with an SSH
remote parked on the passphrase prompt for ``git fetch origin HEAD``,
leaving the agent row ``idle`` with no session and the UI showing a
spinner with nothing behind it.

So git runs non-interactive everywhere. Anything that would have been a
question becomes a fast, readable failure that the normal error path can
surface.
"""

from __future__ import annotations

import os

_NONINTERACTIVE = {
    # HTTPS credential prompts and the stock credential helpers.
    "GIT_TERMINAL_PROMPT": "0",
    "GCM_INTERACTIVE": "never",
    # An askpass helper would pop a GUI dialog on the *server* host.
    "GIT_ASKPASS": "",
    "SSH_ASKPASS": "",
    "SSH_ASKPASS_REQUIRE": "never",
}


def git_env() -> dict[str, str]:
    """Process env plus the settings that stop git asking questions."""
    env = {**os.environ, **_NONINTERACTIVE}
    # BatchMode makes ssh fail instead of prompting for a passphrase or a
    # host-key confirmation. Appended to whatever ssh command the user
    # already configured rather than replacing it, so a custom identity,
    # port, or proxy in GIT_SSH_COMMAND survives.
    ssh = env.get("GIT_SSH_COMMAND") or "ssh"
    env["GIT_SSH_COMMAND"] = f"{ssh} -o BatchMode=yes"
    return env


# ssh and git say this several different ways; all of them mean the same
# thing to the person who has to fix it.
_AUTH_MARKERS = (
    "permission denied",
    "publickey",
    "host key verification failed",
    "could not read from remote repository",
    "authentication failed",
    "terminal prompts disabled",
    "no such identity",
)


def auth_hint(stderr: str) -> str:
    """Turn a git auth failure into the action that resolves it.

    Returns an empty string when the failure isn't about credentials, so
    callers can append it unconditionally.
    """
    lowered = stderr.lower()
    if not any(marker in lowered for marker in _AUTH_MARKERS):
        return ""
    return (
        " Atelier runs git without prompts, so a key that needs a "
        "passphrase can't be unlocked here. Add it to your agent first "
        "(`ssh-add --apple-use-keychain ~/.ssh/id_ed25519` on macOS, "
        "`ssh-add ~/.ssh/id_ed25519` elsewhere), then start the agent "
        "again."
    )
