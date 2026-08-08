"""Start action for standalone user-configured loop objectives."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING, Any, cast

from src.domain.agents.launch import (
    AgentFolderMissing,
    AgentLaunchRequest,
    WorkNotActive,
    launch_agent,
)
from src.domain.agents.ports import AgentAdapterFactory
from src.domain.connections import ConnectionStore
from src.domain.loop import actions, briefs, followups, pr_lifecycle, runtime
from src.domain.loop.agent_policy import (
    resolve_stage_agent_config,
    validate_stage_agent_policies,
)
from src.domain.loop.catalog import LoopDefinitionRoots, locate_definition
from src.domain.loop.definitions import LoopDefinitionConflict, LoopDefinitionInvalid
from src.domain.loop.dtos import (
    LoopBrief,
    LoopContextKind,
    LoopContextResolution,
    LoopContextResolutionRequest,
    LoopRunKind,
    LoopRunStatus,
    LoopStatus,
    LoopStepDefinition,
    LoopStepKind,
    LoopStepStatus,
    LoopTargetKind,
)
from src.domain.loop.models import LoopRunRecord
from src.domain.loop.persistence import persist_run
from src.domain.loop.ports import (
    LoopContextResolver,
    LoopDefinitionLocations,
    LoopDefinitionRepository,
    LoopRunRepository,
)
from src.domain.loop.prompts import (
    ReviewStagePrompt,
    StageReportBlock,
    TaskStagePrompt,
    build_stage_prompt,
)
from src.domain.loop.snapshots import definition_from_snapshot
from src.domain.loop.store import (
    DEFAULT_TARGET_ID,
    DEFAULT_WORKTREE_SLUG,
    get_run_record,
    list_runs,
)
from src.domain.models import Provider
from src.domain.sharedfolders.ports import SharedFolderStore, ShareProvisioner
from src.domain.workstore.ports import WorkStore
from src.domain.worktrees import WorktreeManager

if TYPE_CHECKING:
    from src.domain.supervisor import AgentSupervisorService


class WorkNotFound(ValueError):
    """The standalone run's Work does not exist."""


class ContextMissing(ValueError):
    """One or more required loop context references could not be resolved."""


@dataclass(frozen=True)
class LoopRunStartSpec:
    """User-supplied runtime configuration for one goal-driven loop run."""

    work_slug: str
    goal: str
    root_path: str
    loop_definition_id: str
    loop_revision: str
    provider: str
    model: str
    options: dict[str, object]
    brief: LoopBrief
    brief_explicit: bool = True
    run_kind: LoopRunKind = LoopRunKind.INITIAL
    seed_label: str = ""
    entry_stage_id: str | None = None
    source: LoopRunRecord | None = None


