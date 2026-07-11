"""Start a new execution run for a planning artifact."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING, cast

from src.domain.agents.launch import (
    AgentFolderMissing,
    AgentLaunchRequest,
    InvalidProviderConfig,
    launch_agent,
)
from src.domain.agents.ports import AgentAdapterFactory
from src.domain.commands.planning import _loop_persistence, _loop_runtime
from src.domain.connections import ConnectionStore
from src.domain.loop.agent_policy import apply_stage_agent_policy
from src.domain.loop.builtins import builtin_loop_definition
from src.domain.loop.definitions import (
    LoopDefinitionConflict,
    LoopDefinitionInvalid,
    LoopDefinitionNotFound,
)
from src.domain.loop.dtos import (
    LoopContextResolution,
    LoopContextResolutionRequest,
    LoopDefinition,
    LoopStepKind,
)
from src.domain.loop.ports import (
    LoopContextResolver,
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
    definition = _resolve_definition(files, loop_definitions, req)
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
    if agent_slug is None:
        agent_slug = await _launch_initial_agent(
            workstore,
            planning_sessions,
            supervisor,
            worktree_manager,
            connection_store,
            sharestore,
            share_provisioner,
            adapter_factory,
            req,
            definition,
        )
    elif workstore.get_work_slug_for_agent(agent_slug) != req.work_slug:
        raise AgentNotFound(f"agent not found on work: {agent_slug}")
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
    req: StartArtifactRunRequest,
) -> LoopDefinition:
    """Resolve and revision-check the loop selected for a run."""
    definition_id = req.loop_definition_id or "atelier-fast"
    definition = builtin_loop_definition(definition_id)
    if definition is None:
        root = files.working_root(req.work_slug)
        definition = repository.get_definition(root, definition_id) if root else None
    if definition is None:
        raise LoopDefinitionNotFound(f"loop definition not found: {definition_id}")
    if not definition.valid:
        raise LoopDefinitionInvalid(" ".join(definition.errors))
    if req.loop_revision is not None and req.loop_revision != definition.revision:
        raise LoopDefinitionConflict(
            f"loop definition changed: {definition_id}"
        )
    return definition


async def _launch_initial_agent(
    workstore: WorkStore,
    planning_sessions: PlanningSessionRepository,
    supervisor: AgentSupervisorService,
    worktree_manager: WorktreeManager,
    connection_store: ConnectionStore,
    sharestore: SharedFolderStore,
    share_provisioner: ShareProvisioner,
    adapter_factory: AgentAdapterFactory,
    req: StartArtifactRunRequest,
    definition: LoopDefinition,
) -> str:
    """Launch the first stage from persisted Planning runtime settings."""
    session = planning_sessions.get_by_work_slug(req.work_slug)
    if session is None:
        raise PlanningNotStarted(f"planning session not found: {req.work_slug}")
    stage = definition.stages[0]
    if stage.agent is None:
        raise LoopDefinitionInvalid("The first loop stage needs an agent policy.")
    provider = cast(Provider, stage.agent.provider or session.provider)
    options = apply_stage_agent_policy(
        provider,
        dict(session.options or {}) if provider == session.provider else {},
        stage.agent,
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
            model=stage.agent.model or session.model,
            folder=Path(session.root_path).expanduser(),
            options=options,
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
