"""Create an agent and register it with the supervisor.

Replaces the previous ``start_plan`` shape. The command is async and
calls the supervisor directly so the route stays thin (parse → call
command → format) and there's one inward path from the endpoint into
domain logic.

Steps:
  1. Validate the requested folder exists / can be created.
  2. Validate the provider config (model + options) before allocating
     state we'd have to roll back on failure.
  3. Pre-fetch connection-backed contexts (jira / sentry / honeycomb).
  4. Add the agent row + render contexts.
  5. Provision the per-agent workdir via the WorktreeManager.
  6. Build the adapter + register with the supervisor. Eager — fresh
     agents have no fork concern (no prior provider session exists),
     so the events pump runs immediately. ``resume`` takes the lazy
     path; see ``register_agent``'s ``lazy`` flag.
  7. If contexts produced a synthesised first message, send it now so
     the agent's first SDK turn includes the context-index pointer.
"""

from __future__ import annotations

import logging
from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING

from src.domain.agents import (
    SPECS,
    AgentStartContext,
    CommonAgentConfig,
    detect_shared_envs,
    render_system_prompt,
)
from src.domain.connections import ConnectionStore
from src.domain.models import Agent, Context, Persona, Provider
from src.domain.sharedfolders.dtos import ShareSummary
from src.domain.sharedfolders.ports import (
    MountConflict,
    SharedFolderStore,
    ShareProvisioner,
)
from src.domain.workstore.dtos import AddAgentRequest
from src.domain.workstore.ports import WorkStore
from src.domain.worktrees import WorktreeManager, WorktreeProvisionFailed
from src.infrastructure.agents import build_adapter
from src.settings import Settings

if TYPE_CHECKING:
    from src.domain.supervisor import AgentSupervisorService

_log = logging.getLogger(__name__)

# Context types whose body must be fetched from an external connection
# at start time. Anything else is rendered inline by the renderer.
_CONNECTION_BACKED_TYPES = frozenset({"jira", "sentry", "honeycomb"})

# Fresh agents should start from the canonical integration branch, not
# whatever branch the user's source checkout currently has selected.
# Handoff/forked agents intentionally bypass this and inherit the source
# agent's own worktree state via ``ensure_forked``.
# The git-backed manager treats this as "master preferred" and falls
# back to "main" for repos that never had a master branch.
FRESH_AGENT_BASE_REF = "master"


@dataclass(frozen=True)
class StartAgentRequest:
    work_slug: str
    name: str
    persona: Persona
    role: str
    provider: Provider
    model: str
    folder: Path
    options: dict[str, object]
    contexts: tuple[Context, ...] = ()
    # Slug of an existing agent in the same work whose worktree the new
    # agent should fork from. None (default) provisions a fresh worktree
    # off ``folder``'s base ref. Used by the handoff flow so the new
    # agent inherits the source's uncommitted work without sharing the
    # working dir.
    fork_from_agent: str | None = None
    # Optional name of a branch to create on the new worktree. ``None``
    # (default) leaves the worktree in detached HEAD and the agent is
    # told (via system prompt) to ``git switch -c <name>`` before
    # checking out anything else. Ignored when ``fork_from_agent`` is
    # set — forks are always detached.
    branch_name: str | None = None


@dataclass(frozen=True)
class MountedProjectShares:
    """Mounted project shares split by audience.

    ``summaries`` are prompt-safe and only name the worktree mount path.
    ``writable_roots`` are internal sandbox roots for providers that need
    the resolved target path to write through the symlink.
    """

    summaries: tuple[ShareSummary, ...] = ()
    writable_roots: tuple[Path, ...] = ()


class WorkNotFound(ValueError):
    """The work_slug doesn't exist."""


class InvalidProviderConfig(ValueError):
    """The provider's Spec.build rejected the supplied model/options.
    The route maps this to 422 — it's a client mistake, not a missing
    resource."""


class AgentFolderMissing(ValueError):
    """The agent's requested folder doesn't resolve to an existing
    directory on disk and can't be created. Adapters spawn their
    underlying process in this directory; if it's missing, the spawn
    surfaces as a cryptic ENOENT from the SDK. The route maps this to
    422 so the user can fix the path."""


