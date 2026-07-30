"""Ports used to persist repository-owned loop definitions."""

from __future__ import annotations

from datetime import datetime
from pathlib import Path
from typing import Protocol

from src.domain.loop.dtos import (
    LoopCheckRequest,
    LoopCheckResult,
    LoopContextResolution,
    LoopContextResolutionRequest,
    LoopDefinition,
    LoopDefinitionScope,
    StageDefinition,
    StageDefinitionScope,
)
from src.domain.loop.models import LoopRunRecord, LoopRunTarget, LoopStepRunRecord


class LoopDefinitionRepository(Protocol):
    """Read and write custom loop definitions below one working root."""

    def list_definitions(
        self,
        root_path: str,
        *,
        scope: LoopDefinitionScope = LoopDefinitionScope.LIBRARY,
    ) -> list[LoopDefinition]:
        """Return every repository definition, including invalid entries."""
        ...

    def get_definition(
        self,
        root_path: str,
        definition_id: str,
        *,
        scope: LoopDefinitionScope = LoopDefinitionScope.LIBRARY,
    ) -> LoopDefinition | None:
        """Return one repository definition when present."""
        ...

    def save_definition(
        self,
        root_path: str,
        definition: LoopDefinition,
        *,
        expected_revision: str | None,
        scope: LoopDefinitionScope = LoopDefinitionScope.LIBRARY,
    ) -> LoopDefinition:
        """Create or replace one valid repository definition."""
        ...

    def delete_definition(self, root_path: str, definition_id: str) -> None:
        """Delete one repository definition directory."""
        ...

    def definition_dir(self, root_path: str, definition_id: str) -> Path:
        """Return one definition's on-disk directory, existing or not."""
        ...


class StageDefinitionRepository(Protocol):
    """Read and write standalone stages below one library root."""

    def list_definitions(
        self,
        root_path: str,
        *,
        scope: StageDefinitionScope = StageDefinitionScope.LIBRARY,
    ) -> list[StageDefinition]: ...

    def get_definition(
        self,
        root_path: str,
        definition_id: str,
        *,
        scope: StageDefinitionScope = StageDefinitionScope.LIBRARY,
    ) -> StageDefinition | None: ...

    def save_definition(
        self,
        root_path: str,
        definition: StageDefinition,
        *,
        expected_revision: str | None,
    ) -> StageDefinition: ...

    def delete_definition(self, root_path: str, definition_id: str) -> None: ...

    def definition_dir(self, root_path: str, definition_id: str) -> Path: ...


class LoopDefinitionLocations(Protocol):
    """Resolve Atelier-owned roots for global and Work-local loop storage."""

    def loop_library_root(self) -> str:
        """Return the root below which the reusable loop library is stored."""
        ...

    def work_loop_root(self, work_slug: str) -> str:
        """Return the root below which one Work's private loops are stored."""
        ...


class LoopWorkingRootRepository(Protocol):
    """Resolve the persisted working root for one Work."""

    def working_root_for_work(self, work_slug: str) -> str | None:
        """Return the configured root without exposing its owning feature."""
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

    def list_for_work(self, work_slug: str) -> list[LoopRunRecord]: ...

    def list_active(self) -> list[LoopRunRecord]: ...

    def claim(
        self,
        work_slug: str,
        run_key: str,
        worker_id: str,
        lease_expires_at: datetime,
    ) -> bool: ...

    def release(self, work_slug: str, run_key: str, worker_id: str) -> None: ...


class LoopRunStateStore(Protocol):
    """Load and save loop state independently of its owning feature."""

    def load(self, work_slug: str, run_id: str) -> LoopRunTarget | None:
        """Return one mutable target snapshot when it exists."""
        ...

    def save(self, target: LoopRunTarget) -> None:
        """Persist one target snapshot without changing its wire shape."""
        ...


__all__ = [
    "LoopCheckRunner",
    "LoopContextResolver",
    "LoopDefinitionLocations",
    "LoopDefinitionRepository",
    "LoopRunRepository",
    "LoopRunStateStore",
    "LoopWorkingRootRepository",
    "StageDefinitionRepository",
]
