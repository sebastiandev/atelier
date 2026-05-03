"""WorktreeManager port.

A WorktreeManager provisions a per-agent working directory for adapter
processes. When the work's `folder` is a git repo, the manager creates a
``git worktree add`` checkout under
``<workspace_root>/works/<work_slug>/worktrees/<agent_slug>/`` so each
agent gets its own branch + index without stepping on the user's main
checkout. When the folder is *not* a git repo, the manager falls back to
returning the folder itself: agents that don't need branch isolation
keep working.

The seam stays narrow on purpose — three operations:

  - ``ensure(work_slug, agent_slug, source, base_ref)`` — provision and
    return the workdir. Idempotent: re-calling for the same agent_slug
    returns the existing path.
  - ``remove(work_slug, agent_slug)`` — tear down. Quiet on missing.
  - ``sweep_orphans(work_slug, live_agent_slugs)`` — startup cleanup;
    removes worktrees under the work that don't appear in the live set.
"""

from pathlib import Path
from typing import Protocol


class WorktreeManager(Protocol):
    def ensure(
        self,
        work_slug: str,
        agent_slug: str,
        source: Path,
        base_ref: str = "HEAD",
    ) -> Path: ...

    def remove(self, work_slug: str, agent_slug: str) -> None: ...

    def sweep_orphans(self, work_slug: str, live_agent_slugs: set[str]) -> None: ...