async def start(
    workstore: WorkStore,
    definitions: LoopDefinitionRepository,
    locations: LoopDefinitionLocations,
    loop_runs: LoopRunRepository,
    context_resolver: LoopContextResolver,
    supervisor: AgentSupervisorService,
    worktree_manager: WorktreeManager,
    connection_store: ConnectionStore,
    sharestore: SharedFolderStore,
    share_provisioner: ShareProvisioner,
    adapter_factory: AgentAdapterFactory,
    settings: Any,
    spec: LoopRunStartSpec,
) -> LoopRunRecord:
    """Launch and persist one goal-driven loop run.

    Preconditions: Work, root, template, user runtime config, and required context
    are valid. Postconditions: the entry stage and pinned run state exist.
    """
    work = workstore.get_work(spec.work_slug)
    if work is None:
        raise WorkNotFound(f"work not found: {spec.work_slug}")
    if work.work.status != "active":
        raise WorkNotActive(f"work {spec.work_slug} is completed; reopen it before starting a run")
    source = spec.source
    if source is not None and source.status not in {
        LoopStatus.ACCEPTED,
        LoopStatus.CANCELLED,
        LoopStatus.FAILED,
    }:
        source_id = actions.str_or_empty(source.state.get("id")) or source.run_key
        raise ValueError(f"loop run cannot be reused: {source_id}")
    root = Path(spec.root_path).expanduser().resolve()
    if not root.is_dir():
        raise AgentFolderMissing(f"agent folder does not exist: {root}")
    if (
        source is not None
        and spec.loop_definition_id == source.definition_id
        and spec.loop_revision == source.definition_revision
    ):
        definition = definition_from_snapshot(source.definition_snapshot)
    else:
        definition = locate_definition(
            definitions,
            LoopDefinitionRoots(
                library=locations.loop_library_root(),
                work=locations.work_loop_root(spec.work_slug),
            ),
            spec.loop_definition_id,
        ).definition
    if not definition.valid:
        raise LoopDefinitionInvalid(" ".join(definition.errors))
    if spec.loop_revision and spec.loop_revision != definition.revision:
        raise LoopDefinitionConflict(f"loop definition changed: {definition.definition_id}")
    brief = (
        spec.brief
        if spec.brief_explicit
        else briefs.with_legacy_required_defaults(definition, spec.brief)
    )
    if brief.goal != spec.goal:
        raise ValueError("loop brief goal must match the run goal")
    briefs.validate_brief(definition, brief)

    parent_provider = cast(Provider, spec.provider)
    parent_options = dict(spec.options)
    validate_stage_agent_policies(
        definition,
        parent_provider=parent_provider,
        parent_model=spec.model,
        parent_options=parent_options,
        parent_folder=root,
        overrides={item.stage_id: item.agent for item in brief.stages if item.agent is not None},
    )
    resolutions = _resolve_contexts(
        context_resolver,
        spec.work_slug,
        root,
        definition.stages,
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
        raise ContextMissing("Required loop context is missing: " + "; ".join(missing))

    entry = followups.resolve_entry(definition, spec.entry_stage_id)
    source_loop = actions.dict_or_empty(source.state.get("loop")) if source else {}
    source_agent_slug = actions.str_or_none(source_loop.get("source_agent_slug"))
    if source is not None and source_agent_slug is None:
        raise ValueError("loop run has no reusable source agent")
    workspace_slug, source_workspace = _workspace_source(
        workstore,
        spec,
        source,
        source_loop,
    )
    workspace = worktree_manager.ensure(
        spec.work_slug,
        workspace_slug,
        root,
    )
    if source_workspace is not None and workspace.resolve() != source_workspace:
        raise ValueError("loop run did not resolve to its retained workspace")
    run_number = (
        max(
            (
                actions.int_or_default(row.state.get("number"), 0)
                for row in list_runs(loop_runs, spec.work_slug)
            ),
            default=0,
        )
        + 1
    )
    run_id = f"run-{run_number:03d}"
    entry_agent_slug: str | None = None
    run_agent_slug = source_agent_slug
    cursor = 0
    if followups.entry_needs_agent(entry):
        if entry.agent is None:
            raise LoopDefinitionInvalid(f"Agent policy missing for stage: {entry.step_id}")
        stage_brief = briefs.stage_brief(brief, entry.step_id)
        provider, model, options = resolve_stage_agent_config(
            entry.agent,
            parent_provider=parent_provider,
            parent_model=spec.model,
            parent_options=parent_options,
            override=stage_brief.agent if stage_brief is not None else None,
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
                work_slug=spec.work_slug,
                name=f"{entry.name} · objective",
                persona=("architect" if entry.kind == LoopStepKind.AGENT_REVIEW else "developer"),
                role=entry.instructions,
                provider=provider,
                model=model,
                folder=root,
                options=options,
                worktree_slug=workspace_slug,
                approved_command_prefixes=briefs.resolved_approved_command_prefixes(
                    definition,
                    brief,
                    entry,
                ),
            ),
        )
        if agent.slug is None:
            raise RuntimeError("loop launch returned an agent without slug")
        entry_agent_slug = agent.slug
        run_agent_slug = agent.slug
        cursor = runtime.last_transcript_seq(
            workstore,
            work_slug=spec.work_slug,
            agent_slug=agent.slug,
        )
        resolution = resolutions[entry.step_id]
        brief_note, brief_context = briefs.prompt_values(brief, entry.step_id)
        prompt_type = (
            ReviewStagePrompt if entry.kind == LoopStepKind.AGENT_REVIEW else TaskStagePrompt
        )
        changed_files = (
            actions.changed_file_prompt_lines(source.state.get("changed_files"))
            if source is not None
            else ()
        ) or actions.latest_changed_file_prompt_lines(source_loop)
        workspace_diff = (
            runtime.workspace_prompt_context(
                workstore,
                worktree_manager,
                work_slug=spec.work_slug,
                agent_slug=agent.slug,
            )
            if any(item.kind == LoopContextKind.WORKSPACE_DIFF for item in entry.context)
            else ""
        )
        await runtime.send_loop_prompt(
            workstore,
            supervisor,
            worktree_manager,
            sharestore,
            share_provisioner,
            settings,
            work_slug=spec.work_slug,
            agent_slug=agent.slug,
            prompt=build_stage_prompt(
                prompt_type(
                    run_id=run_id,
                    work_slug=spec.work_slug,
                    artifact_id=DEFAULT_TARGET_ID,
                    artifact_title=spec.goal,
                    source_ref=str(root),
                    stage=entry,
                    # A follow-up run's entry stage has no earlier stage of its
                    # own to read, so the source run's account is labelled as
                    # what it is -- and only when the stage asked for a report.
                    reports=(
                        (
                            StageReportBlock(
                                stage_id="source",
                                stage_name="The run this one continues",
                                summary=actions.str_or_empty(source.state.get("summary")),
                                findings=tuple(actions.str_list(source_loop.get("findings"))),
                            ),
                        )
                        if source is not None and entry.reports
                        else ()
                    ),
                    previous_changed_files=changed_files,
                    workspace_diff=workspace_diff,
                    resolved_context=resolution.entries,
                    context_warnings=resolution.warnings,
                    brief_note=brief_note,
                    brief_context=brief_context,
                )
            ),
        )
    elif source is None or run_agent_slug is None:
        raise ValueError("non-agent loop entry requires a reusable workspace")
    now = actions.now_iso()
    loop = actions.initialized_loop_snapshot(
        target_id=DEFAULT_TARGET_ID,
        agent_slug=run_agent_slug,
        run_id=run_id,
        definition=definition,
        entry_step_id=entry.step_id,
        entry_agent_slug=entry_agent_slug,
        source_agent_slug=source_agent_slug or entry_agent_slug,
        entry_is_agent=followups.entry_needs_agent(entry),
    )
    if source is not None:
        pr_lifecycle.inherit_existing_pr(loop, source_loop)
    loop["last_checked_seq"] = cursor
    for stage_row in loop["stages"]:
        if not isinstance(stage_row, dict):
            continue
        stage_resolution = resolutions.get(actions.str_or_empty(stage_row.get("id")))
        if stage_resolution is not None:
            stage_row["resolved_context"] = list(stage_resolution.entries)
            stage_row["context_warnings"] = list(stage_resolution.warnings)
    waiting_approval = entry.kind == LoopStepKind.USER_APPROVAL
    if waiting_approval:
        loop["status"] = LoopStatus.AWAITING_APPROVAL.value
        loop["status_reason"] = "The result is ready for approval."
        approval_row = actions.stage_row(loop, entry.step_id)
        if approval_row is not None:
            approval_row["status"] = LoopStepStatus.PENDING.value
    run = {
        "id": run_id,
        "number": run_number,
        "goal": spec.goal,
        "brief": briefs.brief_snapshot(brief),
        "run_kind": spec.run_kind.value,
        "seed_label": spec.seed_label,
        "root_path": str(root),
        "workspace_path": str(workspace),
        "provider": spec.provider,
        "model": spec.model,
        "options": dict(spec.options),
        "source_run_id": actions.str_or_none(source.state.get("id")) if source else None,
        "agent_slug": run_agent_slug,
        "status": (
            LoopRunStatus.COMPLETED_PENDING_REVIEW.value
            if waiting_approval
            else LoopRunStatus.RUNNING.value
        ),
        "started_at": now,
        "updated_at": now,
        "completed_at": now if waiting_approval else None,
        "accepted_at": None,
        "loop": loop,
    }
    workstore.save_loop_brief(spec.work_slug, brief)
    persist_run(
        loop_runs,
        work_slug=spec.work_slug,
        target_kind=LoopTargetKind.OBJECTIVE,
        target_ref=spec.goal,
        run=run,
    )
    record = get_run_record(loop_runs, spec.work_slug, run_id)
    if record is None:
        raise RuntimeError(f"loop run was not persisted: {run_id}")
    return record


