"""Claude CLI resume command builder."""

from __future__ import annotations

from pathlib import Path
from typing import Any

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
    flags = _flags(model, options or {})
    return f"cd {cwd} && claude{flags} --resume {sid}"


def _flags(model: str | None, options: dict[str, Any]) -> str:
    parts: list[str] = []
    if model:
        parts.append(f"--model {shell_quote(model)}")
    effort = options.get("thinking_effort")
    if isinstance(effort, str) and effort and effort != "off":
        parts.append(f"--effort {shell_quote(effort)}")
    perm = options.get("permission_mode")
    if isinstance(perm, str) and perm and perm != "default":
        parts.append(f"--permission-mode {shell_quote(perm)}")
    return (" " + " ".join(parts)) if parts else ""
