"""Append contexts to an existing agent mid-session.

Companion to ``start.execute``'s context handling, but for the
"already running, user clicked + Add context, hit Send" path. The
existing context files on disk stay untouched (snapshot stability:
a Jira ticket loaded at start doesn't get re-fetched and the
saved-comment count stays as it was). Only the new entries get fresh
fetches and per-source files. The index is rebuilt from the merged
list so the agent's reference doc stays a single consolidated tree.

Returns ``new_filenames`` so the caller (the WS handler that chains
into ``supervisor.send_input``) can build a small auto-prepend hint
that points the SDK at what's new without inlining the full bodies.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING

from src.domain.agents.context_render import derive_filenames
from src.domain.models import Context
from src.domain.workstore.ports import WorkStore

if TYPE_CHECKING:
    from src.domain.connections import ConnectionStore


_CONNECTION_BACKED_TYPES = frozenset({"jira", "sentry", "honeycomb"})


@dataclass(frozen=True)
class AddContextsRequest:
    agent_slug: str
    contexts: tuple[Context, ...]


@dataclass(frozen=True)
class AddContextsResult:
    new_filenames: tuple[str, ...]
    """Filenames written under ``context/`` for the just-added entries —
    in order. Empty if no contexts were supplied (the command is then
    a no-op)."""


class AgentNotFound(ValueError):
    """The agent_slug doesn't resolve to a stored agent."""


def execute(
    workstore: WorkStore,
    connection_store: ConnectionStore,
    req: AddContextsRequest,
) -> AddContextsResult:
    if not req.contexts:
        return AddContextsResult(new_filenames=())

    work_slug = workstore.get_work_slug_for_agent(req.agent_slug)
    if work_slug is None:
        raise AgentNotFound(f"agent not found: {req.agent_slug}")

    existing = workstore.get_agent_contexts(work_slug, req.agent_slug)
    merged = [*existing, *req.contexts]
    since_index = len(existing)

    # Pre-fetch ONLY the new connection-backed entries. Failure here
    # raises ContextFetchError up to the route — symmetric with
    # start.execute, which also halts on fetch failure. State is
    # untouched (we haven't written anything yet).
    fetched_bodies: dict[int, str] = {
        since_index + offset: connection_store.fetch_context_body(c)
        for offset, c in enumerate(req.contexts)
        if c.type in _CONNECTION_BACKED_TYPES
    }

    workstore.render_agent_contexts(
        work_slug,
        req.agent_slug,
        merged,
        fetched_bodies,
        since_index=since_index,
    )
    workstore.replace_agent_contexts(work_slug, req.agent_slug, merged)

    all_filenames = derive_filenames(merged)
    new_filenames = tuple(all_filenames[since_index:])
    return AddContextsResult(new_filenames=new_filenames)


__all__ = [
    "AddContextsRequest",
    "AddContextsResult",
    "AgentNotFound",
    "execute",
]
