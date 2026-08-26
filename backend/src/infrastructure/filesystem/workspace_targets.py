"""Resolve the directory a launcher should act on.

Reveal, open-in-console and (with the Emacs work) open-in-editor all
answer the same question first — *which directory?* — and then differ
only in what they launch. That question is answered here, once, so the
routes stay thin and the three launchers cannot drift onto different
directories for the same entity.

Two entities own a workspace:

- an **agent**, whose per-agent worktree is where its adapter runs;
- a **run**, which for a Loop-mode run has a workspace of its own, and
  for a planning run has none — planning stages execute in their agent's
  worktree, so the run defers to the agent that ran it.

Both resolvers raise ``WorkspaceNotFound``; mapping that onto a 404 is
the route's business, not this module's.
"""

from __future__ import annotations

from pathlib import Path

from src.domain.loop.models import LoopRunRecord
from src.domain.workstore.ports import WorkStore
from src.infrastructure.filesystem.paths import WorkspacePaths


class WorkspaceNotFound(LookupError):
    """No directory could be resolved for the requested entity."""


def agent_workspace(
    workstore: WorkStore,
    paths: WorkspacePaths,
    agent_slug: str,
) -> Path:
    """The directory an agent's adapter runs in.

    Mirrors ``WorktreeManager.ensure``'s contract: the per-agent git
    worktree when one was provisioned, otherwise the source folder.
    """
    work_slug = workstore.get_work_slug_for_agent(agent_slug)
    if work_slug is None:
        raise WorkspaceNotFound(f"agent not found: {agent_slug}")
    agent = next(
        (
            item
            for item in workstore.list_agents_for_work(work_slug)
            if item.slug == agent_slug
        ),
        None,
    )
    if agent is None:
        raise WorkspaceNotFound(f"agent not found: {agent_slug}")
    return worktree_or_folder(
        paths, work_slug, agent.worktree_slug or agent_slug, agent.folder
    )


def run_workspace(
    record: LoopRunRecord,
    workstore: WorkStore,
    paths: WorkspacePaths,
) -> Path:
    """The directory a loop run's work happened in.

    A Loop-mode run records its own ``workspace_path`` — and it is the
    authority, because a run seeded from another one keeps the source
    run's workspace rather than taking a fresh worktree. A planning run
    records none: its stages run in the worktree of the agent that owns
    the run, which is what the run surface already falls back to.
    """
    workspace = record.state.get("workspace_path")
    if isinstance(workspace, str) and workspace:
        return Path(workspace)
    agent_slug = record.state.get("agent_slug")
    if isinstance(agent_slug, str) and agent_slug:
        return agent_workspace(workstore, paths, agent_slug)
    raise WorkspaceNotFound(f"run has no workspace: {record.run_key}")


def worktree_or_folder(
    paths: WorkspacePaths, work_slug: str, agent_slug: str, folder: Path
) -> Path:
    """The per-agent worktree if it exists on disk, else the source folder."""
    candidate = paths.worktree_dir(work_slug, agent_slug)
    return candidate if candidate.exists() else folder


__all__ = [
    "WorkspaceNotFound",
    "agent_workspace",
    "run_workspace",
    "worktree_or_folder",
]
