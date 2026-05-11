"""Git-backed implementation of WorktreeManager.

Shells out to ``git worktree`` rather than pulling in gitpython — three
commands (`add`, `remove`, list-via-prune) are easier to reason about as
direct subprocess calls than to translate through a library. All
filesystem mutations stay under the workspace root.

Layout (mirrors architecture):

    <workspace_root>/works/<work_slug>/worktrees/<agent_slug>/

If the source folder isn't a git repo (no ``.git`` and ``git rev-parse``
fails), ``ensure`` returns the source folder directly — agents that
don't need branch isolation keep working without forcing the user to
turn every project into a repo just to use Atelier.

`remove` runs ``git worktree remove`` first, falls back to ``--force``
on lock-stale or dirty trees, and finally to a recursive directory
delete + ``git worktree prune`` so a wedged worktree never blocks
provisioning a fresh one.
"""

from __future__ import annotations

import logging
import shutil
import subprocess
from pathlib import Path

from src.domain.worktrees.ports import WorktreeProvisionFailed
from src.infrastructure.filesystem.paths import WorkspacePaths

_log = logging.getLogger(__name__)


class GitWorktreeManager:
    def __init__(self, paths: WorkspacePaths) -> None:
        self._paths = paths

    # -- public API (matches WorktreeManager Protocol) ----------------

    def ensure(
        self,
        work_slug: str,
        agent_slug: str,
        source: Path,
        base_ref: str = "HEAD",
        branch_name: str | None = None,
    ) -> Path:
        target = self._worktree_path(work_slug, agent_slug)
        if not _is_git_repo(source):
            return source
        if target.exists() and (target / ".git").exists():
            # Idempotent: already provisioned. Trust the existing
            # checkout — the caller is the start_agent path and a
            # double-launch is the sole way to hit this branch.
            return target
        # Make sure the parent dir exists so `git worktree add` doesn't
        # fail on the first agent in a brand-new work.
        target.parent.mkdir(parents=True, exist_ok=True)
        if branch_name is None:
            return self._add_detached(work_slug, agent_slug, source, target, base_ref)
        return self._add_branch(work_slug, agent_slug, source, target, base_ref, branch_name)

    def _add_detached(
        self,
        work_slug: str,
        agent_slug: str,
        source: Path,
        target: Path,
        base_ref: str,
    ) -> Path:
        """Detached HEAD — the default. The user/agent names a branch
        when they're ready (``git switch -c <name>``). Symmetric with
        ``ensure_forked``'s shape."""
        try:
            _run_git(source, "worktree", "add", "--detach", str(target), base_ref)
            return target
        except subprocess.CalledProcessError as exc:
            raise WorktreeProvisionFailed(
                f"git worktree add --detach failed for {work_slug}/{agent_slug}: "
                f"{_stderr(exc)}",
                stderr=_stderr(exc),
            ) from exc

    def _add_branch(
        self,
        work_slug: str,
        agent_slug: str,
        source: Path,
        target: Path,
        base_ref: str,
        branch_name: str,
    ) -> Path:
        """Named branch — opt-in via the New Agent modal. Falls back to
        attaching when the branch already exists, prunes a stale registry
        entry on collision, and only then surfaces failure."""
        try:
            _run_git(
                source,
                "worktree",
                "add",
                "-b",
                branch_name,
                str(target),
                base_ref,
            )
            return target
        except subprocess.CalledProcessError as add_with_branch_exc:
            stderr = (add_with_branch_exc.stderr or "").lower()
            if "already exists" not in stderr:
                # Some other failure (bad base ref, locked index, etc.)
                # — surface it cleanly with stderr included.
                raise WorktreeProvisionFailed(
                    f"git worktree add failed for {work_slug}/{agent_slug}: "
                    f"{_stderr(add_with_branch_exc)}",
                    stderr=_stderr(add_with_branch_exc),
                ) from add_with_branch_exc

        # Branch existed — retry by attaching to it.
        try:
            _run_git(source, "worktree", "add", str(target), branch_name)
            return target
        except subprocess.CalledProcessError as attach_exc:
            attach_stderr = _stderr(attach_exc)
            # Common rot path: a previous worktree at the same target
            # was wiped from disk (e.g. via wipe.sh) but not pruned from
            # git's registry, so the branch is "checked out elsewhere"
            # at a missing dir. Prune and retry once. After this,
            # everything is real — surface failure with stderr.
            _log.warning(
                "git worktree add retry failed for %s/%s (%s); pruning + retrying",
                work_slug,
                agent_slug,
                attach_stderr,
            )
            try:
                _run_git(source, "worktree", "prune")
            except subprocess.CalledProcessError as prune_exc:
                # Prune failures are unusual but not fatal here — the
                # next attempt will surface a clean error if attach
                # still doesn't work.
                _log.warning(
                    "git worktree prune failed for %s: %s",
                    source,
                    _stderr(prune_exc),
                )
            try:
                _run_git(source, "worktree", "add", str(target), branch_name)
                return target
            except subprocess.CalledProcessError as final_exc:
                raise WorktreeProvisionFailed(
                    f"git worktree add failed for {work_slug}/{agent_slug} "
                    f"(branch {branch_name} already exists and could not be "
                    f"attached): {_stderr(final_exc)}",
                    stderr=_stderr(final_exc),
                ) from final_exc

    def is_detached(self, workdir: Path) -> bool:
        if not _is_git_repo(workdir):
            return False
        try:
            # symbolic-ref returns 0 + the ref name for branches, non-zero
            # in detached HEAD. -q suppresses the stderr message.
            _run_git(workdir, "symbolic-ref", "-q", "HEAD")
            return False
        except subprocess.CalledProcessError:
            return True

    def ensure_forked(
        self,
        work_slug: str,
        new_agent_slug: str,
        source_agent_slug: str,
        source: Path,
    ) -> Path:
        """Provision a new agent's worktree as a fork of an existing
        agent's worktree. See ``WorktreeManager.ensure_forked``.

        Git source: ``worktree add --detach`` at the source agent's HEAD
        + an overlay of the source's modified + untracked-not-gitignored
        files. ``--detach`` means no auto-branch — the new agent starts
        in detached HEAD and the user names a branch when they're ready.

        Non-git source: falls back to a plain recursive copy (a non-git
        agent's "workdir" is the source folder itself, so this gives the
        new agent a clean copy alongside).
        """
        target = self._worktree_path(work_slug, new_agent_slug)
        source_worktree = self._worktree_path(work_slug, source_agent_slug)

        if not _is_git_repo(source):
            # Non-git: source agent uses ``source`` directly. Copy the
            # whole tree to the new agent's slot.
            target.parent.mkdir(parents=True, exist_ok=True)
            if target.exists():
                return target
            shutil.copytree(source, target)
            return target

        if target.exists() and (target / ".git").exists():
            return target
        target.parent.mkdir(parents=True, exist_ok=True)

        # If the source agent never got a worktree (e.g. a non-git fork
        # path that later became git), fall back to plain ensure().
        if not source_worktree.exists():
            return self.ensure(work_slug, new_agent_slug, source)

        source_head = _run_git(
            source_worktree, "rev-parse", "HEAD"
        ).stdout.strip()
        _run_git(
            source,
            "worktree",
            "add",
            "--detach",
            str(target),
            source_head,
        )
        _overlay_working_state(source_worktree, target)
        return target

    def remove(self, work_slug: str, agent_slug: str) -> None:
        target = self._worktree_path(work_slug, agent_slug)
        source = self._source_for(target) if target.exists() else None
        # If we only know the source via the live worktree, fish it out
        # before the dir disappears. (target.exists() check above already
        # populated source, but keep the guard for the not-exists path.)
        if not target.exists() and not source:
            # Nothing on disk to clean up. Still try to delete the branch
            # in case a previous incomplete teardown left it behind. We
            # need a source repo to do that; without one (rare — only
            # happens for non-git source), there's nothing more to do.
            return
        try:
            if source is not None:
                _run_git(source, "worktree", "remove", str(target))
                self._delete_atelier_branch(source, work_slug, agent_slug)
                return
        except subprocess.CalledProcessError as exc:
            _log.warning(
                "git worktree remove failed for %s/%s: %s; trying --force",
                work_slug,
                agent_slug,
                _stderr(exc),
            )
        # Fallback 1: --force handles dirty trees + lock files.
        try:
            if source is not None:
                _run_git(source, "worktree", "remove", "--force", str(target))
                self._delete_atelier_branch(source, work_slug, agent_slug)
                return
        except subprocess.CalledProcessError as exc:
            _log.warning(
                "git worktree remove --force failed for %s/%s: %s; falling back to rmtree",
                work_slug,
                agent_slug,
                _stderr(exc),
            )
        # Fallback 2: nuke the directory and prune the parent's
        # worktree registry. Last resort but bounded — the dir is
        # always under the workspace root.
        shutil.rmtree(target, ignore_errors=True)
        if source is not None:
            try:
                _run_git(source, "worktree", "prune")
            except subprocess.CalledProcessError:
                pass
            self._delete_atelier_branch(source, work_slug, agent_slug)

    def sweep_orphans(self, work_slug: str, live_agent_slugs: set[str]) -> None:
        root = self._paths.workspace_root / "works" / work_slug / "worktrees"
        if not root.exists():
            return
        for child in root.iterdir():
            if not child.is_dir():
                continue
            if child.name in live_agent_slugs:
                continue
            self.remove(work_slug, child.name)

    # -- internals ----------------------------------------------------

    def _delete_atelier_branch(
        self, source: Path, work_slug: str, agent_slug: str
    ) -> None:
        """Best-effort delete of the per-agent ``atelier/<work>/<agent>``
        branch in the source repo after teardown. Without this, a future
        agent that gets the same slug (after wipe + recreate) collides
        with the leftover branch and fails to provision a worktree.

        Failures are swallowed: the branch may legitimately not exist
        (non-git source, manual cleanup, etc.) and we don't want a
        teardown to error on housekeeping.
        """
        try:
            _run_git(source, "branch", "-D", _branch_name(work_slug, agent_slug))
        except subprocess.CalledProcessError:
            pass

    def _worktree_path(self, work_slug: str, agent_slug: str) -> Path:
        return (
            self._paths.workspace_root
            / "works"
            / work_slug
            / "worktrees"
            / agent_slug
        )

    def _source_for(self, worktree: Path) -> Path | None:
        """Resolve the source repo for an existing worktree by reading
        its ``.git`` pointer file. Returns None if the worktree is
        already detached from a host repo (rare but possible after
        manual filesystem edits)."""
        gitfile = worktree / ".git"
        if not gitfile.is_file():
            return None
        try:
            content = gitfile.read_text().strip()
        except OSError:
            return None
        # Format: "gitdir: /path/to/source/.git/worktrees/<name>"
        if not content.startswith("gitdir:"):
            return None
        gitdir = Path(content.split(":", 1)[1].strip())
        # The source repo is two parents up from .git/worktrees/<name>.
        source = gitdir.parent.parent.parent
        return source if source.exists() else None


