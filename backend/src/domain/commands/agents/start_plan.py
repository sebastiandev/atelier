"""Build a plan to launch an agent.

Pulled out of the HTTP route per the thin-router rule. This command does
not start the agent — it returns a ``StartAgentPlan`` the route then
hands to ``supervisor.start_agent`` over the (forced) async boundary.
The split keeps the planning side sync + unit-testable; only the
supervisor call is async.

The command:

  1. Adds the agent row to its work (allocates the slug).
  2. Provisions a per-agent workdir via the WorktreeManager — a real
     ``git worktree`` checkout when the work's folder is a repo, the
     folder itself when it isn't.
  3. Builds the provider config + adapter via the SPECS registry.
  4. Returns ``(agent, adapter, context, first_message)``.
"""

from __future__ import annotations

from dataclasses import dataclass

from src.domain.agents import (
    SPECS,
    AgentAdapter,
    AgentStartContext,
    CommonAgentConfig,
    render_system_prompt,
)
from src.domain.models import Agent, Context, Persona, Provider
from src.domain.worktrees import WorktreeManager
from src.domain.workstore.dtos import AddAgentRequest
from src.domain.workstore.ports import WorkStore
from src.infrastructure.agents import build_adapter
from src.settings import Settings


@dataclass(frozen=True)
class StartAgentRequest:
    work_slug: str
    name: str
    persona: Persona
    role: str
    provider: Provider
    model: str
    options: dict[str, object]
    contexts: tuple[Context, ...] = ()


@dataclass(frozen=True)
class StartAgentPlan:
    agent: Agent
    adapter: AgentAdapter
    context: AgentStartContext
    # Synthesised "Context for this task is at <abs>/context.md…" message
    # to inject as the agent's first user input. ``None`` when the agent
    # was created without contexts. Resume never sets this — the SDK
    # session already includes the original first message.
    first_message: str | None = None


class WorkNotFound(ValueError):
    """The work_slug doesn't exist."""


class InvalidProviderConfig(ValueError):
    """The provider's Spec.build rejected the supplied model/options.
    The route maps this to 422 — it's a client mistake, not a missing
    resource."""


class WorkFolderMissing(ValueError):
    """The work's folder doesn't resolve to an existing directory on
    disk. Adapters spawn their underlying process in this directory; if
    it's missing, the spawn surfaces as a cryptic ENOENT from the SDK.
    The route maps this to 422 so the user can fix the path."""


def execute(
    workstore: WorkStore,
    worktree_manager: WorktreeManager,
    settings: Settings,
    req: StartAgentRequest,
) -> StartAgentPlan:
    record = workstore.get_work(req.work_slug)
    if record is None:
        raise WorkNotFound(f"work not found: {req.work_slug}")

    # The work's folder is the eventual subprocess cwd for in-process
    # SDK adapters (Amp, Claude). asyncio.create_subprocess_exec raises
    # FileNotFoundError when cwd doesn't exist — which the SDK then
    # reports as a CLI-not-found error, masking the real issue.
    # mkdir(parents=True, exist_ok=True) is idempotent for the common
    # case (folder already exists, often a user repo) and creates the
    # tree on demand for new works the user spelled out without first
    # making the directory. OSError → 422 with the OS message.
    try:
        record.work.folder.mkdir(parents=True, exist_ok=True)
    except OSError as exc:
        raise WorkFolderMissing(
            f"cannot use work folder {record.work.folder}: {exc}"
        ) from exc

    # Build the provider config first — it validates model + options
    # and we want to fail fast on bad input before we allocate an agent
    # row + worktree we'd have to roll back.
    common_for_validation = CommonAgentConfig(
        workdir=record.work.folder,
        system_prompt=render_system_prompt(req.persona, req.role),
    )
    try:
        SPECS[req.provider].build(common_for_validation, req.model, req.options)
    except ValueError as exc:
        raise InvalidProviderConfig(str(exc)) from exc

    try:
        agent = workstore.add_agent_to_work(
            AddAgentRequest(
                work_slug=req.work_slug,
                name=req.name,
                persona=req.persona,
                role=req.role,
                provider=req.provider,
                model=req.model,
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
        req.work_slug, agent.slug, list(req.contexts)
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
        source=record.work.folder,
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
    return StartAgentPlan(
        agent=agent, adapter=adapter, context=context, first_message=first_message
    )
