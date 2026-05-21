"""Rename an agent — update the SQL row and ``agent.json``.

Display-name changes go through this command so the FS/DB seam stays
the workstore's concern, and the route layer keeps to its
``parse → call command → format`` shape.

The rename is FS-canonical (reconcile resolves agent identity from
``agent.json``); ``workstore.rename_agent`` writes both sides
atomically per-side. No supervisor or worktree state is touched —
``name`` is purely metadata.
"""

from __future__ import annotations

from dataclasses import dataclass

from src.domain.models import Agent
from src.domain.workstore.ports import WorkStore


@dataclass(frozen=True)
class RenameAgentRequest:
    agent_slug: str
    name: str


class AgentNotFound(ValueError):
    """The agent slug doesn't resolve to a stored agent."""


def execute(workstore: WorkStore, req: RenameAgentRequest) -> Agent:
    work_slug = workstore.get_work_slug_for_agent(req.agent_slug)
    if work_slug is None:
        raise AgentNotFound(f"agent not found: {req.agent_slug}")
    try:
        return workstore.rename_agent(req.agent_slug, req.name)
    except ValueError as exc:
        # workstore raises bare ``ValueError`` when the slug resolves at
        # the work level but the agent row is gone (rare race; the
        # caller maps both shapes to a 404 the same way).
        raise AgentNotFound(str(exc)) from exc


__all__ = ["AgentNotFound", "RenameAgentRequest", "execute"]
