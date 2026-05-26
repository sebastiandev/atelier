"""Domain contracts and helpers for same-agent context compaction."""

from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Any, Protocol

from src.domain.agents.configs import AgentConfig
from src.domain.agents.ports import AgentStartContext

COMPACTION_TRANSCRIPT_CHAR_CAP = 750_000


@dataclass(frozen=True)
class CompactionSessionStartResult:
    session_id: str


@dataclass(frozen=True)
class BreadcrumbResult:
    written: bool
    error: str | None = None


class CompactionSessionClient(Protocol):
    async def summarize_transcript(
        self,
        *,
        config: AgentConfig,
        context: AgentStartContext,
        prompt: str,
    ) -> str: ...

    async def start_fresh_session(
        self,
        *,
        config: AgentConfig,
        context: AgentStartContext,
        seed_message: str,
    ) -> CompactionSessionStartResult: ...

    async def write_breadcrumb(
        self,
        *,
        config: AgentConfig,
        context: AgentStartContext,
        old_session_id: str,
        breadcrumb: str,
    ) -> BreadcrumbResult: ...


def trim_transcript_to_char_cap(
    events: list[dict[str, Any]], cap: int = COMPACTION_TRANSCRIPT_CHAR_CAP
) -> list[dict[str, Any]]:
    selected: list[dict[str, Any]] = []
    total = 0
    for event in reversed(events):
        size = len(json.dumps(event, ensure_ascii=False))
        if total + size > cap and selected:
            break
        selected.append(event)
        total += size
    selected.reverse()
    return selected


__all__ = [
    "COMPACTION_TRANSCRIPT_CHAR_CAP",
    "BreadcrumbResult",
    "CompactionSessionClient",
    "CompactionSessionStartResult",
    "trim_transcript_to_char_cap",
]
