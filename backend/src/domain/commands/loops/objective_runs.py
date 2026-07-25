"""Commands for standalone freeform loop runs."""

from __future__ import annotations

from copy import deepcopy
from dataclasses import dataclass, replace
from typing import TYPE_CHECKING, Any

from src.domain.agents.launch import (
    AgentFolderMissing,
    InvalidProviderConfig,
    WorkNotActive,
)
from src.domain.agents.ports import AgentAdapterFactory
from src.domain.artifacts.pr_status import PrLifecycleGateway
from src.domain.connections import ConnectionStore
from src.domain.loop import (
    actions,
    briefs,
    lifecycle,
    objective_start,
    pr_lifecycle,
    pr_review,
    runtime,
)
from src.domain.loop import monitor as loop_monitor
from src.domain.loop.definitions import (
    LoopDefinitionConflict,
    LoopDefinitionInvalid,
    LoopDefinitionNotFound,
)
from src.domain.loop.dtos import (
    LoopBrief,
    LoopRunKind,
    LoopStageBrief,
    LoopStatus,
    LoopStepKind,
    LoopTargetKind,
)
from src.domain.loop.models import LoopRunRecord, LoopRunTarget
from src.domain.loop.objective_store import (
    OBJECTIVE_WORKTREE_SLUG,
    ObjectiveLoopRunStore,
    get_objective_run,
    list_objective_runs,
)
from src.domain.loop.persistence import persist_run
from src.domain.loop.ports import (
    LoopCheckRunner,
    LoopContextResolver,
    LoopDefinitionLocations,
    LoopDefinitionRepository,
    LoopRunRepository,
)
from src.domain.loop.snapshots import definition_from_snapshot
from src.domain.sharedfolders.ports import SharedFolderStore, ShareProvisioner
from src.domain.workstore.dtos import UpdateWorkRequest
from src.domain.workstore.ports import WorkStore
from src.domain.worktrees import WorktreeManager

if TYPE_CHECKING:
    from src.domain.supervisor import AgentSupervisorService

WorkNotFound = objective_start.ObjectiveWorkNotFound


class ObjectiveRunNotFound(ValueError):
    """The requested objective run does not exist."""


LoopContextMissing = objective_start.ObjectiveContextMissing


@dataclass(frozen=True)
class StartObjectiveRunRequest:
    """Command input for starting one freeform objective run."""

    work_slug: str
    goal: str
    root_path: str
    loop_definition_id: str
    loop_revision: str
    provider: str
    model: str
    options: dict[str, object]
    brief: LoopBrief | None = None
    source_run_id: str | None = None


@dataclass(frozen=True)
class ObjectiveRunRequest:
    """Command input identifying one freeform objective run."""

    work_slug: str
    run_id: str


@dataclass(frozen=True)
class RerunObjectiveRunRequest:
    """Command input for a terminal-workspace follow-up run."""

    work_slug: str
    run_id: str
    kind: LoopRunKind = LoopRunKind.INITIAL
    note: str = ""


@dataclass(frozen=True)
class ResumeObjectiveRunRequest:
    """Command input for resuming one blocked objective run."""

    work_slug: str
    run_id: str
    resolution_note: str = ""
    gate_decision: str | None = None
    enforced_findings: tuple[int, ...] = ()


@dataclass(frozen=True)
class RequestObjectiveChangesRequest:
    """Command input for returning an objective run to implementation."""

    work_slug: str
    run_id: str
    note: str


@dataclass(frozen=True)
class CreateObjectivePrRequest:
    """Command input for adding a Work-local PR stage to an accepted run."""

    work_slug: str
    run_id: str
    setup: pr_lifecycle.PrSetup


@dataclass(frozen=True)
class SendObjectivePrFeedbackRequest:
    """Command input for starting another pass from selected PR feedback."""

    work_slug: str
    run_id: str
    comments: tuple[pr_lifecycle.PrFeedbackItem, ...] = ()
    instruction: str = ""


@dataclass(frozen=True)
class RefreshObjectivePrRequest:
    """Command input for synchronizing one run's pull request."""

    work_slug: str
    run_id: str
    force: bool = False


def list_runs(
    repository: LoopRunRepository,
    work_slug: str,
) -> tuple[LoopRunRecord, ...]:
    """Return every objective run for one Work, oldest first."""
    return list_objective_runs(repository, work_slug)


