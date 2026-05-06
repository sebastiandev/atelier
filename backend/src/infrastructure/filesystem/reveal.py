"""Cross-platform "show this folder" shell-out.

Used by the work and agent reveal endpoints. Lives here (not in the
route module) so both routers share the same platform branching and
tests can monkeypatch one place.
"""

from __future__ import annotations

import subprocess
import sys


def open_in_file_browser(path: str) -> None:
    """Open ``path`` in the OS file browser. Windows' ``explorer.exe``
    returns exit code 1 on success — don't ``check`` there."""
    if sys.platform == "darwin":
        subprocess.run(["open", path], check=True)
    elif sys.platform == "win32":
        subprocess.run(["explorer", path], check=False)
    else:
        subprocess.run(["xdg-open", path], check=True)


__all__ = ["open_in_file_browser"]
