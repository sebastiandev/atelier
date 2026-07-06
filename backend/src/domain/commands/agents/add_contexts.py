"""Append contexts to an existing agent mid-session."""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING

from src.domain.agents import context_updates
from src.domain.models import Context
from src.domain.workstore.ports import WorkStore

if TYPE_CHECKING:
    from src.domain.connections import ConnectionStore

AgentNotFound = context_updates.AgentNotFound
AddContextsResult = context_updates.AddContextsResult


@dataclass(frozen=True)
class AddContextsRequest:
    """Contexts to append to an existing agent."""

    agent_slug: str
    contexts: tuple[Context, ...]


def execute(
    workstore: WorkStore,
    connection_store: ConnectionStore,
    req: AddContextsRequest,
) -> AddContextsResult:
    """Append contexts to an existing agent mid-session.

    Preconditions: ``req.agent_slug`` resolves to a stored agent.
    Postconditions: new context files are written and the context index is
    rebuilt with existing plus newly supplied contexts.
    """
    return context_updates.append_contexts(
        workstore,
        connection_store,
        agent_slug=req.agent_slug,
        contexts=req.contexts,
    )


__all__ = [
    "AddContextsRequest",
    "AddContextsResult",
    "AgentNotFound",
    "execute",
]
