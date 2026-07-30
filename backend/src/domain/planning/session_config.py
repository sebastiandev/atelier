"""Helpers for PlanningSession metadata copies."""

from __future__ import annotations

from typing import Any

from src.domain.planning.models import PlanningSession

PLANNING_CONFIG_OPTION = "planning_config"


def planning_config_option(session: PlanningSession) -> dict[str, Any]:
    """Return a JSON-safe copy of persisted planning setup."""
    return {
        "root_path": session.root_path,
        "plan_artifacts_dir": session.plan_artifacts_dir,
        "framework": session.framework,
        "profile": session.profile,
        "provider": session.provider,
        "model": session.model,
        "options": dict(session.options or {}),
    }


__all__ = ["PLANNING_CONFIG_OPTION", "planning_config_option"]
