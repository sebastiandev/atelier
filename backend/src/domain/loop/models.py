"""Plain persisted entities for reusable loop execution state."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from typing import Any

from src.domain.loop.dtos import LoopStatus, LoopStepKind, LoopStepStatus, LoopTargetKind


@dataclass(kw_only=True)
class LoopRunRecord:
    """Durable state and immutable definition snapshot for one loop run."""

    id: int | None = None
    run_key: str
    work_slug: str
    target_kind: LoopTargetKind
    target_ref: str
    artifact_id: str | None
    plan_run_id: str | None
    definition_id: str
    definition_revision: str
    definition_snapshot: dict[str, Any]
    status: LoopStatus
    current_step_id: str | None
    state: dict[str, Any]
    started_at: datetime
    updated_at: datetime
    completed_at: datetime | None = None
    accepted_at: datetime | None = None
    cancelled_at: datetime | None = None
    cleanup_at: datetime | None = None
    lease_owner: str | None = None
    lease_expires_at: datetime | None = None


@dataclass(kw_only=True)
class LoopStepRunRecord:
    """Durable aggregate state for one snapshotted stage in a loop run."""

    id: int | None = None
    run_key: str
    step_id: str
    kind: LoopStepKind
    status: LoopStepStatus
    attempt: int
    agent_slug: str | None
    cursor: int
    state: dict[str, Any] = field(default_factory=dict)
    updated_at: datetime
    lease_owner: str | None = None
    lease_expires_at: datetime | None = None


__all__ = ["LoopRunRecord", "LoopStepRunRecord"]