async def execute(
    workstore: WorkStore,
    supervisor: AgentSupervisorService,
    worktree_manager: WorktreeManager,
    connection_store: ConnectionStore,
    sharestore: SharedFolderStore,
    share_provisioner: ShareProvisioner,
    settings: Settings,
    req: StartAgentRequest,
) -> Agent:
    record = workstore.get_work(req.work_slug)
    if record is None:
        raise WorkNotFound(f"work not found: {req.work_slug}")

    # The agent's folder is the eventual subprocess cwd for in-process
    # SDK adapters (Amp, Claude). asyncio.create_subprocess_exec raises
    # FileNotFoundError when cwd doesn't exist — which the SDK then
    # reports as a CLI-not-found error, masking the real issue.
    # mkdir(parents=True, exist_ok=True) is idempotent for the common
    # case (folder already exists, often a user repo) and creates the
    # tree on demand for paths the user spelled out without first
    # making the directory. OSError → 422 with the OS message.
    try:
        req.folder.mkdir(parents=True, exist_ok=True)
    except OSError as exc:
        raise AgentFolderMissing(
            f"cannot use agent folder {req.folder}: {exc}"
        ) from exc

    # Build the provider config first — it validates model + options
    # and we want to fail fast on bad input before we allocate an agent
    # row + worktree we'd have to roll back.
    common_for_validation = CommonAgentConfig(
        workdir=req.folder,
        system_prompt=render_system_prompt(req.persona, req.role),
    )
    try:
        SPECS[req.provider].build(common_for_validation, req.model, req.options)
    except ValueError as exc:
        raise InvalidProviderConfig(str(exc)) from exc

    # Pre-fetch connection-backed contexts BEFORE allocating the agent
    # row. ConnectionStore raises ContextFetchError on any failure
    # (missing connection, missing token, fetcher error). We let it
    # propagate — the route maps it to 422. Halting here means a fetch
    # failure leaves no agent row, no worktree, no context dir to clean
    # up: the user retries cleanly after fixing the connection.
    fetched_bodies: dict[int, str] = {
        idx: connection_store.fetch_context_body(c)
        for idx, c in enumerate(req.contexts)
        if c.type in _CONNECTION_BACKED_TYPES
    }

    try:
        agent = workstore.add_agent_to_work(
            AddAgentRequest(
                work_slug=req.work_slug,
                name=req.name,
                persona=req.persona,
                role=req.role,
                provider=req.provider,
                model=req.model,
                folder=req.folder,
                contexts=req.contexts,
                # ``req.options`` was already validated by the Spec at
                # the top of this command; we persist exactly what the
                # user picked so resume + detach pick it back up.
                options=dict(req.options),
            )
        )
    except ValueError as exc:
        # workstore raises ValueError for missing-work; we already
        # checked above so this is a deeper-state issue worth
        # surfacing as 404 too.
        raise WorkNotFound(str(exc)) from exc

    if agent.slug is None:
        raise RuntimeError("workstore returned agent without slug")

    index_path = workstore.render_agent_contexts(
        req.work_slug, agent.slug, list(req.contexts), fetched_bodies
    )
    first_message = (
        f"Context for this task is at `{index_path}`. "
        "Read individual files as needed."
        if index_path
        else None
    )

    # Past this point the agent row + workspace dir exist. If anything
    # fails (worktree provisioning, supervisor registration), we roll
    # back the whole creation so the user retries cleanly instead of
    # being stuck with a zombie agent that can't be re-attached.
    try:
        if req.fork_from_agent is not None:
            # Inherit the source agent's working state into a fresh,
            # independent worktree (detached HEAD; no auto-branch).
            workdir = worktree_manager.ensure_forked(
                work_slug=req.work_slug,
                new_agent_slug=agent.slug,
                source_agent_slug=req.fork_from_agent,
                source=req.folder,
            )
        else:
            workdir = worktree_manager.ensure(
                work_slug=req.work_slug,
                agent_slug=agent.slug,
                source=req.folder,
                base_ref=FRESH_AGENT_BASE_REF,
                branch_name=req.branch_name,
            )

        # Mount project-scoped shared folders into this worktree (if any).
        # Refuse + warn on conflict — the share's symlink is skipped for
        # this agent's worktree but other agents/shares continue to mount.
        # See ``_bmad-output/stories/STORY-032.md`` § "Design notes".
        mounted_shares = _mount_project_shares(
            sharestore=sharestore,
            provisioner=share_provisioner,
            project_slug=record.work.project_slug,
            work_slug=req.work_slug,
            agent_slug=agent.slug,
        )
        mounted_chat_contexts = _mount_work_chat_contexts(
            workdir=workdir,
            folders=record.chat_context_folders,
        )
        mounted_shares = _merge_mounted_shares(
            mounted_chat_contexts,
            mounted_shares,
        )

        common = CommonAgentConfig(
            workdir=workdir,
            writable_roots=_agent_writable_roots(
                mounted_shares, worktree_manager, workdir
            ),
            system_prompt=render_system_prompt(
                req.persona,
                req.role,
                workdir=workdir,
                shares=mounted_shares.summaries,
                is_detached_worktree=worktree_manager.is_detached(workdir),
                shared_envs=detect_shared_envs(workdir),
            ),
        )
        config = SPECS[req.provider].build(common, req.model, req.options)
        adapter = build_adapter(config, settings)
        context = AgentStartContext(
            workdir=common.workdir,
            model=req.model,
            system_prompt=common.system_prompt,
            session_id=agent.session_id,
        )
        await supervisor.register_agent(req.work_slug, agent.slug, adapter, context)
        if first_message is not None:
            await supervisor.send_input(agent.slug, first_message)
    except WorktreeProvisionFailed:
        # Surfaceable upstream — keep stderr by re-raising. Roll back
        # first so the user can immediately retry without a zombie row.
        _rollback_agent(workstore, worktree_manager, req.work_slug, agent.slug)
        raise
    except Exception:
        # Any other failure post-DB-insert (adapter build error, supervisor
        # registration crash, send_input error). Roll back too — the
        # alternative is a half-provisioned agent that can't be reused.
        _rollback_agent(workstore, worktree_manager, req.work_slug, agent.slug)
        raise
    return agent