def _overlay_working_state(src: Path, dst: Path) -> None:
    """Copy src's modified-vs-HEAD and untracked-not-gitignored files onto
    dst. dst is already at src's HEAD (provisioned via
    ``git worktree add --detach``), so this only needs to overlay the
    delta — keeps the fork fast even when src has node_modules.
    """
    modified = _run_git(src, "diff", "HEAD", "--name-only", "-z").stdout
    untracked = _run_git(
        src, "ls-files", "-o", "--exclude-standard", "-z"
    ).stdout
    paths = [p for p in (modified + untracked).split("\0") if p]
    for rel in paths:
        src_file = src / rel
        if not src_file.exists() or not src_file.is_file():
            # Could be a file deleted in src's working tree (modified
            # diff includes deletions) or a directory; skip both.
            continue
        dst_file = dst / rel
        dst_file.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(src_file, dst_file)


def _is_git_repo(path: Path) -> bool:
    if not path.exists():
        return False
    try:
        _run_git(path, "rev-parse", "--git-dir")
        return True
    except (subprocess.CalledProcessError, FileNotFoundError):
        return False


def _branch_name(work_slug: str, agent_slug: str) -> str:
    """Branch name pattern: ``atelier/<work>/<agent>`` — namespaced so
    multi-agent runs don't collide and the user can spot them in
    ``git branch``."""
    return f"atelier/{work_slug}/{agent_slug}"


def _run_git(cwd: Path, *args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ["git", *args],
        cwd=str(cwd),
        check=True,
        capture_output=True,
        text=True,
    )


def _stderr(exc: subprocess.CalledProcessError) -> str:
    return (exc.stderr or "").strip()


__all__ = ["GitWorktreeManager"]
