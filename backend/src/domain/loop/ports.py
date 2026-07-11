"""Ports used to persist repository-owned loop definitions."""

from __future__ import annotations

from datetime import datetime
from typing import Protocol

from src.domain.loop.dtos import (
    LoopCheckRequest,
    LoopCheckResult,
    LoopContextResolution,
    LoopContextResolutionRequest,
    LoopDefinition,
)
from src.domain.loop.models import LoopRunRecord, LoopStepRunRecord


class LoopDefinitionRepository(Protocol):
    """Read and write custom loop definitions below one working root."""

    def list_definitions(self, root_path: str) -> list[LoopDefinition]:
        """Return every repository definition, including invalid entries."""
        ...

    def get_definition(
        self, root_path: str, definition_id: str
    ) -> LoopDefinition | None:
        """Return one repository definition when present."""
        ...

    def save_definition(
        self,
        root_path: str,
        definition: LoopDefinition,
        *,
        expected_revision: str | None,
    ) -> LoopDefinition:
        """Create or replace one valid repository definition."""
        ...

    def delete_definition(self, root_path: str, definition_id: str) -> None:
        """Delete one repository definition directory."""
        ...


class LoopCheckRunner(Protocol):
    """Execute an explicitly configured deterministic loop check."""

    async def run(self, request: LoopCheckRequest) -> LoopCheckResult: ...


class LoopContextResolver(Protocol):
    """Resolve stage context references without reading file contents."""

    def resolve(self, request: LoopContextResolutionRequest) -> LoopContextResolution: ...


class LoopRunRepository(Protocol):
    """Persist and recover generic loop runs and stage state."""

    def upsert(
        self,
        run: LoopRunRecord,
        steps: tuple[LoopStepRunRecord, ...],
    ) -> None: ...

    def get(self, work_slug: str, run_key: str) -> LoopRunRecord | None: ...

    def list_active(self) -> list[LoopRunRecord]: ...

    def claim(
        self,
        work_slug: str,
        run_key: str,
        worker_id: str,
        lease_expires_at: datetime,
    ) -> bool: ...

    def release(self, work_slug: str, run_key: str, worker_id: str) -> None: ...


__all__ = [
    "LoopCheckRunner",
    "LoopContextResolver",
    "LoopDefinitionRepository",
    "LoopRunRepository",
]
