"""Mark a Planning chat ready when the assistant emits the readiness marker."""

from __future__ import annotations

from dataclasses import dataclass

from src.domain.chatstore.ports import ChatStore
from src.domain.planning.readiness import (
    PLANNING_READINESS_OPTION,
    PlanningChatReadiness,
    is_planning_chat,
    planning_readiness_from_options,
    planning_readiness_payload,
    scan_text_for_planning_readiness,
)


@dataclass(frozen=True)
class MarkPlanningChatReadyRequest:
    """Inputs for accepting a Planning readiness marker."""

    chat_slug: str
    assistant_text: str


@dataclass(frozen=True)
class MarkPlanningChatReadyResult:
    """Result of recording Planning chat readiness."""

    readiness: PlanningChatReadiness
    changed: bool


def execute(
    chatstore: ChatStore,
    req: MarkPlanningChatReadyRequest,
) -> MarkPlanningChatReadyResult | None:
    """Persist Planning chat readiness when a marker is present.

    Preconditions: ``chat_slug`` identifies the chat that produced
    ``assistant_text``.
    Postconditions: the chat's ``planning_readiness`` option is written once
    when a valid marker appears on the reserved Planning chat.
    """
    record = chatstore.get_chat(req.chat_slug)
    if record is None or not is_planning_chat(record.chat):
        return None
    existing = planning_readiness_from_options(record.chat.options)
    if existing is not None:
        return MarkPlanningChatReadyResult(readiness=existing, changed=False)
    readiness = scan_text_for_planning_readiness(req.assistant_text)
    if readiness is None:
        return None
    chatstore.set_chat_option(
        req.chat_slug,
        PLANNING_READINESS_OPTION,
        planning_readiness_payload(readiness),
    )
    return MarkPlanningChatReadyResult(readiness=readiness, changed=True)


__all__ = [
    "MarkPlanningChatReadyRequest",
    "MarkPlanningChatReadyResult",
    "execute",
]
