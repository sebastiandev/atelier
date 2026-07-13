"""Start a new execution run for a planning artifact."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING, cast

from src.domain.agents.configs import CommonAgentConfig
from src.domain.agents.launch import (
    AgentFolderMissing,
    AgentLaunchRequest,
    InvalidProviderConfig,
    launch_agent,
)
from src.domain.agents.ports import AgentAdapterFactory
from src.domain.agents.specs import SPECS
from src.domain.commands.planning import _loop_persistence, _loop_runtime
from src.domain.connections import ConnectionStore
from src.domain.loop.agent_policy import apply_stage_agent_policy, resolve_stage_model
from src.domain.loop.builtins import builtin_loop_definition
from src.domain.loop.catalog import LoopDefinitionRoots, locate_definition
from src.domain.loop.definitions import (
    LoopDefinitionConflict,
    LoopDefinitionInvalid,
    LoopDefinitionNotFound,
)
from src.domain.loop.dtos import (
    LoopAgentPolicy,
    LoopContextResolution,
    LoopContextResolutionRequest,
    LoopDefinition,
    LoopPermission,
    LoopSessionPolicy,
    LoopStepKind,
)
from src.domain.loop.ports import (
    LoopContextResolver,
    LoopDefinitionLocations,
    LoopDefinitionRepository,
    LoopRunRepository,
)
from src.domain.loop.prompts import (
    ReviewStagePrompt,
    TaskStagePrompt,
    build_stage_prompt,
)
from src.domain.models import Provider
from src.domain.planning import actions
from src.domain.planning.dtos import PlanArtifactDetail, PlanRunStatus
from src.domain.planning.loop import initial_run_prompt
from src.domain.planning.paths import atelier_planning_rel_path
from src.domain.planning.ports import PlanningFiles, PlanningSessionRepository
from src.domain.planning.service import (
    PlanArtifactNotExecutable,
    PlanArtifactNotFound,
    PlanningNotStarted,
)
from src.domain.sharedfolders.ports import SharedFolderStore, ShareProvisioner
from src.domain.workstore.ports import WorkStore
from src.domain.worktrees import WorktreeManager
from src.settings import Settings

if TYPE_CHECKING:
    from src.domain.supervisor import AgentSupervisorService


class WorkNotFound(ValueError):
    """The work_slug doesn't exist."""


class AgentNotFound(ValueError):
    """The agent_slug doesn't belong to the work."""


class LoopContextMissing(ValueError):
    """One or more required loop context references could not be resolved."""


_StageAgentConfig = tuple[Provider, str, dict[str, object]]


@dataclass(frozen=True)
class StartArtifactRunRequest:
    """Command input for starting one artifact run."""

    work_slug: str
    artifact_id: str
    agent_slug: str | None = None
    loop_definition_id: str | None = None
    loop_revision: str | None = None