def _rollback_agent(
    workstore: WorkStore,
    worktree_manager: WorktreeManager,
    work_slug: str,
    agent_slug: str,
) -> None:
    """Best-effort cleanup of an agent that failed to provision. Both
    calls are idempotent — safe to invoke even when the worktree never
    materialised or the agent dir was never written. Failures here are
    logged but never re-raised: the caller's original exception is the
    one the user needs to see."""
    try:
        worktree_manager.remove(work_slug, agent_slug)
    except Exception:
        _log.warning("rollback: worktree.remove failed for %s/%s", work_slug, agent_slug)
    try:
        workstore.delete_agent(agent_slug)
    except Exception:
        _log.warning("rollback: workstore.delete_agent failed for %s", agent_slug)


def _mount_project_shares(
    *,
    sharestore: SharedFolderStore,
    provisioner: ShareProvisioner,
    project_slug: str | None,
    work_slug: str,
    agent_slug: str,
) -> MountedProjectShares:
    """Mount each project share into the agent's worktree as a symlink.

    Idempotent — re-mounting an existing correctly-targeted symlink is
    a no-op. Mount conflicts (existing dir/file at the path) are
    logged and skipped; the share is omitted from the returned summary
    and writable roots so the agent's system prompt and sandbox config
    don't promise something the filesystem doesn't deliver.
    """
    if project_slug is None:
        return MountedProjectShares()
    summaries: list[ShareSummary] = []
    writable_roots: list[Path] = []
    for share in sharestore.list_for_project(project_slug):
        if share.slug is None:
            continue
        target = provisioner.share_canonical_path(project_slug, share.slug)
        try:
            provisioner.mount_in_worktree(
                work_slug, agent_slug, share.mount_path, target
            )
        except MountConflict as exc:
            _log.warning(
                "share %s not mounted for %s/%s: %s",
                share.slug,
                work_slug,
                agent_slug,
                exc,
            )
            continue
        summaries.append(
            ShareSummary(name=share.name, mount_path=share.mount_path)
        )
        writable_roots.append(target.resolve(strict=False))
    return MountedProjectShares(
        summaries=tuple(summaries),
        writable_roots=tuple(dict.fromkeys(writable_roots)),
    )


def _agent_writable_roots(
    mounted_shares: MountedProjectShares,
    worktree_manager: WorktreeManager,
    workdir: Path,
) -> tuple[Path, ...]:
    return tuple(
        dict.fromkeys(
            (
                *mounted_shares.writable_roots,
                *worktree_manager.sandbox_writable_roots(workdir),
            )
        )
    )


def _mount_work_chat_contexts(
    *,
    workdir: Path,
    folders: Sequence[object],
) -> MountedProjectShares:
    summaries: list[ShareSummary] = []
    writable_roots: list[Path] = []
    for folder in folders:
        name = getattr(folder, "name", None)
        mount_path = getattr(folder, "mount_path", None)
        target = getattr(folder, "absolute_path", None)
        if not isinstance(name, str) or not isinstance(mount_path, str):
            continue
        if not isinstance(target, Path):
            continue
        link_path = workdir / mount_path
        try:
            link_path.parent.mkdir(parents=True, exist_ok=True)
            if link_path.is_symlink():
                if link_path.resolve(strict=False) == target.resolve(strict=False):
                    pass
                else:
                    link_path.unlink()
                    link_path.symlink_to(target, target_is_directory=True)
            elif link_path.exists():
                _log.warning(
                    "chat context %s not mounted at %s: path already exists",
                    name,
                    link_path,
                )
                continue
            else:
                link_path.symlink_to(target, target_is_directory=True)
        except OSError as exc:
            _log.warning(
                "chat context %s not mounted at %s: %s",
                name,
                link_path,
                exc,
            )
            continue
        summaries.append(ShareSummary(name=name, mount_path=mount_path))
        writable_roots.append(target.resolve(strict=False))
    return MountedProjectShares(
        summaries=tuple(summaries),
        writable_roots=tuple(dict.fromkeys(writable_roots)),
    )


def _merge_mounted_shares(
    *groups: MountedProjectShares,
) -> MountedProjectShares:
    summaries: list[ShareSummary] = []
    writable_roots: list[Path] = []
    for group in groups:
        summaries.extend(group.summaries)
        writable_roots.extend(group.writable_roots)
    return MountedProjectShares(
        summaries=tuple(summaries),
        writable_roots=tuple(dict.fromkeys(writable_roots)),
    )


__all__ = [
    "AgentFolderMissing",
    "InvalidProviderConfig",
    "MountedProjectShares",
    "StartAgentRequest",
    "WorkNotFound",
    "execute",
]
