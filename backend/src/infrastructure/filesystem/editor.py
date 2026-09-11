"""Launch editors that have no URL handler.

Most editors are opened by the browser: the backend ships a
``url_template`` per editor and the frontend hands it to the OS. A few
have no scheme at all and can only be started as a command -- Emacs
being the first -- so those are launched here instead.

Adding one is a registry entry plus a function; nothing else in the
stack learns its name. The registry is deliberately a fixed map rather
than exec-ing the descriptor's ``command`` string: those strings exist
to be *shown* to a user, and turning display text into a subprocess is
a different contract than this module wants.
"""

from __future__ import annotations

import os
import subprocess
import sys
from collections.abc import Callable


def open_in_emacs(path: str) -> None:
    """Open ``path`` in a new frame on the default Emacs server.

    ``ALTERNATE_EDITOR`` is dropped from the environment on purpose: set
    (even to ""), ``emacsclient`` silently starts a *new* Emacs when no
    server is reachable, which looks like success while ignoring the
    running session the user meant to open. Without it the command fails
    and the message below says why.
    """
    if sys.platform not in {"darwin", "linux"}:
        raise OSError(
            f"opening Emacs from Atelier is supported on macOS and Linux, "
            f"not {sys.platform}"
        )
    env = os.environ.copy()
    env.pop("ALTERNATE_EDITOR", None)
    try:
        subprocess.run(
            ["emacsclient", "-n", "-c", path],
            check=True,
            env=env,
            timeout=10,
        )
    except FileNotFoundError as exc:
        raise OSError("emacsclient is not on PATH") from exc
    except subprocess.CalledProcessError as exc:
        raise OSError(
            "emacsclient could not reach a running Emacs server "
            "(start one with M-x server-start)"
        ) from exc


#: Editors Atelier starts as a command, by descriptor value. An editor
#: absent here is one the browser opens through its ``url_template``.
COMMAND_EDITORS: dict[str, Callable[[str], None]] = {
    "emacs": open_in_emacs,
}


def launcher_for(editor: str) -> Callable[[str], None] | None:
    """Return the command launcher for ``editor``, or None if it has none."""
    return COMMAND_EDITORS.get(editor)


__all__ = ["COMMAND_EDITORS", "launcher_for", "open_in_emacs"]
