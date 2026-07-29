"""Start a new execution run for a planning artifact."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING, cast

from src.domain.agents.launch import (
    AgentFolderMissing,
    AgentLaunchRequest,
    InvalidProviderConfig,
    WorkNotActive,
    launch_agent,
)
from src.domain.agents.ports import AgentAdapterFactory
from src.domain.agents.specs import SPECS
from src.domain.connections import ConnectionStore
from src.domain.loop import actions as loop_actions
from src.domain.loop import briefs, followups
from src.domain.loop import runtime as _loop_runtime
from src.domain.loop.agent_policy import (
    StageAgentConfig,
    resolve_stage_agent_config,
    validate_stage_agent_policies,
)
from src.domain.loop.briefs import LoopBriefInvalid
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
    LoopRunKind,
    LoopStageBrief,
    LoopStepDefinition,
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


class StageAgentUnresolved(ValueError):
    """The entry stage has no provider to run under, and nothing to inherit."""


class LoopContextMissing(ValueError):
    """One or more required loop context references could not be resolved."""


@dataclass(frozen=True)
class StartArtifactRunRequest:
    """Command input for starting one artifact run."""

    work_slug: str
    artifact_id: str
    loop_definition_id: str | None = None
    loop_revision: str | None = None
    # A full brief from the setup screen: per-stage notes, context and agent
    # overrides, the same shape a goal-driven run pins. `brief_note` remains
    # the shorthand for "one note on every agent stage".
    brief: LoopBrief | None = None
    # False when Atelier synthesised the brief rather than the client sending
    # one, which is when the legacy required-slot shim applies -- same split
    # the goal-driven path makes.
    brief_explicit: bool = False
    brief_note: str = ""
    # A follow-up re-enters a finished run's loop on its branch. INITIAL
    # starts at the loop's own first stage.
    run_kind: LoopRunKind = LoopRunKind.INITIAL
    follow_up_note: str = ""
    entry_stage_id: str | None = None


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
    brief = req.brief or _planning_brief(detail, definition, req.brief_note)
    if req.run_kind is not LoopRunKind.INITIAL:
        brief = followups.seeded_brief(
            brief or LoopBrief(goal=detail.artifact.title),
            definition,
            req.run_kind,
            req.follow_up_note,
        )
    if brief is not None:
        if not req.brief_explicit:
            brief = briefs.with_legacy_required_defaults(definition, brief)
        briefs.validate_brief(definition, brief)
    entry = followups.resolve_entry(definition, req.entry_stage_id)
    entry_provider, entry_model, entry_options = _entry_agent_config(entry, brief)
    session = planning_sessions.get_by_work_slug(req.work_slug)
    if session is None:
        raise PlanningNotStarted(f"planning session not found: {req.work_slug}")
    workspace_root = Path(session.root_path).expanduser()
    # The entry agent is the run's root config, so later stages preflight
    # against what will actually run -- matching the monitor, which inherits
    # from the run's first write-capable agent rather than from Planning.
    validate_stage_agent_policies(
        definition,
        parent_provider=entry_provider,
        parent_model=entry_model,
        parent_options=entry_options,
        parent_folder=workspace_root,
        overrides={
            item.stage_id: item.agent for item in brief.stages if item.agent is not None
        }
        if brief is not None
        else None,
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
        agent_config=(entry_provider, entry_model, entry_options),
        workspace_root=workspace_root,
        entry=entry,
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
        resolutions[entry.step_id],
        brief,
        (
            _loop_runtime.workspace_prompt_context(
                workstore,
                worktree_manager,
                work_slug=req.work_slug,
                agent_slug=agent_slug,
            )
            if any(
                item.kind == LoopContextKind.WORKSPACE_DIFF for item in entry.context
            )
            else ""
        ),
        entry,
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
        entry_step_id=entry.step_id,
        entry_agent_slug=agent_slug,
        entry_is_agent=followups.entry_needs_agent(entry),
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
    agent_config: StageAgentConfig,
    workspace_root: Path,
    entry: LoopStepDefinition,
) -> str:
    """Launch the entry stage with the config the run resolved for it.

    Preconditions: ``agent_config`` came from ``_entry_agent_config``.
    Postconditions: the returned agent runs that config; Planning supplies
    only the workspace root.
    """
    stage = entry
    provider, model, options = agent_config
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
            folder=workspace_root,
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
    entry: LoopStepDefinition,
) -> str:
    """Render the entry stage's provider-facing prompt."""
    stage = entry
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


def _entry_agent_config(
    entry: LoopStepDefinition,
    brief: LoopBrief | None,
) -> StageAgentConfig:
    """Resolve the entry stage's agent from the run's own inputs alone.

    A goal-driven run inherits from the provider chosen for the run; a story
    run has no such parent. The Planning session is not one either -- it is
    the agent that *wrote* the plan, a coincidence rather than a decision,
    and usually the wrong model to implement with. So there is nothing to
    fall back to, and falling back anyway is how a run silently executes as
    something nobody picked.

    Every caller can satisfy this: run setup pins the entry agent, and a
    follow-up arrives with the source run's agent already written into its
    brief by ``rerun_run._with_inherited_entry_agent``. If neither did, that
    is a bug in the caller, not a cue to guess.

    Checking and resolving are deliberately one function. They used to be
    two, and the check passed on a brief the launch then ignored.

    Preconditions: ``entry`` is the run's entry stage and needs an agent;
    ``brief`` belongs to the same definition.
    Postconditions: returns a complete provider/model/options triple built
    only from the brief and the stage policy, or raises.
    """
    if entry.agent is None:
        raise LoopDefinitionInvalid("The first loop stage needs an agent policy.")
    stage_brief = briefs.stage_brief(brief, entry.step_id)
    override = stage_brief.agent if stage_brief is not None else None
    chosen = (override.provider if override else None) or entry.agent.provider
    if not chosen:
        raise StageAgentUnresolved(
            f"{entry.name} does not pin a provider. Choose one in run setup: a "
            "story run has no parent agent to inherit from."
        )
    provider = cast(Provider, chosen)
    # The stage is its own parent. That is what keeps Planning's provider,
    # model, effort and permission posture out of the resolution entirely --
    # cross-provider inheritance only fires when parent and stage differ.
    return resolve_stage_agent_config(
        entry.agent,
        parent_provider=provider,
        parent_model=SPECS[provider].describe().primary_field.default,
        parent_options={},
        override=override,
    )


def _planning_brief(
    detail: PlanArtifactDetail,
    definition: LoopDefinition,
    note: str,
) -> LoopBrief | None:
    """Pin one optional user note to every agent-backed Planning stage.

    The shorthand for a run started without the setup screen; an explicit
    brief on the request takes precedence.
    """
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
    "LoopBriefInvalid",
    "LoopContextMissing",
    "LoopDefinitionConflict",
    "LoopDefinitionInvalid",
    "LoopDefinitionNotFound",
    "PlanArtifactNotExecutable",
    "PlanArtifactNotFound",
    "PlanningNotStarted",
    "StageAgentUnresolved",
    "StartArtifactRunRequest",
    "WorkNotActive",
    "WorkNotFound",
    "execute",
]