def get_run(
    repository: LoopRunRepository,
    req: ObjectiveRunRequest,
) -> LoopRunRecord:
    """Return one objective run or raise before any state changes."""
    record = get_objective_run(repository, req.work_slug, req.run_id)
    if record is None:
        raise ObjectiveRunNotFound(f"objective run not found: {req.run_id}")
    return record


def get_work_brief(workstore: WorkStore, work_slug: str) -> LoopBrief | None:
    """Return the latest editable brief saved on one Work."""
    record = workstore.get_work(work_slug)
    if record is None:
        raise WorkNotFound(f"work not found: {work_slug}")
    return record.loop_brief


def save_work_brief(
    workstore: WorkStore,
    work_slug: str,
    brief: LoopBrief,
) -> LoopBrief:
    """Persist one editable Work brief without mutating a loop definition."""
    if workstore.get_work(work_slug) is None:
        raise WorkNotFound(f"work not found: {work_slug}")
    if not brief.goal.strip():
        raise ValueError("loop brief goal is required")
    workstore.save_loop_brief(work_slug, brief)
    return brief


async def start_run(
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
    req: StartObjectiveRunRequest,
) -> LoopRunRecord:
    """Adapt user-selected runtime config to the standalone Loop start action."""
    source = (
        get_run(loop_runs, ObjectiveRunRequest(req.work_slug, req.source_run_id))
        if req.source_run_id
        else None
    )
    record = await objective_start.start(
        workstore,
        definitions,
        locations,
        loop_runs,
        context_resolver,
        supervisor,
        worktree_manager,
        connection_store,
        sharestore,
        share_provisioner,
        adapter_factory,
        settings,
        objective_start.ObjectiveStartSpec(
            work_slug=req.work_slug,
            goal=req.goal,
            root_path=req.root_path,
            loop_definition_id=req.loop_definition_id,
            loop_revision=req.loop_revision,
            provider=req.provider,
            model=req.model,
            options=dict(req.options),
            brief=req.brief or LoopBrief(goal=req.goal),
            brief_explicit=req.brief is not None,
            source=source,
        ),
    )
    work = workstore.get_work(req.work_slug)
    if work is not None and work.work.mode != "loop":
        workstore.update_work(UpdateWorkRequest(work_slug=req.work_slug, mode="loop"))
    return record


async def monitor_run(
    workstore: WorkStore,
    loop_runs: LoopRunRepository,
    supervisor: AgentSupervisorService,
    worktree_manager: WorktreeManager,
    connection_store: ConnectionStore,
    sharestore: SharedFolderStore,
    share_provisioner: ShareProvisioner,
    adapter_factory: AgentAdapterFactory,
    check_runner: LoopCheckRunner,
    settings: Any,
    req: ObjectiveRunRequest,
    *,
    pr_gateway: PrLifecycleGateway | None = None,
) -> LoopRunRecord:
    """Monitor one objective run through the generic leased loop engine."""
    get_run(loop_runs, req)
    store = ObjectiveLoopRunStore(loop_runs)
    target = await loop_monitor.execute(
        workstore,
        store,
        supervisor,
        worktree_manager,
        connection_store,
        sharestore,
        share_provisioner,
        adapter_factory,
        check_runner,
        loop_runs,
        settings,
        loop_monitor.MonitorLoopRunRequest(
            work_slug=req.work_slug,
            run_id=req.run_id,
        ),
    )
    if pr_gateway is not None:
        await pr_review.post_addressed_replies(target, pr_gateway)
        store.save(target)
    return get_run(loop_runs, req)


async def resume_run(
    workstore: WorkStore,
    loop_runs: LoopRunRepository,
    supervisor: AgentSupervisorService,
    worktree_manager: WorktreeManager,
    connection_store: ConnectionStore,
    sharestore: SharedFolderStore,
    share_provisioner: ShareProvisioner,
    adapter_factory: AgentAdapterFactory,
    settings: Any,
    req: ResumeObjectiveRunRequest,
) -> LoopRunRecord:
    """Resume one blocked objective run through the loop lifecycle action."""
    key = ObjectiveRunRequest(req.work_slug, req.run_id)
    get_run(loop_runs, key)
    store = ObjectiveLoopRunStore(loop_runs)
    target = _target_or_raise(store, key)
    await lifecycle.resume(
        target,
        workstore,
        supervisor,
        worktree_manager,
        connection_store,
        sharestore,
        share_provisioner,
        adapter_factory,
        settings,
        resolution_note=req.resolution_note,
        gate_decision=req.gate_decision,
        enforced_findings=req.enforced_findings,
    )
    store.save(target)
    return get_run(loop_runs, key)


