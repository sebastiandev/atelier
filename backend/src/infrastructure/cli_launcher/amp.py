"""Amp CLI resume command builder."""

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
    return f"cd {cwd} && amp{flags} threads continue {sid}"


def _flags(model: str | None, options: dict[str, Any]) -> str:
    parts: list[str] = []
    if options.get("permission_mode") == "allow_all":
        parts.append("--dangerously-allow-all")
    if model:
        parts.append(f"--mode {shell_quote(model)}")
    return (" " + " ".join(parts)) if parts else ""
