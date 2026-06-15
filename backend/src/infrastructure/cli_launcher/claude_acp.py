"""Claude (ACP) resume command builder.

claude-agent-acp wraps the Claude Code runtime, so its session ids are
plain Claude session ids and detach resumes through the regular
``claude`` CLI. Only the option *values* need translation: the wrapper
models "no override" as ``default`` where the CLI wants the flag
omitted, and the model values are CLI aliases the binary accepts as-is.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from src.infrastructure.cli_launcher import claude


def build_command(
    session_id: str,
    workdir: Path,
    *,
    model: str | None = None,
    options: dict[str, Any] | None = None,
) -> str:
    translated = dict(options or {})
    if translated.get("thinking_effort") == "default":
        translated.pop("thinking_effort")
    # ``default`` permission mode is already skipped by the claude
    # builder; ``auto`` / ``dontAsk`` pass through as real CLI modes.
    return claude.build_command(
        session_id,
        workdir,
        model=None if model == "default" else model,
        options=translated,
    )