def _resolve_contexts(
    resolver: LoopContextResolver,
    work_slug: str,
    root: Path,
    stages: tuple[LoopStepDefinition, ...],
    shared_refs: tuple[str, ...],
) -> dict[str, LoopContextResolution]:
    """Resolve all stage context from a standalone run's user-selected root."""
    return {
        stage.step_id: resolver.resolve(
            LoopContextResolutionRequest(
                root_path=root,
                work_slug=work_slug,
                target_ref=str(root),
                plan_index_ref=str(root / "planning-index-unavailable"),
                dependencies=(),
                shared_context_refs=shared_refs,
                references=stage.context,
            )
        )
        for stage in stages
    }


def _workspace_source(
    workstore: WorkStore,
    spec: LoopRunStartSpec,
    source: LoopRunRecord | None,
    source_loop: dict[str, Any],
) -> tuple[str, Path | None]:
    """Return the stable workspace owner and retained path for one run."""
    if source is None:
        return DEFAULT_WORKTREE_SLUG, None
    source_agent_slug = actions.str_or_none(source_loop.get("source_agent_slug"))
    if source_agent_slug is None:
        raise ValueError("loop run has no reusable workspace")
    source_agent = next(
        (
            item
            for item in workstore.list_agents_for_work(spec.work_slug)
            if item.slug == source_agent_slug
        ),
        None,
    )
    if source_agent is None:
        raise ValueError("loop run has no reusable workspace")
    raw_workspace = actions.str_or_empty(source.state.get("workspace_path"))
    if not raw_workspace:
        raise ValueError("loop run has no reusable workspace")
    source_workspace = Path(raw_workspace).expanduser().resolve()
    if not source_workspace.is_dir():
        raise ValueError(f"loop run workspace is missing: {source_workspace}")
    return source_agent.worktree_slug or source_agent_slug, source_workspace


__all__ = [
    "ContextMissing",
    "LoopRunStartSpec",
    "WorkNotActive",
    "WorkNotFound",
    "start",
]
