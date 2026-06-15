"""Open the user's terminal at a given folder with a provider resume command."""

from __future__ import annotations

from collections.abc import Sequence
from pathlib import Path
from types import ModuleType
from typing import Any

from src.domain.models import Provider
from src.infrastructure.cli_launcher import (
    amp,
    claude,
    claude_acp,
    codex,
    codex_acp,
    opencode,
)
from src.infrastructure.cli_launcher.terminal import LaunchResult, launch_in_terminal

_PROVIDERS: dict[Provider, ModuleType] = {
    "claude-code": claude,
    "amp": amp,
    "codex": codex,
    # ACP runtimes wrap the same CLIs, so detach resumes natively; the
    # modules translate ACP option vocabularies back to CLI flags.
    "claude-acp": claude_acp,
    "codex-acp": codex_acp,
    "opencode": opencode,
}


def build_resume_command(
    provider: Provider,
    session_id: str,
    workdir: Path,
    *,
    model: str | None = None,
    options: dict[str, Any] | None = None,
    additional_directories: Sequence[Path] = (),
) -> str:
    """Return the shell command that resumes the provider CLI session."""
    impl = _PROVIDERS.get(provider)
    if impl is None:
        raise ValueError(f"unknown provider for CLI resume: {provider!r}")
    if provider in ("codex", "codex-acp"):
        return str(
            impl.build_command(
                session_id,
                workdir,
                model=model,
                options=options,
                additional_directories=additional_directories,
            )
        )
    return str(impl.build_command(session_id, workdir, model=model, options=options))


__all__ = ["LaunchResult", "build_resume_command", "launch_in_terminal"]
