"""Actions for updating agent context attachments."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING

from src.domain.agents.context_render import derive_filenames
from src.domain.models import Context
from src.domain.workstore.ports import WorkStore

if TYPE_CHECKING:
    from src.domain.connections import ConnectionStore

_CONNECTION_BACKED_TYPES = frozenset({"jira", "sentry", "honeycomb"})


@dataclass(frozen=True)
class AddContextsResult:
    """Files written when contexts are appended to an existing agent."""

    new_filenames: tuple[str, ...]
    index_path: str | None
    new_file_paths: tuple[str, ...]


class AgentNotFound(ValueError):
    """The agent_slug doesn't resolve to a stored agent."""


def append_contexts(
    workstore: WorkStore,
    connection_store: ConnectionStore,
    *,
    agent_slug: str,
    contexts: tuple[Context, ...],
) -> AddContextsResult:
    """Append contexts to an existing agent.

    Preconditions: ``agent_slug`` resolves to a stored agent and connection
    backed contexts can be fetched.
    Postconditions: new context files and the context index are written, then
    the agent context list is replaced atomically from the command caller's
    perspective.
    """
    if not contexts:
        return AddContextsResult(
            new_filenames=(), index_path=None, new_file_paths=()
        )

    work_slug = workstore.get_work_slug_for_agent(agent_slug)
    if work_slug is None:
        raise AgentNotFound(f"agent not found: {agent_slug}")

    existing = workstore.get_agent_contexts(work_slug, agent_slug)
    merged = [*existing, *contexts]
    since_index = len(existing)

    fetched_bodies: dict[int, str] = {
        since_index + offset: connection_store.fetch_context_body(c)
        for offset, c in enumerate(contexts)
        if c.type in _CONNECTION_BACKED_TYPES
    }

    index_path = workstore.render_agent_contexts(
        work_slug,
        agent_slug,
        merged,
        fetched_bodies,
        since_index=since_index,
    )
    workstore.replace_agent_contexts(work_slug, agent_slug, merged)

    all_filenames = derive_filenames(merged)
    new_filenames = tuple(all_filenames[since_index:])
    new_file_paths: tuple[str, ...] = ()
    if index_path is not None:
        context_dir = Path(index_path).parent / "context"
        new_file_paths = tuple(str(context_dir / name) for name in new_filenames)
    return AddContextsResult(
        new_filenames=new_filenames,
        index_path=index_path,
        new_file_paths=new_file_paths,
    )


__all__ = [
    "AddContextsResult",
    "AgentNotFound",
    "append_contexts",
]
