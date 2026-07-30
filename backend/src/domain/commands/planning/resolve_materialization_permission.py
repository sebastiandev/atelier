"""Resolve a pending Planning materializer tool permission."""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING

from src.domain.agents import PermissionDecisionValue
from src.domain.chatstore.ports import ChatStore
from src.domain.commands.planning import materialization_status
from src.domain.workstore.ports import WorkStore

if TYPE_CHECKING:
    from src.domain.supervisor import AgentSupervisorService


@dataclass(frozen=True)
class ResolveMaterializationPermissionRequest:
    """Identify one pending permission and the user's decision."""

    work_slug: str
    request_id: str
    decision: PermissionDecisionValue


class WorkNotFound(ValueError):
    """The work slug does not resolve to a stored Work."""


class PermissionNotFound(ValueError):
    """The permission is no longer pending for this materializer."""


class MaterializerNotRunning(RuntimeError):
    """The provider runtime that owns the permission is no longer live."""


async def execute(
    workstore: WorkStore,
    chatstore: ChatStore,
    supervisor: AgentSupervisorService,
    req: ResolveMaterializationPermissionRequest,
) -> None:
    """Forward one user decision to the active materializer runtime.

    Preconditions: the Work and unresolved permission exist.
    Postconditions: the decision is delivered to that materializer only.
    """
    if workstore.get_work(req.work_slug) is None:
        raise WorkNotFound(f"work not found: {req.work_slug}")
    record = materialization_status.materializer_record(chatstore, req.work_slug)
    if record is None or record.chat.slug is None:
        raise PermissionNotFound("planning materializer not found")
    pending = materialization_status.unresolved_permissions(
        list(chatstore.read_transcript_from_cursor(record.chat.slug, 0))
    )
    if not any(item.request_id == req.request_id for item in pending):
        raise PermissionNotFound(
            f"materializer permission is no longer pending: {req.request_id}"
        )
    if not supervisor.is_registered(record.chat.slug) or supervisor.is_lazy_registered(
        record.chat.slug
    ):
        raise MaterializerNotRunning("planning materializer runtime is not running")
    await supervisor.resolve_permission(
        record.chat.slug, req.request_id, req.decision
    )


__all__ = [
    "MaterializerNotRunning",
    "PermissionNotFound",
    "ResolveMaterializationPermissionRequest",
    "WorkNotFound",
    "execute",
]