async def retry_stage(
    workstore: WorkStore,
    loop_runs: LoopRunRepository,
    supervisor: AgentSupervisorService,
    worktree_manager: WorktreeManager,
    connection_store: ConnectionStore,
    sharestore: SharedFolderStore,
    share_provisioner: ShareProvisioner,
    adapter_factory: AgentAdapterFactory,
    settings: Any,
    req: ObjectiveRunRequest,
) -> LoopRunRecord:
    """Retry the failed current stage in its existing run and workspace.

    Preconditions: the objective run failed on a retryable pinned stage.
    Postconditions: the same run/worktree and a fresh stage agent are active
    for one additional attempt.
    """
    get_run(loop_runs, req)
    store = ObjectiveLoopRunStore(loop_runs)
    target = _target_or_raise(store, req)
    await lifecycle.resume(
        target,
        workstore,
        supervisor,
        worktree_manager,
        connection_store,
        sharestore,
        share_provisioner,
        adapter_factory,
        settings,
        resolution_note="Retry the failed stage in the existing workspace.",
        retry_failed=True,
    )
    store.save(target)
    return get_run(loop_runs, req)


async def request_changes(
    workstore: WorkStore,
    loop_runs: LoopRunRepository,
    supervisor: AgentSupervisorService,
    worktree_manager: WorktreeManager,
    sharestore: SharedFolderStore,
    share_provisioner: ShareProvisioner,
    settings: Any,
    req: RequestObjectiveChangesRequest,
) -> LoopRunRecord:
    """Return an awaiting-approval objective to its configured write stage."""
    key = ObjectiveRunRequest(req.work_slug, req.run_id)
    get_run(loop_runs, key)
    store = ObjectiveLoopRunStore(loop_runs)
    target = _target_or_raise(store, key)
    await lifecycle.request_changes(
        target,
        workstore,
        supervisor,
        worktree_manager,
        sharestore,
        share_provisioner,
        settings,
        note=req.note,
    )
    store.save(target)
    return get_run(loop_runs, key)


def create_pr_stage(
    workstore: WorkStore,
    loop_runs: LoopRunRepository,
    req: CreateObjectivePrRequest,
) -> LoopRunRecord:
    """Add and schedule one durable PR stage on an accepted objective run."""
    if workstore.get_work(req.work_slug) is None:
        raise WorkNotFound(f"work not found: {req.work_slug}")
    key = ObjectiveRunRequest(req.work_slug, req.run_id)
    get_run(loop_runs, key)
    store = ObjectiveLoopRunStore(loop_runs)
    target = _target_or_raise(store, key)
    pr_lifecycle.add_one_off_stage(target, req.setup)
    store.save(target)
    return get_run(loop_runs, key)


def send_pr_feedback(
    workstore: WorkStore,
    loop_runs: LoopRunRepository,
    req: SendObjectivePrFeedbackRequest,
) -> LoopRunRecord:
    """Schedule selected PR feedback through the same objective loop run."""
    if workstore.get_work(req.work_slug) is None:
        raise WorkNotFound(f"work not found: {req.work_slug}")
    key = ObjectiveRunRequest(req.work_slug, req.run_id)
    get_run(loop_runs, key)
    store = ObjectiveLoopRunStore(loop_runs)
    target = _target_or_raise(store, key)
    pr_lifecycle.prepare_feedback(target, req.comments, req.instruction)
    store.save(target)
    return get_run(loop_runs, key)


