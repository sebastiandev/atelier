"""Reusable agent creation and runtime launch action."""

from __future__ import annotations

import logging
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING

from src.domain.agents.configs import CommonAgentConfig
from src.domain.agents.mounts import (
    MountedProjectShares,
    agent_writable_roots,
    merge_mounted_shares,
    mount_project_shares,
    mount_work_chat_contexts,
)
from src.domain.agents.ports import AgentAdapterFactory, AgentStartContext
from src.domain.agents.specs import SPECS
from src.domain.agents.system_prompt import detect_shared_envs, render_system_prompt
from src.domain.connections import ConnectionStore
from src.domain.models import Agent, Context, Persona, Provider
from src.domain.sharedfolders.ports import SharedFolderStore, ShareProvisioner
from src.domain.workstore.dtos import AddAgentRequest
from src.domain.workstore.ports import WorkStore
from src.domain.worktrees import WorktreeManager, WorktreeProvisionFailed

if TYPE_CHECKING:
    from src.domain.supervisor import AgentSupervisorService

_log = logging.getLogger(__name__)

_CONNECTION_BACKED_TYPES = frozenset({"jira", "sentry", "honeycomb"})
FRESH_AGENT_BASE_REF = "HEAD"


@dataclass(frozen=True)
class AgentLaunchRequest:
    """Inputs shared by user-driven and loop-driven agent launches."""

    work_slug: str
    name: str
    persona: Persona
    role: str
    provider: Provider
    model: str
    folder: Path
    options: dict[str, object]
    contexts: tuple[Context, ...] = ()
    fork_from_agent: str | None = None
    branch_name: str | None = None
    worktree_slug: str | None = None
    approved_command_prefixes: tuple[str, ...] = ()
    artifact_id: str | None = None


class WorkNotFound(ValueError):
    """The target Work does not exist."""


class WorkNotActive(ValueError):
    """The target Work is completed and must be reopened before mutation."""


class InvalidProviderConfig(ValueError):
    """The provider rejected the selected model or options."""


class AgentFolderMissing(ValueError):
    """The selected source folder cannot be created or used."""


async def launch_agent(
    workstore: WorkStore,
    supervisor: AgentSupervisorService,
    worktree_manager: WorktreeManager,
    connection_store: ConnectionStore,
    sharestore: SharedFolderStore,
    share_provisioner: ShareProvisioner,
    adapter_factory: AgentAdapterFactory,
    req: AgentLaunchRequest,
) -> Agent:
    """Persist, provision, and register one agent atomically.

    Preconditions: the Work exists and provider inputs are valid.
    Postconditions: a live agent and worktree exist, or all allocated state is
    rolled back when provisioning fails.
    """
    record = workstore.get_work(req.work_slug)
    if record is None:
        raise WorkNotFound(f"work not found: {req.work_slug}")
    if record.work.status != "active":
        raise WorkNotActive(
            f"work {req.work_slug} is completed; reopen it before starting an agent"
        )

    try:
        req.folder.mkdir(parents=True, exist_ok=True)
    except OSError as exc:
        raise AgentFolderMissing(f"cannot use agent folder {req.folder}: {exc}") from exc

    validation_common = CommonAgentConfig(
        workdir=req.folder,
        system_prompt=render_system_prompt(req.persona, req.role),
        approved_command_prefixes=req.approved_command_prefixes,
    )
    try:
        SPECS[req.provider].build(validation_common, req.model, req.options)
    except ValueError as exc:
        raise InvalidProviderConfig(str(exc)) from exc

    fetched_bodies = {
        index: connection_store.fetch_context_body(context)
        for index, context in enumerate(req.contexts)
        if context.type in _CONNECTION_BACKED_TYPES
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
                options=dict(req.options),
                worktree_slug=req.worktree_slug,
                artifact_id=req.artifact_id,
            )
        )
    except ValueError as exc:
        raise WorkNotFound(str(exc)) from exc
    if agent.slug is None:
        raise RuntimeError("workstore returned agent without slug")

    index_path = workstore.render_agent_contexts(
        req.work_slug, agent.slug, list(req.contexts), fetched_bodies
    )
    first_message = (
        f"Context for this task is at `{index_path}`. Read individual files as needed."
        if index_path
        else None
    )

    try:
        worktree_slug = req.worktree_slug or agent.slug
        if req.fork_from_agent is not None:
            workdir = worktree_manager.ensure_forked(
                work_slug=req.work_slug,
                new_agent_slug=worktree_slug,
                source_agent_slug=req.fork_from_agent,
                source=req.folder,
            )
        else:
            workdir = worktree_manager.ensure(
                work_slug=req.work_slug,
                agent_slug=worktree_slug,
                source=req.folder,
                base_ref=FRESH_AGENT_BASE_REF,
                branch_name=req.branch_name,
            )

        mounted_shares = mount_project_shares(
            sharestore=sharestore,
            provisioner=share_provisioner,
            project_slug=record.work.project_slug,
            work_slug=req.work_slug,
            agent_slug=worktree_slug,
        )
        mounted_chat_contexts = mount_work_chat_contexts(
            workdir=workdir,
            folders=record.chat_context_folders,
        )
        mounted_shares = merge_mounted_shares(mounted_chat_contexts, mounted_shares)
        common = CommonAgentConfig(
            workdir=workdir,
            writable_roots=agent_writable_roots(
                mounted_shares, worktree_manager, workdir
            ),
            # The worktree is cut from req.folder, and a run's plan artifacts
            # live there rather than in the worktree, so every briefed run
            # reads outside its own workspace by design.
            readable_roots=(req.folder.resolve(strict=False),),
            approved_command_prefixes=req.approved_command_prefixes,
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
        adapter = adapter_factory.build(config)
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
        _rollback_agent(
            workstore, worktree_manager, req.work_slug, agent.slug, req.worktree_slug
        )
        raise
    except Exception:
        _rollback_agent(
            workstore, worktree_manager, req.work_slug, agent.slug, req.worktree_slug
        )
        raise
    return agent


def _rollback_agent(
    workstore: WorkStore,
    worktree_manager: WorktreeManager,
    work_slug: str,
    agent_slug: str,
    worktree_slug: str | None,
) -> None:
    """Remove partial agent state while preserving the original launch error."""
    if worktree_slug is None:
        try:
            worktree_manager.remove(work_slug, agent_slug)
        except Exception:
            _log.warning("rollback: worktree.remove failed for %s/%s", work_slug, agent_slug)
    try:
        workstore.delete_agent(agent_slug)
    except Exception:
        _log.warning("rollback: workstore.delete_agent failed for %s", agent_slug)


__all__ = [
    "FRESH_AGENT_BASE_REF",
    "AgentFolderMissing",
    "AgentLaunchRequest",
    "InvalidProviderConfig",
    "MountedProjectShares",
    "WorkNotActive",
    "WorkNotFound",
    "launch_agent",
]
