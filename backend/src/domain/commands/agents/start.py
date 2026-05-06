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

from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING

from src.domain.agents import (
    SPECS,
    AgentStartContext,
    CommonAgentConfig,
    render_system_prompt,
)
from src.domain.connections import ConnectionStore
from src.domain.models import Agent, Context, Persona, Provider
from src.domain.workstore.dtos import AddAgentRequest
from src.domain.workstore.ports import WorkStore
from src.domain.worktrees import WorktreeManager
from src.infrastructure.agents import build_adapter
from src.settings import Settings

if TYPE_CHECKING:
    from src.domain.supervisor import AgentSupervisorService

# Context types whose body must be fetched from an external connection
# at start time. Anything else is rendered inline by the renderer.
_CONNECTION_BACKED_TYPES = frozenset({"jira", "sentry", "honeycomb"})


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

    workdir = worktree_manager.ensure(
        work_slug=req.work_slug,
        agent_slug=agent.slug,
        source=req.folder,
    )

    common = CommonAgentConfig(
        workdir=workdir,
        system_prompt=render_system_prompt(req.persona, req.role),
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
    return agent


__all__ = [
    "AgentFolderMissing",
    "InvalidProviderConfig",
    "StartAgentRequest",
    "WorkNotFound",
    "execute",
]
