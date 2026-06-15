"""Catch-up merge from provider CLI transcripts into Atelier's NDJSON ledger."""

from __future__ import annotations

from pathlib import Path
from types import ModuleType
from typing import Any, cast

from src.domain.models import Provider
from src.infrastructure.cli_transcript import amp, claude, codex, opencode

_PROVIDERS: dict[Provider, ModuleType] = {
    "claude-code": claude,
    "amp": amp,
    "codex": codex,
    # ACP runtimes wrap the same CLIs and reuse their session stores, so
    # the existing readers apply unchanged. OpenCode has its own
    # export-based reader.
    "claude-acp": claude,
    "codex-acp": codex,
    "opencode": opencode,
}


def sdk_cursor_at_detach(
    provider: Provider, session_id: str, workdir: Path
) -> dict[str, Any]:
    """Snapshot the provider source so a later merge knows where to start."""
    impl = _PROVIDERS.get(provider)
    if impl is None:
        return {}
    return cast(dict[str, Any], impl.cursor_at_detach(session_id, workdir))


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
    return cast(
        list[dict[str, Any]],
        impl.merge(session_id, workdir, cursor or impl.empty_cursor()),
    )


def sdk_transcript_path(
    provider: Provider, session_id: str, workdir: Path
) -> Path | None:
    """Return the local transcript path for providers that expose one."""
    impl = _PROVIDERS.get(provider)
    if impl is None:
        return None
    return cast(Path | None, impl.transcript_path(session_id, workdir))


__all__ = [
    "merge_cli_transcript",
    "sdk_cursor_at_detach",
    "sdk_transcript_path",
]