async def execute(
    workstore: WorkStore,
    files: PlanningFiles,
    planning_sessions: PlanningSessionRepository,
    loop_definitions: LoopDefinitionRepository,
    loop_locations: LoopDefinitionLocations,
    loop_runs: LoopRunRepository,
    context_resolver: LoopContextResolver,
    supervisor: AgentSupervisorService,
    worktree_manager: WorktreeManager,
    connection_store: ConnectionStore,
    sharestore: SharedFolderStore,
    share_provisioner: ShareProvisioner,
    adapter_factory: AgentAdapterFactory,
    settings: Settings,
    req: StartArtifactRunRequest,
) -> PlanArtifactDetail:
    """Start a fresh run and send the initial loop prompt.

    Preconditions: Work exists, artifact is executable, and agent belongs to
    the work.
    Postconditions: manifest stores a new running run with a durable run id,
    and the linked agent has received the run prompt.
    """
    work = workstore.get_work(req.work_slug)
    if work is None:
        raise WorkNotFound(f"work not found: {req.work_slug}")
    manifest = actions.manifest_or_raise(files, req.work_slug)
    detail = actions.detail_or_raise(files, req.work_slug, req.artifact_id)
    actions.require_executable(detail.artifact)
    definition = _resolve_definition(
        files,
        loop_definitions,
        loop_locations,
        req,
    )
    if req.agent_slug is None:
        session = planning_sessions.get_by_work_slug(req.work_slug)
        if session is None:
            raise PlanningNotStarted(f"planning session not found: {req.work_slug}")
        parent_provider = session.provider
        parent_model = session.model
        parent_options = dict(session.options or {})
        parent_folder = Path(session.root_path).expanduser()
    else:
        parent_agent = next(
            (
                agent
                for agent in workstore.list_agents_for_work(req.work_slug)
                if agent.slug == req.agent_slug
            ),
            None,
        )
        if parent_agent is None:
            raise AgentNotFound(f"agent not found on work: {req.agent_slug}")
        parent_provider = parent_agent.provider
        parent_model = parent_agent.model
        parent_options = dict(parent_agent.options or {})
        parent_folder = parent_agent.folder
    reuse_initial_agent = req.agent_slug is not None and (
        req.loop_definition_id is None
        or _can_reuse_initial_agent(
            definition,
            parent_provider=parent_provider,
            parent_model=parent_model,
            parent_options=parent_options,
            parent_folder=parent_folder,
        )
    )
    _validate_stage_agent_policies(
        definition,
        parent_provider=parent_provider,
        parent_model=parent_model,
        parent_options=parent_options,
        parent_folder=parent_folder,
        reuse_initial_agent=reuse_initial_agent,
    )
    resolutions = _resolve_contexts(
        files,
        context_resolver,
        req.work_slug,
        detail,
        definition,
        tuple(
            value
            for context in work.contexts
            for value in (context.value, context.conn_id)
            if value
        ),
    )
    missing = [
        f"{stage_id}: {item}"
        for stage_id, resolution in resolutions.items()
        for item in resolution.missing_required
    ]
    if missing:
        raise LoopContextMissing("Required loop context is missing: " + "; ".join(missing))
    agent_slug = req.agent_slug
    if not reuse_initial_agent:
        agent_slug = await _launch_initial_agent(
            workstore,
            supervisor,
            worktree_manager,
            connection_store,
            sharestore,
            share_provisioner,
            adapter_factory,
            req,
            definition,
            parent_provider=parent_provider,
            parent_model=parent_model,
            parent_options=parent_options,
            parent_folder=parent_folder,
            fork_from_agent=req.agent_slug,
        )
    assert agent_slug is not None
    runs = actions.artifact_runs_for_update(manifest, req.artifact_id)
    run_id = actions.next_run_id(runs)
    cursor = _loop_runtime.last_transcript_seq(
        workstore,
        work_slug=req.work_slug,
        agent_slug=agent_slug,
    )

    try:
        await _loop_runtime.send_loop_prompt(
            workstore,
            supervisor,
            worktree_manager,
            sharestore,
            share_provisioner,
            settings,
            work_slug=req.work_slug,
            agent_slug=agent_slug,
            prompt=(
                initial_run_prompt(detail.artifact)
                if req.loop_definition_id is None
                else _initial_stage_prompt(
                    req,
                    run_id,
                    definition,
                    detail,
                    resolutions[definition.stages[0].step_id],
                )
            ),
        )
    except _loop_runtime.AgentNotFound as exc:
        raise AgentNotFound(str(exc)) from exc

    now = actions.now_iso()
    loop = actions.initialized_loop_snapshot(
        artifact_id=req.artifact_id,
        agent_slug=agent_slug,
        run_id=run_id,
        definition=definition,
    )
    loop["legacy"] = req.loop_definition_id is None
    loop["last_checked_seq"] = cursor
    for stage_row in loop["stages"]:
        if not isinstance(stage_row, dict):
            continue
        resolution = resolutions.get(actions.str_or_empty(stage_row.get("id")))
        if resolution is not None:
            stage_row["resolved_context"] = list(resolution.entries)
            stage_row["context_warnings"] = list(resolution.warnings)
    runs.append(
        {
            "id": run_id,
            "agent_slug": agent_slug,
            "status": PlanRunStatus.RUNNING.value,
            "started_at": now,
            "completed_at": None,
            "loop": loop,
        }
    )
    manifest["updated_at"] = now
    files.write_manifest(req.work_slug, manifest)
    _loop_persistence.persist_artifact_run(
        loop_runs,
        work_slug=req.work_slug,
        artifact=detail.artifact,
        run=runs[-1],
    )
    return actions.detail_or_raise(files, req.work_slug, req.artifact_id)


def _resolve_definition(
    files: PlanningFiles,
    repository: LoopDefinitionRepository,
    locations: LoopDefinitionLocations,
    req: StartArtifactRunRequest,
) -> LoopDefinition:
    """Resolve and revision-check the loop selected for a run."""
    definition_id = req.loop_definition_id
    if definition_id is None:
        definition = builtin_loop_definition("atelier-fast")
        if definition is None:
            raise LoopDefinitionNotFound("loop definition not found: atelier-fast")
    else:
        definition = locate_definition(
            repository,
            LoopDefinitionRoots(
                library=locations.loop_library_root(),
                work=locations.work_loop_root(req.work_slug),
                legacy=files.working_root(req.work_slug),
            ),
            definition_id,
        ).definition
    if not definition.valid:
        raise LoopDefinitionInvalid(" ".join(definition.errors))
    if req.loop_revision is not None and req.loop_revision != definition.revision:
        raise LoopDefinitionConflict(
            f"loop definition changed: {definition.definition_id}"
        )
    return definition


