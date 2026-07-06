"""Start or reuse a write-capable Planning materialization chat."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from src.domain.chatstore.dtos import ChatRecord
from src.domain.chatstore.ports import ChatStore
from src.domain.models import Provider
from src.domain.planning import materialization
from src.domain.planning.dtos import (
    PlanningFramework,
    PlanningFrameworkStatus,
    PlanningProfile,
)
from src.domain.workstore.ports import WorkStore

WorkNotFound = materialization.WorkNotFound
PlanningFrameworkNotReady = materialization.PlanningFrameworkNotReady


@dataclass(frozen=True)
class StartPlanningMaterializationChatRequest:
    """Inputs for a write-capable materialization run.

    Preconditions: the Work exists, the selected framework is initialized,
    and the provider config can write inside ``root_path``.
    Postconditions: a work-grounded materialization chat exists in
    ``root_path`` with backend-built instructions.
    """

    work_slug: str
    root_path: str
    framework: PlanningFramework
    profile: PlanningProfile
    provider: Provider
    model: str
    options: dict[str, Any] = field(default_factory=dict)
    planning_chat_slug: str | None = None


def execute(
    workstore: WorkStore,
    chatstore: ChatStore,
    req: StartPlanningMaterializationChatRequest,
) -> tuple[ChatRecord, PlanningFrameworkStatus]:
    """Create or return a Planning materialization chat.

    Preconditions: Work and framework readiness have been validated.
    Postconditions: the materializer can write planning files under the
    selected root while the backend command resolves any permission prompts.
    """
    return materialization.start_materialization_chat(
        workstore,
        chatstore,
        work_slug=req.work_slug,
        root_path=req.root_path,
        framework=req.framework,
        profile=req.profile,
        provider=req.provider,
        model=req.model,
        options=req.options,
        planning_chat_slug=req.planning_chat_slug,
    )


def validate_provider_config(req: StartPlanningMaterializationChatRequest) -> None:
    """Validate provider/model/options for a materialization run."""
    materialization.validate_materialization_provider_config(
        root_path=req.root_path,
        provider=req.provider,
        model=req.model,
        options=req.options,
    )


__all__ = [
    "PlanningFrameworkNotReady",
    "StartPlanningMaterializationChatRequest",
    "WorkNotFound",
    "execute",
    "validate_provider_config",
]
