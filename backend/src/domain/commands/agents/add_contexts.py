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
from pathlib import Path
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

    index_path: str | None
    """Absolute path to the (rebuilt) ``context.md`` index, or ``None``
    when ``new_filenames`` is empty (the command was a no-op). The
    caller uses this to point the SDK at the file with an absolute
    path — relative ``context.md`` would resolve against the adapter's
    cwd (the per-agent worktree), which doesn't carry the index."""

    new_file_paths: tuple[str, ...]
    """Absolute paths to the just-added per-source files. Same length
    and order as ``new_filenames`` — derived from the index dir + the
    sub-folder name. The auto-prepend hint references these directly so
    the SDK's Read tool resolves them on first try."""


class AgentNotFound(ValueError):
    """The agent_slug doesn't resolve to a stored agent."""


def execute(
    workstore: WorkStore,
    connection_store: ConnectionStore,
    req: AddContextsRequest,
) -> AddContextsResult:
    if not req.contexts:
        return AddContextsResult(
            new_filenames=(), index_path=None, new_file_paths=()
        )

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

    index_path = workstore.render_agent_contexts(
        work_slug,
        req.agent_slug,
        merged,
        fetched_bodies,
        since_index=since_index,
    )
    workstore.replace_agent_contexts(work_slug, req.agent_slug, merged)

    all_filenames = derive_filenames(merged)
    new_filenames = tuple(all_filenames[since_index:])
    # Per-source files live in ``<index_dir>/context/<filename>`` — same
    # layout the renderer writes to. Pre-compute absolute paths so the
    # caller doesn't have to know the workspace layout.
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
    "AddContextsRequest",
    "AddContextsResult",
    "AgentNotFound",
    "execute",
]
