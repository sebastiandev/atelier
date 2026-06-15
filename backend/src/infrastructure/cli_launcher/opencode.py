"""OpenCode resume command builder.

OpenCode's ACP session ids are its native session ids, so detach
resumes through the TUI: ``opencode --session <sid>``. Model is
configured-default in v1 (no flag); a non-sentinel model would pass
through as ``--model provider/model`` for forward compat with the
picker follow-up. ``mode`` has no TUI flag — the session's mode is
already persisted OpenCode-side.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from src.domain.agents.configs import OPENCODE_CONFIGURED_MODEL
from src.infrastructure.cli_launcher.common import shell_quote


def build_command(
    session_id: str,
    workdir: Path,
    *,
    model: str | None = None,
    options: dict[str, Any] | None = None,
) -> str:
    cwd = shell_quote(str(workdir))
    sid = shell_quote(session_id)
    flags = ""
    if model and model != OPENCODE_CONFIGURED_MODEL:
        flags = f" --model {shell_quote(model)}"
    return f"cd {cwd} && opencode{flags} --session {sid}"