async def refresh_pr(
    workstore: WorkStore,
    loop_runs: LoopRunRepository,
    gateway: PrLifecycleGateway,
    req: RefreshObjectivePrRequest,
) -> LoopRunRecord:
    """Synchronize PR review state and persist any posted comment replies."""
    if workstore.get_work(req.work_slug) is None:
        raise WorkNotFound(f"work not found: {req.work_slug}")
    key = ObjectiveRunRequest(req.work_slug, req.run_id)
    get_run(loop_runs, key)
    store = ObjectiveLoopRunStore(loop_runs)
    target = _target_or_raise(store, key)
    await pr_review.refresh(target, gateway, force=req.force)
    store.save(target)
    return get_run(loop_runs, key)


async def rerun(
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
    req: RerunObjectiveRunRequest,
) -> LoopRunRecord:
    """Start a fresh run from one completed objective run's saved workspace."""
    key = ObjectiveRunRequest(req.work_slug, req.run_id)
    source = get_run(loop_runs, key)
    if source.status not in {
        LoopStatus.ACCEPTED,
        LoopStatus.CANCELLED,
        LoopStatus.FAILED,
    }:
        raise ValueError(f"objective run cannot be reused: {req.run_id}")
    state = source.state
    raw_options = state.get("options")
    pinned_brief = briefs.optional_brief_from_snapshot(state.get("brief"))
    source_brief = pinned_brief or LoopBrief(goal=source.target_ref)
    definition = definition_from_snapshot(source.definition_snapshot)
    brief = source_brief
    entry_stage_id = None
    if req.kind == LoopRunKind.AMEND:
        task_stage = next(
            (stage for stage in definition.stages if stage.kind == LoopStepKind.AGENT_TASK),
            None,
        )
        if task_stage is None:
            raise ValueError("amend follow-up requires an implementation stage")
        brief = _amend_brief(source_brief, req.note, task_stage.step_id)
    elif req.kind == LoopRunKind.VERIFY:
        review_stage = next(
            (
                stage
                for stage in definition.stages
                if stage.kind in {LoopStepKind.AGENT_REVIEW, LoopStepKind.DETERMINISTIC_CHECK}
            ),
            None,
        )
        if review_stage is None:
            raise ValueError("verify follow-up requires a review or check stage")
        entry_stage_id = review_stage.step_id
    record = await objective_start.start(
        workstore,
        definitions,
        locations,
        loop_runs,
        context_resolver,
        supervisor,
        worktree_manager,
        connection_store,
        sharestore,
        share_provisioner,
        adapter_factory,
        settings,
        objective_start.ObjectiveStartSpec(
            work_slug=req.work_slug,
            goal=source.target_ref,
            root_path=actions.str_or_empty(state.get("root_path")),
            loop_definition_id=source.definition_id,
            loop_revision=source.definition_revision,
            provider=actions.str_or_empty(state.get("provider")),
            model=actions.str_or_empty(state.get("model")),
            options=dict(raw_options) if isinstance(raw_options, dict) else {},
            brief=brief,
            brief_explicit=pinned_brief is not None,
            run_kind=req.kind,
            seed_label=(
                "feedback"
                if req.kind == LoopRunKind.AMEND
                else "manual edits"
                if req.kind == LoopRunKind.VERIFY
                else ""
            ),
            entry_stage_id=entry_stage_id,
            source=source,
        ),
    )
    work = workstore.get_work(req.work_slug)
    if work is not None and work.work.mode != "loop":
        workstore.update_work(UpdateWorkRequest(work_slug=req.work_slug, mode="loop"))
    return record


def _amend_brief(brief: LoopBrief, note: str, stage_id: str) -> LoopBrief:
    """Append required follow-up feedback to the first task-stage note."""
    feedback = note.strip()
    if not feedback:
        raise ValueError("amend follow-up runs require a brief note")
    existing = next((item for item in brief.stages if item.stage_id == stage_id), None)
    if existing is not None:
        updated = replace(
            existing,
            note="\n\n".join(part for part in (existing.note.strip(), feedback) if part),
        )
        return replace(
            brief,
            stages=tuple(updated if item.stage_id == stage_id else item for item in brief.stages),
        )
    return replace(
        brief,
        stages=(*brief.stages, LoopStageBrief(stage_id=stage_id, note=feedback)),
    )


