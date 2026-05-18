"""Catch-up merge from provider CLI transcripts into Atelier's NDJSON ledger."""

from __future__ import annotations

from pathlib import Path
from types import ModuleType
from typing import Any

from src.domain.models import Provider
from src.infrastructure.cli_transcript import amp, claude, codex

_PROVIDERS: dict[Provider, ModuleType] = {
    "claude-code": claude,
    "amp": amp,
    "codex": codex,
}


def sdk_cursor_at_detach(
    provider: Provider, session_id: str, workdir: Path
) -> dict[str, Any]:
    """Snapshot the provider source so a later merge knows where to start."""
    impl = _PROVIDERS.get(provider)
    if impl is None:
        return {}
    return impl.cursor_at_detach(session_id, workdir)


def merge_cli_transcript(
    provider: Provider,
    session_id: str,
    workdir: Path,
    cursor: dict[str, Any] | None,
) -> list[dict[str, Any]]:
    """Translate provider CLI entries past ``cursor`` into AgentEvent dicts."""
    impl = _PROVIDERS.get(provider)
    if impl is None:
        return []
    return impl.merge(session_id, workdir, cursor or impl.empty_cursor())


def sdk_transcript_path(
    provider: Provider, session_id: str, workdir: Path
) -> Path | None:
    """Return the local transcript path for providers that expose one."""
    impl = _PROVIDERS.get(provider)
    if impl is None:
        return None
    return impl.transcript_path(session_id, workdir)


__all__ = [
    "merge_cli_transcript",
    "sdk_cursor_at_detach",
    "sdk_transcript_path",
]
