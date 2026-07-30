"""Domain entities for source-backed Work planning."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Any

from src.domain.models import Provider
from src.domain.planning.dtos import PlanningFramework, PlanningProfile


@dataclass(kw_only=True)
class PlanningSession:
    """Persisted planning setup for one Work."""

    id: int | None = None
    work_slug: str
    planning_chat_slug: str | None
    root_path: str
    plan_artifacts_dir: str | None
    framework: PlanningFramework
    profile: PlanningProfile
    provider: Provider
    model: str
    options: dict[str, Any] | None
    created_at: datetime
    updated_at: datetime


__all__ = ["PlanningSession"]