def _resolve_stage_agent_config(
    policy: LoopAgentPolicy,
    *,
    parent_provider: Provider,
    parent_model: str,
    parent_options: dict[str, object],
) -> tuple[Provider, str, dict[str, object]]:
    """Resolve one stage's provider config without changing runtime state.

    Preconditions: the parent config is valid for its registered provider.
    Postconditions: returns the stage's effective provider, model, and options.
    """
    provider = cast(Provider, policy.provider or parent_provider)
    model = resolve_stage_model(provider, parent_provider, parent_model, policy)
    options = apply_stage_agent_policy(
        provider,
        parent_options,
        policy,
        parent_provider=parent_provider,
        parent_model=parent_model,
    )
    return provider, model, options


def _can_reuse_initial_agent(
    definition: LoopDefinition,
    *,
    parent_provider: Provider,
    parent_model: str,
    parent_options: dict[str, object],
    parent_folder: Path,
) -> bool:
    """Return whether the supplied agent already matches the first stage.

    Preconditions: the supplied parent agent belongs to the target Work.
    Postconditions: invalid provider settings fail before launch; no state changes.
    """
    stage = definition.stages[0]
    if stage.agent is None:
        raise LoopDefinitionInvalid("The first loop stage needs an agent policy.")
    if stage.agent.session != LoopSessionPolicy.REUSE:
        return False
    common = CommonAgentConfig(workdir=parent_folder, system_prompt="")
    try:
        provider, model, options = _resolve_stage_agent_config(
            stage.agent,
            parent_provider=parent_provider,
            parent_model=parent_model,
            parent_options=parent_options,
        )
        current = SPECS[parent_provider].build(common, parent_model, parent_options)
        desired = SPECS[provider].build(common, model, options)
    except (KeyError, ValueError) as exc:
        raise InvalidProviderConfig(f"{stage.name}: {exc}") from exc
    return current == desired


def _validate_stage_agent_policies(
    definition: LoopDefinition,
    *,
    parent_provider: Provider,
    parent_model: str,
    parent_options: dict[str, object],
    parent_folder: Path,
    reuse_initial_agent: bool,
) -> None:
    """Preflight every agent stage against its effective parent config.

    Preconditions: the parent config belongs to this Work's initial agent.
    Postconditions: every reachable agent config can be built, or validation
    fails before any loop agent or run is persisted.
    """
    common = CommonAgentConfig(workdir=parent_folder, system_prompt="")
    stages = {stage.step_id: stage for stage in definition.stages}
    first_id = definition.stages[0].step_id
    launched: dict[str, _StageAgentConfig] = {}
    if reuse_initial_agent:
        launched[first_id] = (
            parent_provider,
            parent_model,
            dict(parent_options),
        )
    pending = [
        (
            first_id,
            parent_provider,
            parent_model,
            dict(parent_options),
            True,
            launched,
        )
    ]
    seen: set[tuple[str, Provider, str, str, bool, str]] = set()
    while pending:
        (
            stage_id,
            source_provider,
            source_model,
            source_options,
            initial,
            launched,
        ) = pending.pop()
        state = (
            stage_id,
            source_provider,
            source_model,
            repr(sorted(source_options.items())),
            initial,
            repr(
                sorted(
                    (
                        launched_id,
                        config[0],
                        config[1],
                        repr(sorted(config[2].items())),
                    )
                    for launched_id, config in launched.items()
                )
            ),
        )
        if state in seen:
            continue
        seen.add(state)
        stage = stages[stage_id]
        provider = source_provider
        model = source_model
        options = source_options
        next_launched = launched
        reused = (
            stage.agent is not None
            and stage.agent.session == LoopSessionPolicy.REUSE
            and stage_id in launched
        )
        if reused:
            provider, model, options = launched[stage_id]
        elif stage.agent is not None:
            try:
                provider, model, options = _resolve_stage_agent_config(
                    stage.agent,
                    parent_provider=source_provider,
                    parent_model=source_model,
                    parent_options=source_options,
                )
                SPECS[provider].build(common, model, options)
            except (KeyError, ValueError) as exc:
                raise InvalidProviderConfig(f"{stage.name}: {exc}") from exc
            if stage.agent.session == LoopSessionPolicy.REUSE:
                next_launched = {
                    **launched,
                    stage_id: (provider, model, options),
                }
        if not initial and (
            stage.kind != LoopStepKind.AGENT_TASK
            or stage.agent is None
            or stage.agent.permissions == LoopPermission.READ
        ):
            provider = source_provider
            model = source_model
            options = source_options
        for destination in stage.transitions.values():
            if destination in stages:
                pending.append(
                    (
                        destination,
                        provider,
                        model,
                        options,
                        False,
                        next_launched,
                    )
                )


