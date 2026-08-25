"""Git-backed implementation of WorktreeManager.

Shells out to ``git worktree`` rather than pulling in gitpython — three
commands (`add`, `remove`, list-via-prune) are easier to reason about as
direct subprocess calls than to translate through a library. All
filesystem mutations stay under the workspace root.

Layout (mirrors architecture):

    <workspace_root>/works/<work_slug>/worktrees/<worktree_slug>/

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
import shlex
import shutil
import subprocess
from filecmp import cmp
from pathlib import Path

from src.domain.worktrees.ports import WorktreeProvisionFailed, WorktreeState
from src.infrastructure.filesystem.paths import WorkspacePaths
from src.infrastructure.git.env import auth_hint, git_env

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
        base_ref = self._fresh_base_ref(source, base_ref)
        # Make sure the parent dir exists so `git worktree add` doesn't
        # fail on the first agent in a brand-new work.
        target.parent.mkdir(parents=True, exist_ok=True)
        base_ref = _resolve_base_ref(source, base_ref)
        if branch_name is None:
            return self._add_detached(work_slug, agent_slug, source, target, base_ref)
        return self._add_branch(work_slug, agent_slug, source, target, base_ref, branch_name)

    @staticmethod
    def _fresh_base_ref(source: Path, base_ref: str) -> str:
        """Resolve a fresh default checkout to the current remote HEAD."""
        if base_ref != "HEAD":
            return base_ref
        try:
            _run_git(source, "remote", "get-url", "origin")
        except subprocess.CalledProcessError:
            return base_ref
        try:
            _run_git(source, "fetch", "--quiet", "origin", "HEAD")
            revision = _git_out(source, "rev-parse", "FETCH_HEAD")
        except subprocess.CalledProcessError as exc:
            detail = _stderr(exc)
            raise WorktreeProvisionFailed(
                f"git fetch origin HEAD failed for {source}: {detail}",
                stderr=f"{detail}{auth_hint(detail)}",
            ) from exc
        if not revision:
            raise WorktreeProvisionFailed(
                f"git fetch origin HEAD did not resolve a revision for {source}"
            )
        return revision

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
        except subprocess.CalledProcessError as exc:
            raise WorktreeProvisionFailed(
                f"git worktree add --detach failed for {work_slug}/{agent_slug}: "
                f"{_stderr(exc)}",
                stderr=_stderr(exc),
            ) from exc
        _symlink_devtime_artifacts(source, target)
        return target

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
            _symlink_devtime_artifacts(source, target)
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
            _symlink_devtime_artifacts(source, target)
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
                _symlink_devtime_artifacts(source, target)
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

    def describe_state(self, workdir: Path) -> WorktreeState:
        if not workdir.exists():
            return WorktreeState(
                workdir=workdir,
                is_git_repo=False,
                error=f"workdir does not exist: {workdir}",
            )
        if not _is_git_repo(workdir):
            return WorktreeState(workdir=workdir, is_git_repo=False)

        branch = _git_out(workdir, "branch", "--show-current") or None
        head = _git_out(workdir, "rev-parse", "--short", "HEAD") or None
        raw_status = _git_stdout(workdir, "status", "--short")
        source_root = _worktree_source_root(workdir)
        status_lines: list[str] = []
        changed: list[str] = []
        untracked: list[str] = []
        for line in raw_status.splitlines():
            if not line:
                continue
            raw_path = line[3:] if len(line) > 3 else line.strip()
            parts = shlex.split(raw_path)
            path = parts[-1] if parts else raw_path
            if (workdir / path).is_symlink():
                continue
            if line.startswith("?? ") and _is_legacy_env_copy(
                workdir, source_root, path
            ):
                continue
            status_lines.append(line)
            if line.startswith("?? "):
                untracked.append(path)
            else:
                changed.append(path)
        status = "\n".join(status_lines)
        return WorktreeState(
            workdir=workdir,
            is_git_repo=True,
            branch=branch,
            head=head,
            status=status,
            changed_files=tuple(changed),
            untracked_files=tuple(untracked),
        )

    def list_states(self, work_slug: str) -> tuple[WorktreeState, ...]:
        root = self._paths.workspace_root / "works" / work_slug / "worktrees"
        if not root.exists():
            return ()
        return tuple(
            self.describe_state(path)
            for path in sorted(root.iterdir(), key=lambda item: item.name)
            if path.is_dir()
        )

    def sandbox_writable_roots(self, workdir: Path) -> tuple[Path, ...]:
        workdir = workdir.resolve(strict=False)
        if not _is_git_repo(workdir):
            return ()

        common_dir = _git_out(
            workdir, "rev-parse", "--path-format=absolute", "--git-common-dir"
        )
        if not common_dir:
            return ()
        common_path = Path(common_dir).resolve(strict=False)
        try:
            common_path.relative_to(workdir)
        except ValueError:
            return (common_path,)
        return ()

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
        # Symlink devtime artifacts from the ORIGINAL source (not the
        # forked-from agent's worktree) — the upstream venv / node_modules
        # is what stays in sync with the canonical repo layout. The
        # source agent's worktree typically just has a symlink to the
        # same place anyway.
        _symlink_devtime_artifacts(source, target)
        return target

    def remove(
        self, work_slug: str, agent_slug: str, *, force: bool = True
    ) -> None:
        """Remove a managed worktree without following filesystem symlinks."""
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
            if not force:
                state = self.describe_state(target)
                if not state.is_git_repo or state.error is not None or state.status:
                    raise WorktreeProvisionFailed(
                        f"git worktree remove failed for {work_slug}/{agent_slug}: "
                        f"{_stderr(exc)}",
                        stderr=_stderr(exc),
                    ) from exc
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
        dst_file = dst / rel
        if not src_file.exists():
            if dst_file.is_file() or dst_file.is_symlink():
                dst_file.unlink()
            continue
        if src_file.is_dir():
            if dst_file.is_file() or dst_file.is_symlink():
                dst_file.unlink()
            continue
        dst_file.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(src_file, dst_file)


# Devtime artifacts we mirror from the source repo into every fresh
# worktree. Symlinks (not copies) so agents share one environment by
# default — fast, zero extra disk, package upgrades propagate. The risk
# is parallel mutating installs (one agent's ``uv add`` clobbers a
# sibling's lockfile); the system prompt should warn about this. Names
# match the canonical conventions across uv/poetry/pipenv/npm/yarn.
_DEVTIME_ARTIFACT_NAMES = (".venv", "venv", "node_modules")
# Env files are gitignored too, but they're plain files rather than
# directories. They're load-bearing in a different way: the backend's
# pydantic-settings reads them at startup, and Vite's ``loadEnv`` reads
# them at dev-server start (then ``define`` bakes the values into the
# bundle at build time). Same names every common toolchain looks for —
# dotenv, pydantic-settings, Vite, Next.js, etc.
_DEVTIME_ENV_FILES = (
    ".env",
    ".env.local",
    ".env.development",
    ".env.development.local",
    ".env.production",
    ".env.production.local",
)
# Agent tooling config. Unlike the two classes above these dirs are
# *partly* tracked: the repo commits ``.agents/architecture.md`` and its
# docs while gitignoring the installed skills (bmad-*, speckit-*) and
# ``.claude/settings.local.json``. Git gives the worktree the tracked
# half and nothing else, so the agent lands in a checkout where its own
# skills and permissions are missing. Mirrored by merging children
# rather than linking the dir, since the dir itself already exists.
_AGENT_CONFIG_DIRS = (".agents", ".claude", ".codex")


def _symlink_devtime_artifacts(source: Path, target: Path) -> None:
    """Mirror gitignored dev-time artifacts from ``source`` into ``target``
    as symlinks. Two classes:

      - **Dirs** — ``.venv`` / ``venv`` / ``node_modules`` (per
        ``_DEVTIME_ARTIFACT_NAMES``). Saves a multi-GB reinstall per
        agent and lets siblings share one env.
      - **Files** — ``.env*`` (per ``_DEVTIME_ENV_FILES``). Without
        these the agent's app can't boot (pydantic raises on required
        fields, Vite silently bakes empty strings via ``define``).
      - **Agent config** — ``.agents`` / ``.claude`` / ``.codex`` (per
        ``_AGENT_CONFIG_DIRS``), merged child by child so the tracked
        half git already checked out survives. Without this the agent
        runs without the skills and permissions the repo's own humans
        have. Directories are shared by link, loose files are *copied*
        — an agent rewrites its own config, and a linked one would
        write through into the human's checkout.

    Both classes are scanned at the source's top level and one level
    down (so monorepos with ``backend/.venv`` + ``frontend/.env.local``
    are covered). No-op for any name the source doesn't have.

    Failures are logged and swallowed — the worktree itself is already
    provisioned by the time we run, and a missing convenience symlink is
    a degradation (the agent can ``uv sync`` or ``cp .env.local`` to
    recover), not a reason to fail the agent launch.
    """
    try:
        _link_top_level(source, target)
        _link_one_level_deep(source, target)
        _link_agent_config(source, target)
    except OSError as exc:
        _log.warning(
            "devtime artifact symlinking partially failed for %s → %s: %s",
            source,
            target,
            exc,
        )


def _link_top_level(source: Path, target: Path) -> None:
    for name in _DEVTIME_ARTIFACT_NAMES:
        _maybe_symlink(source / name, target / name)
    for name in _DEVTIME_ENV_FILES:
        _maybe_symlink_file(source / name, target / name)


def _link_one_level_deep(source: Path, target: Path) -> None:
    # Skip hidden dirs (notably ``.git``) and anything that isn't a
    # directory on disk — a regular file named ``backend`` shouldn't
    # cause us to fabricate a target subdir.
    for child in source.iterdir():
        if not child.is_dir() or child.name.startswith("."):
            continue
        target_child = target / child.name
        # Only mirror into subdirs that actually exist in the worktree
        # (i.e. the source's subdir contains tracked files). Creating an
        # otherwise-empty ``backend/`` just to hold a venv symlink would
        # mislead path-scanning tools.
        if not target_child.is_dir():
            continue
        for name in _DEVTIME_ARTIFACT_NAMES:
            _maybe_symlink(child / name, target_child / name)
        for name in _DEVTIME_ENV_FILES:
            _maybe_symlink_file(child / name, target_child / name)


def _link_agent_config(source: Path, target: Path) -> None:
    """Merge the source's agent-config dirs into the worktree.

    ``.agents`` and friends are usually partly tracked, so the worktree
    already holds a real directory at that path and the whole-dir link
    ``_maybe_symlink`` would make is neither possible nor wanted. Walk
    instead: descend while both sides have a directory, and fill in the
    first entry the target lacks — linking directories, copying files
    (see ``_copy_agent_config_file``). That leaves every checked-out
    file untouched and fills in exactly the gitignored remainder.
    """
    for name in _AGENT_CONFIG_DIRS:
        _merge_link_tree(source / name, target / name)


def _merge_link_tree(src: Path, dst: Path) -> None:
    # Only real dirs on both sides recurse; a symlinked src would let a
    # link chain out of the repo, and a file at dst is the user's.
    if not src.is_dir() or src.is_symlink():
        return
    dst.mkdir(parents=True, exist_ok=True)
    for child in src.iterdir():
        if child.name == ".DS_Store":
            continue
        link = dst / child.name
        if link.is_dir() and not link.is_symlink():
            _merge_link_tree(child, link)
            continue
        if link.exists() or link.is_symlink():
            continue
        if child.is_dir():
            _maybe_symlink(child, link)
        else:
            _copy_agent_config_file(child, link)


def _copy_agent_config_file(src: Path, dst: Path) -> None:
    """Copy, never link, a loose agent-config file into the worktree.

    Linking these breaks the isolation the worktree exists for: the file
    an agent is most likely to *write* is its own config --
    ``.claude/settings.local.json`` grows an entry every time an agent
    grants itself a permission -- and through a symlink that write lands
    in the human's checkout and outlives the worktree it came from.
    A copy gives the agent the same starting state and keeps its edits
    where they can be thrown away.

    Directories are still shared by link (see ``_merge_link_tree``):
    installed skills run to tens of megabytes and copying them per
    worktree would be wasteful. The trade is deliberate — writes *inside*
    a shared directory do reach the source, which is what makes editing
    a skill from a worktree work.
    """
    if not src.is_file():
        return
    if dst.exists() or dst.is_symlink():
        return
    try:
        shutil.copy2(src, dst)
    except OSError as exc:
        _log.debug("could not copy agent config %s → %s: %s", src, dst, exc)


def _maybe_symlink(src: Path, link: Path) -> None:
    # Only mirror directories (e.g. a ``.venv`` file would be weird and
    # we don't want to risk symlinking a real file). Don't clobber an
    # existing path at the link site — if the user (or a prior pass)
    # already put something there, leave it alone.
    if not src.is_dir():
        return
    if link.exists() or link.is_symlink():
        return
    link.symlink_to(src, target_is_directory=True)


def _maybe_symlink_file(src: Path, link: Path) -> None:
    # Peer of ``_maybe_symlink`` for plain files (env files only today).
    # ``is_file`` excludes dirs and broken symlinks; we deliberately
    # don't chase symlinks at the source — those are rare and could
    # create surprising link chains. Same don't-clobber rule as the
    # dir variant.
    if not src.is_file():
        return
    if link.exists() or link.is_symlink():
        return
    link.symlink_to(src, target_is_directory=False)


def _worktree_source_root(workdir: Path) -> Path | None:
    """Return the primary checkout that owns a linked worktree."""
    common = Path(_git_out(workdir, "rev-parse", "--git-common-dir"))
    common = common if common.is_absolute() else workdir / common
    common = common.resolve()
    return common.parent if common.name == ".git" else None


def _is_legacy_env_copy(
    workdir: Path,
    source_root: Path | None,
    relative_path: str,
) -> bool:
    """Return whether an untracked env file is an unchanged legacy mirror."""
    path = Path(relative_path)
    if (
        source_root is None
        or path.name not in _DEVTIME_ENV_FILES
        or len(path.parts) > 2
    ):
        return False
    source = source_root / path
    copy = workdir / path
    try:
        return source.is_file() and copy.is_file() and cmp(source, copy, shallow=False)
    except OSError:
        return False


def _is_git_repo(path: Path) -> bool:
    if not path.exists():
        return False
    try:
        _run_git(path, "rev-parse", "--git-dir")
        return True
    except (subprocess.CalledProcessError, FileNotFoundError):
        return False


def _resolve_base_ref(source: Path, base_ref: str) -> str:
    if base_ref != "master":
        return base_ref
    if _ref_exists(source, "master"):
        return "master"
    if _ref_exists(source, "main"):
        return "main"
    return base_ref


def _ref_exists(source: Path, ref: str) -> bool:
    try:
        _run_git(source, "rev-parse", "--verify", "--quiet", f"{ref}^{{commit}}")
        return True
    except subprocess.CalledProcessError:
        return False


def _branch_name(work_slug: str, agent_slug: str) -> str:
    """Branch name pattern: ``atelier/<work>/<agent>`` — namespaced so
    multi-agent runs don't collide and the user can spot them in
    ``git branch``."""
    return f"atelier/{work_slug}/{agent_slug}"


# Long enough for a cold fetch on a large repo over a slow link, short
# enough that a wedged call fails inside one impatient user's attention
# span rather than never. Paired with git_env(), which is what stops the
# call blocking on a prompt in the first place.
_GIT_TIMEOUT_SECONDS = 180


def _run_git(cwd: Path, *args: str) -> subprocess.CompletedProcess[str]:
    try:
        return subprocess.run(
            ["git", *args],
            cwd=str(cwd),
            check=True,
            capture_output=True,
            text=True,
            env=git_env(),
            timeout=_GIT_TIMEOUT_SECONDS,
            # No inherited terminal to read from, so a git that ignores
            # the env above still fails rather than blocking on input.
            stdin=subprocess.DEVNULL,
        )
    except subprocess.TimeoutExpired as exc:
        # Re-raised as CalledProcessError so every call site keeps its
        # existing handling — the ones that tolerate failure still
        # tolerate it, the ones that wrap it still wrap it.
        raise subprocess.CalledProcessError(
            returncode=128,
            cmd=["git", *args],
            output=_decode(exc.stdout),
            stderr=(
                f"git {args[0] if args else ''} timed out after "
                f"{_GIT_TIMEOUT_SECONDS}s in {cwd}"
            ),
        ) from exc


def _decode(raw: str | bytes | None) -> str:
    if raw is None:
        return ""
    if isinstance(raw, bytes):
        return raw.decode("utf-8", "replace")
    return raw



def _git_out(cwd: Path, *args: str) -> str:
    return _git_stdout(cwd, *args).strip()


def _git_stdout(cwd: Path, *args: str) -> str:
    try:
        return _run_git(cwd, *args).stdout
    except (subprocess.CalledProcessError, FileNotFoundError):
        return ""


def _stderr(exc: subprocess.CalledProcessError) -> str:
    return (exc.stderr or "").strip()


__all__ = ["GitWorktreeManager"]
