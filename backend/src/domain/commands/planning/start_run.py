"""Start a new execution run for a planning artifact."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING

from src.domain.agents.launch import (
    AgentFolderMissing,
    AgentLaunchRequest,
    InvalidProviderConfig,
    WorkNotActive,
    launch_agent,
)
from src.domain.agents.ports import AgentAdapterFactory
from src.domain.connections import ConnectionStore
from src.domain.loop import actions as loop_actions
from src.domain.loop import briefs
from src.domain.loop import runtime as _loop_runtime
from src.domain.loop.agent_policy import (
    resolve_stage_agent_config,
    validate_stage_agent_policies,
)
from src.domain.loop.builtins import builtin_loop_definition
from src.domain.loop.catalog import LoopDefinitionRoots, locate_definition
from src.domain.loop.definitions import (
    LoopDefinitionConflict,
    LoopDefinitionInvalid,
    LoopDefinitionNotFound,
)
from src.domain.loop.dtos import (
    LoopBrief,
    LoopContextKind,
    LoopContextResolution,
    LoopContextResolutionRequest,
    LoopDefinition,
    LoopStageBrief,
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
from src.domain.planning.loop_persistence import (
    artifact_run_rows,
    persist_artifact_run,
)
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
    """A stage agent could not be resolved while launching the run."""


class LoopContextMissing(ValueError):
    """One or more required loop context references could not be resolved."""


@dataclass(frozen=True)
class StartArtifactRunRequest:
    """Command input for starting one artifact run."""

    work_slug: str
    artifact_id: str
    loop_definition_id: str | None = None
    loop_revision: str | None = None
    brief_note: str = ""


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
    if work.work.status != "active":
        raise WorkNotActive(f"work {req.work_slug} is completed; reopen it before starting a run")
    manifest = actions.manifest_or_raise(files, req.work_slug)
    detail = actions.detail_or_raise(files, loop_runs, req.work_slug, req.artifact_id)
    actions.require_executable(detail.artifact)
    definition = _resolve_definition(
        files,
        loop_definitions,
        loop_locations,
        req,
    )
    brief = _planning_brief(detail, definition, req.brief_note)
    session = planning_sessions.get_by_work_slug(req.work_slug)
    if session is None:
        raise PlanningNotStarted(f"planning session not found: {req.work_slug}")
    parent_provider = session.provider
    parent_model = session.model
    parent_options = dict(session.options or {})
    parent_folder = Path(session.root_path).expanduser()
    validate_stage_agent_policies(
        definition,
        parent_provider=parent_provider,
        parent_model=parent_model,
        parent_options=parent_options,
        parent_folder=parent_folder,
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
        brief,
        parent_provider=parent_provider,
        parent_model=parent_model,
        parent_options=parent_options,
        parent_folder=parent_folder,
    )
    run_id = actions.next_run_id(
        artifact_run_rows(loop_runs, req.work_slug, req.artifact_id)
    )
    cursor = _loop_runtime.last_transcript_seq(
        workstore,
        work_slug=req.work_slug,
        agent_slug=agent_slug,
    )

    prompt = _initial_stage_prompt(
        req,
        run_id,
        definition,
        detail,
        resolutions[definition.stages[0].step_id],
        brief,
        (
            _loop_runtime.workspace_prompt_context(
                workstore,
                worktree_manager,
                work_slug=req.work_slug,
                agent_slug=agent_slug,
            )
            if any(
                item.kind == LoopContextKind.WORKSPACE_DIFF for item in definition.stages[0].context
            )
            else ""
        ),
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
            prompt=prompt,
        )
    except _loop_runtime.AgentNotFound as exc:
        raise AgentNotFound(str(exc)) from exc

    now = actions.now_iso()
    loop = loop_actions.initialized_loop_snapshot(
        target_id=req.artifact_id,
        agent_slug=agent_slug,
        run_id=run_id,
        definition=definition,
    )
    loop["last_checked_seq"] = cursor
    for stage_row in loop["stages"]:
        if not isinstance(stage_row, dict):
            continue
        resolution = resolutions.get(actions.str_or_empty(stage_row.get("id")))
        if resolution is not None:
            stage_row["resolved_context"] = list(resolution.entries)
            stage_row["context_warnings"] = list(resolution.warnings)
    run: dict[str, object] = {
        "id": run_id,
        "agent_slug": agent_slug,
        "status": PlanRunStatus.RUNNING.value,
        "started_at": now,
        "completed_at": None,
        "loop": loop,
    }
    if brief is not None:
        run["brief"] = briefs.brief_snapshot(brief)
    persist_artifact_run(
        loop_runs,
        work_slug=req.work_slug,
        artifact=detail.artifact,
        run=run,
    )
    # The manifest keeps the id so the plan file still shows which stories
    # have been run; the run's state lives in SQL.
    actions.record_artifact_run_id(manifest, req.artifact_id, run_id)
    manifest["updated_at"] = now
    files.write_manifest(req.work_slug, manifest)
    return actions.detail_or_raise(files, loop_runs, req.work_slug, req.artifact_id)


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
        raise LoopDefinitionConflict(f"loop definition changed: {definition.definition_id}")
    return definition


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
    brief: LoopBrief | None,
    *,
    parent_provider: Provider,
    parent_model: str,
    parent_options: dict[str, object],
    parent_folder: Path,
) -> str:
    """Launch the first stage from persisted Planning runtime settings."""
    stage = definition.stages[0]
    if stage.agent is None:
        raise LoopDefinitionInvalid("The first loop stage needs an agent policy.")
    provider, model, options = resolve_stage_agent_config(
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
            persona=("architect" if stage.kind == LoopStepKind.AGENT_REVIEW else "developer"),
            role=stage.instructions,
            provider=provider,
            model=model,
            folder=parent_folder,
            options=options,
            worktree_slug=loop_actions.sourced_worktree_slug(req.artifact_id),
            approved_command_prefixes=briefs.resolved_approved_command_prefixes(
                definition,
                brief,
                stage,
            ),
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
    brief: LoopBrief | None,
    workspace_diff: str,
) -> str:
    """Render the first provider-facing prompt from the selected definition."""
    stage = definition.stages[0]
    prompt_type: type[TaskStagePrompt] | type[ReviewStagePrompt]
    if stage.kind == LoopStepKind.AGENT_TASK:
        prompt_type = TaskStagePrompt
    elif stage.kind == LoopStepKind.AGENT_REVIEW:
        prompt_type = ReviewStagePrompt
    else:
        raise LoopDefinitionInvalid("Planning runs must start with an agent task or review stage.")
    brief_note, brief_context = briefs.prompt_values(brief, stage.step_id)
    return build_stage_prompt(
        prompt_type(
            run_id=run_id,
            work_slug=req.work_slug,
            artifact_id=detail.artifact.id,
            artifact_title=detail.artifact.title,
            source_ref=detail.artifact.source_ref,
            stage=stage,
            workspace_diff=workspace_diff,
            resolved_context=resolution.entries,
            context_warnings=resolution.warnings,
            brief_note=brief_note,
            brief_context=brief_context,
        )
    )


def _planning_brief(
    detail: PlanArtifactDetail,
    definition: LoopDefinition,
    note: str,
) -> LoopBrief | None:
    """Pin one optional user note to every agent-backed Planning stage."""
    value = note.strip()
    if not value:
        return None
    return LoopBrief(
        goal=detail.artifact.title,
        stages=tuple(
            LoopStageBrief(stage_id=stage.step_id, note=value)
            for stage in definition.stages
            if stage.kind in {LoopStepKind.AGENT_TASK, LoopStepKind.AGENT_REVIEW}
        ),
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
    "WorkNotActive",
    "WorkNotFound",
    "execute",
]