async def _launch_initial_agent(
    workstore: WorkStore,
    supervisor: AgentSupervisorService,
    worktree_manager: WorktreeManager,
    connection_store: ConnectionStore,
    sharestore: SharedFolderStore,
    share_provisioner: ShareProvisioner,
    adapter_factory: AgentAdapterFactory,
    req: StartArtifactRunRequest,
    definition: LoopDefinition,
    *,
    parent_provider: Provider,
    parent_model: str,
    parent_options: dict[str, object],
    parent_folder: Path,
    fork_from_agent: str | None,
) -> str:
    """Launch the first stage from persisted Planning runtime settings."""
    stage = definition.stages[0]
    if stage.agent is None:
        raise LoopDefinitionInvalid("The first loop stage needs an agent policy.")
    provider, model, options = _resolve_stage_agent_config(
        stage.agent,
        parent_provider=parent_provider,
        parent_model=parent_model,
        parent_options=parent_options,
    )
    agent = await launch_agent(
        workstore,
        supervisor,
        worktree_manager,
        connection_store,
        sharestore,
        share_provisioner,
        adapter_factory,
        AgentLaunchRequest(
            work_slug=req.work_slug,
            name=f"{stage.name} · {req.artifact_id}",
            persona=(
                "architect"
                if stage.kind == LoopStepKind.AGENT_REVIEW
                else "developer"
            ),
            role=stage.instructions,
            provider=provider,
            model=model,
            folder=parent_folder,
            options=options,
            fork_from_agent=fork_from_agent,
        ),
    )
    if agent.slug is None:
        raise RuntimeError("loop launch returned an agent without slug")
    return agent.slug


def _initial_stage_prompt(
    req: StartArtifactRunRequest,
    run_id: str,
    definition: LoopDefinition,
    detail: PlanArtifactDetail,
    resolution: LoopContextResolution,
) -> str:
    """Render the first provider-facing prompt from the selected definition."""
    stage = definition.stages[0]
    prompt_type: type[TaskStagePrompt] | type[ReviewStagePrompt]
    if stage.kind == LoopStepKind.AGENT_TASK:
        prompt_type = TaskStagePrompt
    elif stage.kind == LoopStepKind.AGENT_REVIEW:
        prompt_type = ReviewStagePrompt
    else:
        raise LoopDefinitionInvalid(
            "Planning runs must start with an agent task or review stage."
        )
    return build_stage_prompt(
        prompt_type(
            run_id=run_id,
            work_slug=req.work_slug,
            artifact_id=detail.artifact.id,
            artifact_title=detail.artifact.title,
            source_ref=detail.artifact.source_ref,
            stage=stage,
            resolved_context=resolution.entries,
            context_warnings=resolution.warnings,
        )
    )


def _resolve_contexts(
    files: PlanningFiles,
    resolver: LoopContextResolver,
    work_slug: str,
    detail: PlanArtifactDetail,
    definition: LoopDefinition,
    shared_refs: tuple[str, ...],
) -> dict[str, LoopContextResolution]:
    """Resolve every snapshotted stage context without reading file bodies."""
    raw_root = files.working_root(work_slug)
    if raw_root is None:
        raise PlanningNotStarted(f"planning root not found: {work_slug}")
    root = Path(raw_root).expanduser().resolve()
    plan_index = root / atelier_planning_rel_path(work_slug) / "manifest.json"
    return {
        stage.step_id: resolver.resolve(
            LoopContextResolutionRequest(
                root_path=root,
                work_slug=work_slug,
                target_ref=detail.artifact.source_ref,
                plan_index_ref=str(plan_index),
                dependencies=tuple(detail.artifact.dependencies),
                shared_context_refs=shared_refs,
                references=stage.context,
            )
        )
        for stage in definition.stages
    }


__all__ = [
    "AgentFolderMissing",
    "AgentNotFound",
    "InvalidProviderConfig",
    "LoopContextMissing",
    "LoopDefinitionConflict",
    "LoopDefinitionInvalid",
    "LoopDefinitionNotFound",
    "PlanArtifactNotExecutable",
    "PlanArtifactNotFound",
    "PlanningNotStarted",
    "StartArtifactRunRequest",
    "WorkNotFound",
    "execute",
]
