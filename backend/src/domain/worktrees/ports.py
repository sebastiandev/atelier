"""WorktreeManager port.

A WorktreeManager provisions a per-agent working directory for adapter
processes. When the work's `folder` is a git repo, the manager creates a
``git worktree add`` checkout under
``<workspace_root>/works/<work_slug>/worktrees/<agent_slug>/`` so each
agent gets its own branch + index without stepping on the user's main
checkout. When the folder is *not* a git repo, the manager falls back to
returning the folder itself: agents that don't need branch isolation
keep working.

The seam stays narrow on purpose:

  - ``ensure(work_slug, agent_slug, source, base_ref)`` — provision and
    return the workdir. Idempotent: re-calling for the same agent_slug
    returns the existing path.
  - ``is_detached(workdir)`` / ``describe_state(workdir)`` — read-only
    state for prompts and compaction summaries.
  - ``sandbox_writable_roots(workdir)`` — extra filesystem roots an
    adapter sandbox needs to mutate this worktree correctly.
  - ``remove(work_slug, agent_slug)`` — tear down. Quiet on missing.
  - ``sweep_orphans(work_slug, live_agent_slugs)`` — startup cleanup;
    removes worktrees under the work that don't appear in the live set.
"""

from dataclasses import dataclass
from pathlib import Path
from typing import Protocol


class WorktreeProvisionFailed(RuntimeError):
    """Raised when ``ensure``/``ensure_forked`` exhausted its self-heal
    attempts and still couldn't produce a usable worktree.

    Carries ``stderr`` so the application layer (route handler / WS
    handler) can surface a meaningful message to the user instead of an
    opaque non-zero-exit traceback. Typical causes: branch already
    checked out elsewhere with a missing prunable worktree, locked git
    index in the source repo, or a base ref that doesn't exist.
    """

    def __init__(self, message: str, *, stderr: str = "") -> None:
        super().__init__(message)
        self.stderr = stderr


@dataclass(frozen=True)
class WorktreeState:
    workdir: Path
    is_git_repo: bool
    branch: str | None = None
    head: str | None = None
    status: str = ""
    changed_files: tuple[str, ...] = ()
    untracked_files: tuple[str, ...] = ()
    error: str | None = None


class WorktreeManager(Protocol):
    def ensure(
        self,
        work_slug: str,
        agent_slug: str,
        source: Path,
        base_ref: str = "HEAD",
        branch_name: str | None = None,
    ) -> Path:
        """Provision a per-agent worktree.

        ``branch_name`` is the optional name of a branch to create (or
        attach to, if it already exists) on ``base_ref``. When ``None``
        (the default), the worktree starts in **detached HEAD** — no
        auto-named branch is created. The user/agent is expected to
        ``git switch -c <name>`` before checking out anything else if
        they want to keep the work.
        """
        ...

    def is_detached(self, workdir: Path) -> bool:
        """Whether ``workdir`` is currently checked out in detached HEAD.
        Returns ``False`` for non-git folders so callers can use it as a
        soft hint without branching on the git-vs-not-git case."""
        ...

    def describe_state(self, workdir: Path) -> WorktreeState:
        """Return branch/status information for compaction and handoff docs.

        Non-git folders are valid workdirs; implementations should return
        ``is_git_repo=False`` rather than raising.
        """
        ...

    def sandbox_writable_roots(self, workdir: Path) -> tuple[Path, ...]:
        """Extra writable roots required by sandboxed providers.

        Git worktrees can store mutable metadata outside ``workdir`` in the
        source repo's shared ``.git`` directory. Providers such as Codex
        need that directory in their sandbox to create branches from a
        detached worktree. Non-git folders should return ``()``.
        """
        ...

    def ensure_forked(
        self,
        work_slug: str,
        new_agent_slug: str,
        source_agent_slug: str,
        source: Path,
    ) -> Path:
        """Provision a new worktree by forking from an existing agent's
        worktree. The new worktree starts at the source agent's HEAD
        commit (in detached HEAD — no auto-branch) and inherits the
        source's uncommitted + untracked-not-gitignored files. Source
        and new worktree are independent thereafter — both can keep
        working without colliding.

        For non-git source folders, falls back to a recursive directory
        copy (the simpler model — no branch concerns)."""
        ...

    def remove(self, work_slug: str, agent_slug: str) -> None: ...

    def sweep_orphans(self, work_slug: str, live_agent_slugs: set[str]) -> None: ...
