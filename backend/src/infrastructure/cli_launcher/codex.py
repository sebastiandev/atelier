"""Codex CLI resume command builder."""

from __future__ import annotations

from collections.abc import Sequence
from pathlib import Path
from typing import Any

from src.infrastructure.cli_launcher.common import shell_quote


def build_command(
    session_id: str,
    workdir: Path,
    *,
    model: str | None = None,
    options: dict[str, Any] | None = None,
    additional_directories: Sequence[Path] = (),
) -> str:
    cwd = shell_quote(str(workdir))
    sid = shell_quote(session_id)
    flags = _flags(model, options or {}, additional_directories)
    return f"cd {cwd} && codex resume{flags} {sid}"


def _flags(
    model: str | None,
    options: dict[str, Any],
    additional_directories: Sequence[Path] = (),
) -> str:
    parts: list[str] = []
    if model:
        parts.append(f"--model {shell_quote(model)}")
    sandbox = options.get("sandbox")
    sandbox_mode = (
        sandbox if isinstance(sandbox, str) and sandbox else "workspace-write"
    )
    if sandbox_mode == "workspace-write":
        for directory in additional_directories:
            parts.append(f"--add-dir {shell_quote(str(directory))}")
    if isinstance(sandbox, str) and sandbox and sandbox != "workspace-write":
        parts.append(f"--sandbox {shell_quote(sandbox)}")
    approval = options.get("approval_mode")
    if isinstance(approval, str) and approval and approval != "on-request":
        parts.append(f"--ask-for-approval {shell_quote(approval)}")
    effort = options.get("reasoning_effort")
    if isinstance(effort, str) and effort and effort != "medium":
        effort_config = f'model_reasoning_effort="{effort}"'
        parts.append(f"-c {shell_quote(effort_config)}")
    return (" " + " ".join(parts)) if parts else ""