async def accept_run(
    workstore: WorkStore,
    loop_runs: LoopRunRepository,
    supervisor: AgentSupervisorService,
    req: ObjectiveRunRequest,
) -> LoopRunRecord:
    """Approve one objective, releasing runtimes only when the loop ends."""
    if workstore.get_work(req.work_slug) is None:
        raise WorkNotFound(f"work not found: {req.work_slug}")
    get_run(loop_runs, req)
    store = ObjectiveLoopRunStore(loop_runs)
    target = _target_or_raise(store, req)
    completed = lifecycle.accept(target)
    store.save(target)
    if completed:
        await runtime.release_run_agents(
            workstore,
            supervisor,
            work_slug=req.work_slug,
            run=target.run,
            loop=actions.dict_or_empty(target.run.get("loop")),
        )
    return get_run(loop_runs, req)


async def cancel_run(
    workstore: WorkStore,
    loop_runs: LoopRunRepository,
    supervisor: AgentSupervisorService,
    req: ObjectiveRunRequest,
) -> LoopRunRecord:
    """Stop an active objective run while preserving its workspace for cleanup.

    Preconditions: the run exists and is non-terminal.
    Postconditions: stage agents are stopped and the durable run is cancelled.
    """
    get_run(loop_runs, req)
    store = ObjectiveLoopRunStore(loop_runs)
    target = _target_or_raise(store, req)
    lifecycle.cancel(target)
    loop = actions.dict_or_empty(target.run.get("loop"))
    await runtime.release_run_agents(
        workstore,
        supervisor,
        work_slug=req.work_slug,
        run=target.run,
        loop=loop,
    )
    store.save(target)
    return get_run(loop_runs, req)


async def clean_run(
    workstore: WorkStore,
    loop_runs: LoopRunRepository,
    supervisor: AgentSupervisorService,
    req: ObjectiveRunRequest,
) -> LoopRunRecord:
    """Compatibility alias for releasing a terminal run's providers.

    Preconditions: the run exists and is accepted, cancelled, or already cleaned.
    Postconditions: providers are stopped and cleanup time is recorded while
    agents, transcripts, and the Work workspace remain.
    """
    record = get_run(loop_runs, req)
    if record.status == LoopStatus.CLEANED:
        return record
    if record.status not in {LoopStatus.ACCEPTED, LoopStatus.CANCELLED}:
        raise ValueError(f"objective run cannot be cleaned up: {req.run_id}")
    run = deepcopy(record.state)
    loop = actions.dict_or_empty(run.get("loop"))
    await runtime.release_run_agents(
        workstore,
        supervisor,
        work_slug=req.work_slug,
        run=run,
        loop=loop,
    )
    now = actions.now_iso()
    loop["status"] = LoopStatus.CLEANED.value
    loop["status_reason"] = "Provider runtimes were released; transcripts and workspace were kept."
    run["cleanup_at"] = now
    run["loop"] = loop
    persist_run(
        loop_runs,
        work_slug=req.work_slug,
        target_kind=LoopTargetKind.OBJECTIVE,
        target_ref=record.target_ref,
        run=run,
    )
    return get_run(loop_runs, req)


def _target_or_raise(
    store: ObjectiveLoopRunStore,
    req: ObjectiveRunRequest,
) -> LoopRunTarget:
    target = store.load(req.work_slug, req.run_id)
    if target is None:
        raise ObjectiveRunNotFound(f"objective run not found: {req.run_id}")
    return target


__all__ = [
    "OBJECTIVE_WORKTREE_SLUG",
    "AgentFolderMissing",
    "CreateObjectivePrRequest",
    "InvalidProviderConfig",
    "LoopContextMissing",
    "LoopDefinitionConflict",
    "LoopDefinitionInvalid",
    "LoopDefinitionNotFound",
    "ObjectiveRunNotFound",
    "ObjectiveRunRequest",
    "RefreshObjectivePrRequest",
    "RequestObjectiveChangesRequest",
    "ResumeObjectiveRunRequest",
    "SendObjectivePrFeedbackRequest",
    "StartObjectiveRunRequest",
    "WorkNotActive",
    "WorkNotFound",
    "accept_run",
    "cancel_run",
    "clean_run",
    "create_pr_stage",
    "get_run",
    "list_runs",
    "monitor_run",
    "refresh_pr",
    "request_changes",
    "rerun",
    "resume_run",
    "retry_stage",
    "send_pr_feedback",
    "start_run",
]
