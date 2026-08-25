"""Cross-platform launch helpers for editors without URL handlers."""

from __future__ import annotations

import os
import subprocess
import sys


def open_in_emacs(path: str) -> None:
    """Open ``path`` in a new frame on the default Emacs server."""
    if sys.platform == "win32":
        raise OSError("opening Emacs from Atelier is unsupported on Windows")
    if sys.platform not in {"darwin", "linux"}:
        raise OSError("opening Emacs from Atelier is supported only on macOS and Linux")
    env = os.environ.copy()
    env.pop("ALTERNATE_EDITOR", None)
    subprocess.run(
        ["emacsclient", "-n", "-c", path],
        check=True,
        env=env,
        timeout=10,
    )


__all__ = ["open_in_emacs"]
