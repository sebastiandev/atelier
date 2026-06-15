"""Codex (ACP) resume command builder.

codex-acp wraps the Codex runtime, so session ids resume through the
regular ``codex`` CLI. The ACP config's three-tier ``mode`` is unfolded
back into the CLI's independent sandbox + approval flags.
"""

from __future__ import annotations

from collections.abc import Sequence
from pathlib import Path
from typing import Any

from src.infrastructure.cli_launcher import codex

# ACP mode → (sandbox, approval_mode) in the bespoke CLI's vocabulary.
# ``auto`` matches the CLI defaults (workspace-write / on-request), so
# both values are the skip-defaults the codex builder already omits.
_MODE_TO_CLI: dict[str, tuple[str, str]] = {
    "read-only": ("read-only", "on-request"),
    "auto": ("workspace-write", "on-request"),
    "full-access": ("danger-full-access", "never"),
}


def build_command(
    session_id: str,
    workdir: Path,
    *,
    model: str | None = None,
    options: dict[str, Any] | None = None,
    additional_directories: Sequence[Path] = (),
) -> str:
    translated = dict(options or {})
    mode = translated.pop("mode", None)
    if isinstance(mode, str) and mode in _MODE_TO_CLI:
        sandbox, approval = _MODE_TO_CLI[mode]
        translated.setdefault("sandbox", sandbox)
        translated.setdefault("approval_mode", approval)
    return codex.build_command(
        session_id,
        workdir,
        model=model,
        options=translated,
        additional_directories=additional_directories,
    )
