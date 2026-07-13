"""Shared planning invariants and mutation helpers."""

from __future__ import annotations

import re
from datetime import UTC, datetime
from typing import Any

from src.domain.loop.dtos import (
    LoopAssessment,
    LoopAssessmentStatus,
    LoopDefinition,
    LoopStatus,
    LoopStepStatus,
)
from src.domain.loop.snapshots import definition_snapshot
from src.domain.planning.dtos import (
    AcceptPlanArtifactRequest,
    PlanArtifactDetail,
    PlanArtifactKind,
    PlanArtifactSummary,
    PlanRunStatus,
    PlanTrackingKind,
    SubmitPlanArtifactReportRequest,
    WorkPlanView,
)
from src.domain.planning.loop import (
    PLANNING_ARTIFACT_LOOP_DEFINITION_ID,
    assess_planning_report,
    build_report,
    loop_status_for_run_status,
    run_status_for_loop,
)
from src.domain.planning.ports import PlanningFiles
from src.domain.planning.service import (
    PlanArtifactNotExecutable,
    PlanArtifactNotFound,
    PlanArtifactRunNotFound,
    PlanningNotStarted,
    PlanningService,
)

_TRACKING_KINDS: set[str] = {"jira", "pr", "blocker", "bug"}
_RUN_STATUSES: set[PlanRunStatus] = {
    PlanRunStatus.RUNNING,
    PlanRunStatus.NEEDS_ATTENTION,
    PlanRunStatus.WAITING_APPROVAL,
    PlanRunStatus.BLOCKED,
    PlanRunStatus.COMPLETED_PENDING_REVIEW,
    PlanRunStatus.ACCEPTED,
}
_LOOP_STATUSES: set[LoopStatus] = {
    LoopStatus.PENDING,
    LoopStatus.RUNNING,
    LoopStatus.WAITING_REPORT,
    LoopStatus.ASSESSING,
    LoopStatus.NEEDS_AGENT,
    LoopStatus.BLOCKED_USER,
    LoopStatus.COMPLETED,
    LoopStatus.AWAITING_APPROVAL,
    LoopStatus.ACCEPTED,
    LoopStatus.CLEANED,
    LoopStatus.FAILED,
    LoopStatus.CANCELLED,
}


def manifest_or_raise(files: PlanningFiles, work_slug: str) -> dict[str, Any]:
    """Load a planning manifest.

    Preconditions: planning has been started for ``work_slug``.
    Postconditions: returns the mutable manifest dict or raises.
    """
    manifest = files.read_manifest(work_slug)
    if manifest is None:
        raise PlanningNotStarted(f"planning not started: {work_slug}")
    return manifest


def get_plan_or_raise(files: PlanningFiles, work_slug: str) -> WorkPlanView:
    """Project the current source-backed plan.

    Preconditions: planning has been started for ``work_slug``.
    Postconditions: no source files are changed.
    """
    plan = PlanningService(files).get_plan(work_slug)
    if plan is None:
        raise PlanningNotStarted(f"planning not started: {work_slug}")
    return plan


def detail_or_raise(
    files: PlanningFiles, work_slug: str, artifact_id: str
) -> PlanArtifactDetail:
    """Read one source-backed artifact detail.

    Preconditions: planning exists and ``artifact_id`` is indexed.
    Postconditions: no source files are changed.
    """
    detail = PlanningService(files).get_artifact(work_slug, artifact_id)
    if detail is None:
        raise PlanArtifactNotFound(f"plan artifact not found: {artifact_id}")
    return detail


def require_executable(artifact: PlanArtifactSummary) -> None:
    """Ensure a planning artifact can be assigned to an agent.

    Preconditions: ``artifact`` is an indexed planning artifact.
    Postconditions: raises if the artifact is not executable.
    """
    if not artifact.executable:
        raise PlanArtifactNotExecutable(
            f"plan artifact is not executable: {artifact.id}"
        )


def current_hashes(files: PlanningFiles, work_slug: str) -> dict[str, str]:
    """Return current source hashes for every indexed plan artifact.

    Preconditions: planning files may exist for ``work_slug``.
    Postconditions: no source files are changed.
    """
    plan = PlanningService(files).get_plan(work_slug)
    if plan is None:
        return {}
    return {row.path: row.source_hash for row in plan.artifacts}


def artifact_proposals_for_update(
    manifest: dict[str, Any], artifact_id: str
) -> list[dict[str, Any]]:
    """Return mutable proposal rows for one artifact.

    Preconditions: ``manifest`` is mutable.
    Postconditions: the proposal container exists in ``manifest``.
    """
    all_proposals = manifest.setdefault("artifact_proposals", {})
    if not isinstance(all_proposals, dict):
        all_proposals = {}
        manifest["artifact_proposals"] = all_proposals
    proposals = all_proposals.setdefault(artifact_id, [])
    if not isinstance(proposals, list):
        proposals = []
        all_proposals[artifact_id] = proposals
    return proposals


def artifact_tracking_for_update(
    manifest: dict[str, Any], artifact_id: str
) -> list[dict[str, Any]]:
    """Return mutable tracking rows for one artifact.

    Preconditions: ``manifest`` is mutable.
    Postconditions: the tracking container exists in ``manifest``.
    """
    all_tracking = manifest.setdefault("artifact_tracking", {})
    if not isinstance(all_tracking, dict):
        all_tracking = {}
        manifest["artifact_tracking"] = all_tracking
    links = all_tracking.setdefault(artifact_id, [])
    if not isinstance(links, list):
        links = []
        all_tracking[artifact_id] = links
    return links


def artifact_runs_for_update(
    manifest: dict[str, Any], artifact_id: str
) -> list[dict[str, Any]]:
    """Return mutable run rows for one artifact.

    Preconditions: ``manifest`` is mutable.
    Postconditions: the run container exists in ``manifest``.
    """
    all_runs = manifest.setdefault("artifact_runs", {})
    if not isinstance(all_runs, dict):
        all_runs = {}
        manifest["artifact_runs"] = all_runs
    runs = all_runs.setdefault(artifact_id, [])
    if not isinstance(runs, list):
        runs = []
        all_runs[artifact_id] = runs
    return runs


def proposal_for_update(
    manifest: dict[str, Any], artifact_id: str, proposal_id: str
) -> dict[str, Any] | None:
    """Find a mutable proposal row.

    Preconditions: ``manifest`` is mutable.
    Postconditions: returns the row or ``None``; no source files are changed.
    """
    for proposal in artifact_proposals_for_update(manifest, artifact_id):
        if proposal.get("id") == proposal_id:
            return proposal
    return None


def create_artifact_proposal(
    files: PlanningFiles,
    work_slug: str,
    artifact_id: str,
    title: str,
    proposed_content: str,
) -> PlanArtifactDetail:
    """Create a pending full-source proposal for one artifact.

    Preconditions: planning exists and ``artifact_id`` is indexed.
    Postconditions: manifest stores one pending proposal; source files are not
    changed.
    """
    manifest = manifest_or_raise(files, work_slug)
    detail = detail_or_raise(files, work_slug, artifact_id)
    now = now_iso()
    proposals = artifact_proposals_for_update(manifest, artifact_id)
    proposals.append(
        {
            "id": next_id("proposal", proposals),
            "artifact_id": artifact_id,
            "title": title,
            "path": detail.artifact.path,
            "source_hash": detail.artifact.source_hash,
            "proposed_content": ensure_newline(proposed_content),
            "status": "pending",
            "created_at": now,
            "resolved_at": None,
        }
    )
    manifest["updated_at"] = now
    files.write_manifest(work_slug, manifest)
    return detail_or_raise(files, work_slug, artifact_id)


def find_run(runs: list[dict[str, Any]], agent_slug: str) -> dict[str, Any] | None:
    """Find a mutable artifact run row by agent slug."""
    for run in runs:
        if run.get("agent_slug") == agent_slug:
            return run
    return None


def find_run_by_id(runs: list[dict[str, Any]], run_id: str) -> dict[str, Any] | None:
    """Find a mutable artifact run row by run id."""
    for run in runs:
        if run.get("id") == run_id:
            return run
    return None


def select_run(
    runs: list[dict[str, Any]], agent_slug: str | None
) -> dict[str, Any] | None:
    """Select an explicit or latest artifact run row."""
    if agent_slug:
        return find_run(runs, agent_slug)
    return runs[-1] if runs else None


def apply_report_fields(
    run: dict[str, Any],
    req: AcceptPlanArtifactRequest | SubmitPlanArtifactReportRequest,
) -> None:
    """Copy structured report fields onto a mutable run row.

    Preconditions: ``run`` is mutable and ``req`` contains report fields.
    Postconditions: ``run`` stores the latest structured report values.
    """
    run["summary"] = req.summary
    run["divergences"] = req.divergences
    run["skipped_scope"] = req.skipped_scope
    run["blockers"] = req.blockers
    run["decisions"] = req.decisions
    run["changes"] = req.changes
    run["validation_evidence"] = req.validation_evidence


def apply_artifact_report(
    manifest: dict[str, Any],
    artifact: PlanArtifactSummary,
    req: SubmitPlanArtifactReportRequest,
    *,
    now: str,
) -> None:
    """Apply and assess a report for one executable planning artifact.

    Preconditions: ``manifest`` is mutable and ``artifact`` is the indexed
    artifact targeted by ``req``.
    Postconditions: the selected/latest run stores report fields, loop
    assessment, run status, and completion timestamp when terminal.
    """
    require_executable(artifact)
    runs = artifact_runs_for_update(manifest, req.artifact_id)
    run = find_run_by_id(runs, req.run_id) if req.run_id else None
    if run is None:
        run = select_run(runs, req.agent_slug)
    if run is None:
        run = {
            "id": req.run_id or next_run_id(runs),
            "agent_slug": req.agent_slug or "manual-review",
            "started_at": now,
        }
        runs.append(run)
    apply_report_fields(run, req)
    loop = dict_or_empty(run.get("loop"))
    current_loop_run_id = loop_run_id(
        req.artifact_id,
        str(run["agent_slug"]),
        loop,
        run_id=str_or_none(run.get("id")),
    )
    report_id = next_loop_child_id("report", loop.get("latest_report_id"))
    assessment_id = next_loop_child_id("assess", loop.get("latest_assessment_id"))
    report = build_report(
        req,
        loop_run_id=current_loop_run_id,
        report_id=report_id,
        submitted_at=now,
        source=req.report_source,
    )
    assessment = assess_planning_report(
        report,
        assessment_id=assessment_id,
        created_at=now,
    )
    next_loop_status = loop_status_from_assessment(assessment)
    run["loop"] = assessed_loop_snapshot(
        artifact_id=req.artifact_id,
        agent_slug=str(run["agent_slug"]),
        existing=loop,
        loop_status=next_loop_status,
        report_id=report.report_id,
        report_source=report.source,
        assessment=assessment,
    )
    run["status"] = run_status_for_loop(next_loop_status).value
    run["completed_at"] = (
        now
        if next_loop_status
        in {LoopStatus.COMPLETED, LoopStatus.BLOCKED_USER, LoopStatus.FAILED}
        else None
    )


def upsert_artifact_entry(
    manifest: dict[str, Any],
    *,
    path: str,
    title: str,
    artifact_kind: PlanArtifactKind,
    executable: bool,
    dependencies: list[str],
) -> None:
    """Create or replace one manifest artifact metadata entry.

    Preconditions: ``manifest`` is mutable.
    Postconditions: exactly one entry for ``path`` exists in the manifest.
    """
    raw = manifest.setdefault("artifacts", [])
    if not isinstance(raw, list):
        raw = []
        manifest["artifacts"] = raw
    entry = {
        "path": path,
        "title": title,
        "artifact_kind": artifact_kind,
        "executable": executable,
        "dependencies": dependencies,
    }
    for idx, item in enumerate(raw):
        if isinstance(item, dict) and item.get("path") == path:
            raw[idx] = entry
            return
    raw.append(entry)


def running_loop_snapshot(
    *,
    artifact_id: str,
    agent_slug: str,
    existing: dict[str, Any] | None,
    run_id: str | None = None,
) -> dict[str, Any]:
    """Build a loop snapshot for a running planning artifact agent.

    Preconditions: ``artifact_id`` and ``agent_slug`` identify one run.
    Postconditions: returns manifest-ready loop metadata.
    """
    previous = existing or {}
    attempt = int_or_default(previous.get("attempt"), 0) + 1
    return {
        **previous,
        "loop_run_id": loop_run_id(artifact_id, agent_slug, previous, run_id=run_id),
        "definition_id": str_or_none(previous.get("definition_id"))
        or PLANNING_ARTIFACT_LOOP_DEFINITION_ID,
        "status": LoopStatus.RUNNING.value,
        "status_reason": f"{agent_slug} is working on {artifact_id}.",
        "attempt": attempt,
        "latest_report_id": str_or_none(previous.get("latest_report_id")),
        "latest_assessment_id": str_or_none(previous.get("latest_assessment_id")),
        "findings": str_list(previous.get("findings")),
        "last_checked_seq": int_or_default(previous.get("last_checked_seq"), 0),
    }


def initialized_loop_snapshot(
    *,
    artifact_id: str,
    agent_slug: str,
    run_id: str,
    definition: LoopDefinition,
) -> dict[str, Any]:
    """Build the immutable definition and initial stage state for a new run.

    Preconditions: ``definition`` is valid and revision-pinned for this launch.
    Postconditions: the first stage is running and later stages are pending.
    """
    loop = running_loop_snapshot(
        artifact_id=artifact_id,
        agent_slug=agent_slug,
        existing=None,
        run_id=run_id,
    )
    first = definition.stages[0]
    loop.update(
        {
            "definition_id": definition.definition_id,
            "definition_name": definition.name,
            "definition_revision": definition.revision,
            "definition_snapshot": definition_snapshot(definition),
            "current_stage_id": first.step_id,
            "source_agent_slug": agent_slug,
            "owned_agent_slugs": [agent_slug],
            "stages": [
                {
                    "id": stage.step_id,
                    "name": stage.name,
                    "kind": stage.kind.value,
                    "status": (
                        LoopStepStatus.RUNNING.value
                        if stage.step_id == first.step_id
                        else LoopStepStatus.PENDING.value
                    ),
                    "attempt": 1 if stage.step_id == first.step_id else 0,
                    "max_attempts": stage.retry.max_attempts,
                    "agent_slug": (
                        agent_slug if stage.step_id == first.step_id else None
                    ),
                    "permissions": (
                        stage.agent.permissions.value
                        if stage.agent and stage.agent.permissions is not None
                        else None
                    ),
                    "session": stage.agent.session.value if stage.agent else None,
                    "summary": "",
                    "findings": [],
                }
                for stage in definition.stages
            ],
        }
    )
    return loop


def assessed_loop_snapshot(
    *,
    artifact_id: str,
    agent_slug: str,
    existing: dict[str, Any],
    loop_status: LoopStatus,
    report_id: str,
    report_source: str,
    assessment: LoopAssessment,
) -> dict[str, Any]:
    """Build a loop snapshot from a completed backend assessment.

    Preconditions: ``assessment`` belongs to the run's latest report.
    Postconditions: returns manifest-ready loop metadata.
    """
    return {
        **existing,
        "loop_run_id": loop_run_id(artifact_id, agent_slug, existing),
        "definition_id": str_or_none(existing.get("definition_id"))
        or PLANNING_ARTIFACT_LOOP_DEFINITION_ID,
        "status": loop_status.value,
        "status_reason": loop_status_reason(loop_status, assessment),
        "attempt": int_or_default(existing.get("attempt"), 1),
        "latest_report_id": report_id,
        "latest_assessment_id": assessment.assessment_id,
        "report_source": report_source,
        "findings": assessment.findings,
        "next_prompt": assessment.next_prompt,
    }


def mark_run_running(
    manifest: dict[str, Any],
    *,
    artifact_id: str,
    agent_slug: str | None = None,
    run_id: str | None = None,
    existing_loop: dict[str, Any],
    last_checked_seq: int | None = None,
) -> None:
    """Mark one artifact run as active again.

    Preconditions: ``manifest`` contains a run for ``artifact_id`` and either
    ``run_id`` or ``agent_slug``.
    Postconditions: the run status and loop status are running, with an
    incremented loop attempt.
    """
    runs = artifact_runs_for_update(manifest, artifact_id)
    run = find_run_by_id(runs, run_id) if run_id else None
    if run is None and agent_slug:
        run = find_run(runs, agent_slug)
    if run is None:
        raise PlanArtifactRunNotFound(
            f"plan artifact run not found: {run_id or agent_slug}"
        )
    run["status"] = PlanRunStatus.RUNNING.value
    run["completed_at"] = None
    loop = running_loop_snapshot(
        artifact_id=artifact_id,
        agent_slug=str(run.get("agent_slug") or agent_slug or ""),
        existing=existing_loop,
        run_id=run_id,
    )
    if last_checked_seq is not None:
        loop["last_checked_seq"] = last_checked_seq
    run["loop"] = loop
    manifest["updated_at"] = now_iso()


def mark_run_failed(
    manifest: dict[str, Any],
    *,
    artifact_id: str,
    run_id: str,
    reason: str,
    findings: list[str],
) -> None:
    """Mark one artifact run as failed.

    Preconditions: ``manifest`` contains ``run_id`` for ``artifact_id``.
    Postconditions: the run is terminal, with failed loop metadata persisted.
    """
    run = find_run_by_id(artifact_runs_for_update(manifest, artifact_id), run_id)
    if run is None:
        raise PlanArtifactRunNotFound(f"plan artifact run not found: {run_id}")
    now = now_iso()
    run["status"] = PlanRunStatus.BLOCKED.value
    run["completed_at"] = now
    loop = dict_or_empty(run.get("loop"))
    loop["status"] = LoopStatus.FAILED.value
    loop["status_reason"] = reason
    loop["findings"] = findings
    run["loop"] = loop
    manifest["updated_at"] = now


def loop_status_from_assessment(assessment: LoopAssessment) -> LoopStatus:
    """Map a report assessment onto the loop state machine."""
    if assessment.status == LoopAssessmentStatus.PASS:
        return LoopStatus.COMPLETED
    if assessment.status == LoopAssessmentStatus.NEEDS_AGENT:
        return LoopStatus.NEEDS_AGENT
    if assessment.status == LoopAssessmentStatus.BLOCKED_USER:
        return LoopStatus.BLOCKED_USER
    return LoopStatus.FAILED


def loop_status_reason(status: LoopStatus, assessment: LoopAssessment) -> str:
    """Return the displayable reason for one assessed loop status."""
    if status == LoopStatus.COMPLETED:
        return "Report is complete and ready for review."
    if status == LoopStatus.NEEDS_AGENT:
        return "Report is incomplete; the agent needs to continue."
    if status == LoopStatus.BLOCKED_USER:
        return "The agent reported a blocker that needs user input."
    if status == LoopStatus.FAILED:
        return "Atelier could not assess the loop report."
    return assessment.findings[0] if assessment.findings else status.replace("_", " ")


def loop_run_id(
    artifact_id: str,
    agent_slug: str,
    existing: dict[str, Any] | None,
    *,
    run_id: str | None = None,
) -> str:
    """Return the stable loop id for one artifact/agent pair."""
    if existing:
        current = str_or_none(existing.get("loop_run_id"))
        if current:
            return current
    raw_suffix = run_id or agent_slug
    safe_suffix = re.sub(r"[^a-zA-Z0-9._-]+", "-", raw_suffix).strip("-").lower()
    return f"loop-{artifact_id}-{safe_suffix}"


def next_loop_child_id(prefix: str, current: object) -> str:
    """Return the next manifest-local loop child id."""
    previous = str_or_none(current)
    if previous:
        match = re.search(r"(\d+)$", previous)
        if match:
            return f"{prefix}-{int(match.group(1)) + 1:03d}"
    return f"{prefix}-001"


def next_id(prefix: str, items: list[dict[str, Any]]) -> str:
    """Return the next id for a manifest-local list."""
    return f"{prefix}-{len(items) + 1:03d}"


def next_run_id(items: list[dict[str, Any]]) -> str:
    """Return the next run id for one artifact's manifest-local runs."""
    return next_id("run", items)


def next_bug_path(files: PlanningFiles, work_slug: str) -> tuple[str, str]:
    """Return the next bug id and source path.

    Preconditions: planning directory may contain bug docs.
    Postconditions: no files are changed.
    """
    used = {
        int(match.group(1))
        for rel_path in files.list_markdown(work_slug, "bugs")
        if (match := re.match(r"bugs/bug-(\d+)\.md$", rel_path))
    }
    n = 1
    while n in used:
        n += 1
    bug_id = f"bug-{n:03d}"
    return bug_id, f"bugs/{bug_id}.md"


def bug_template(source: PlanArtifactSummary, bug_id: str, title: str, description: str) -> str:
    """Render a source-backed bug document.

    Preconditions: ``source`` is the finding source artifact.
    Postconditions: returns Markdown ending in one newline.
    """
    return ensure_newline(
        f"""# {bug_id}: {clean(title) or "Review finding"}

## Explanation

{clean(description) or "Describe the finding and observed behavior."}

## References

- Source artifact: {source.id}
- Source file: {source.source_ref}

## Validation

- Reproduce or verify the finding.
- Confirm the fix with focused validation.
"""
    )


def summary_template(
    artifact: PlanArtifactSummary, req: AcceptPlanArtifactRequest, accepted_at: str
) -> str:
    """Render the durable accepted-work summary document.

    Preconditions: ``artifact`` is executable and accepted by the caller.
    Postconditions: returns Markdown ending in one newline.
    """
    cleaned = clean(req.summary) or "Accepted without additional notes."
    return ensure_newline(
        f"""# {artifact.title} Work Summary

Artifact: {artifact.id}
Accepted at: {accepted_at}
Source hash: {artifact.source_hash}

## Reviewer Summary

{cleaned}

## Divergences

{clean(req.divergences) or "None reported."}

## Skipped Scope

{clean(req.skipped_scope) or "None reported."}

## Blockers

{clean(req.blockers) or "None reported."}

## Decisions

{clean(req.decisions) or "None reported."}

## Changes

{clean(req.changes) or "Not reported."}

## Validation Evidence

{clean(req.validation_evidence) or "Not reported."}
"""
    )


def loop_status(value: object, fallback: PlanRunStatus) -> LoopStatus:
    """Return persisted loop status or infer one from legacy run status."""
    if isinstance(value, str):
        try:
            status = LoopStatus(value)
        except ValueError:
            pass
        else:
            if status in _LOOP_STATUSES:
                return status
    return loop_status_for_run_status(fallback)


def run_status(run: dict[str, Any]) -> PlanRunStatus:
    """Return a normalized run status from a mutable manifest row.

    Preconditions: ``run`` is a manifest artifact-run row.
    Postconditions: returns a valid planning run status.
    """
    value = run.get("status")
    if isinstance(value, str):
        try:
            status = PlanRunStatus(value)
        except ValueError:
            pass
        else:
            if status in _RUN_STATUSES:
                return status
    return PlanRunStatus.RUNNING


def tracking_kind(value: PlanTrackingKind) -> PlanTrackingKind:
    """Normalize a tracking kind for manifest storage."""
    if value in _TRACKING_KINDS:
        return value
    return "jira"


def now_iso() -> str:
    """Return the canonical planning timestamp string."""
    return datetime.now(UTC).isoformat()


def ensure_newline(value: str) -> str:
    """Return ``value`` with one trailing newline when missing."""
    return value if value.endswith("\n") else value + "\n"


def clean(value: str | None) -> str | None:
    """Trim optional user content and collapse blanks to ``None``."""
    if value is None:
        return None
    cleaned = value.strip()
    return cleaned or None


def dict_or_empty(value: object) -> dict[str, Any]:
    """Return ``value`` when it is a dict, otherwise an empty dict."""
    return value if isinstance(value, dict) else {}


def str_or_none(value: object) -> str | None:
    """Return ``value`` when it is a string, otherwise ``None``."""
    return value if isinstance(value, str) else None


def str_or_empty(value: object) -> str:
    """Return ``value`` when it is a string, otherwise empty string."""
    return value if isinstance(value, str) else ""


def int_or_default(value: object, default: int) -> int:
    """Return a positive integer value or ``default``."""
    return value if isinstance(value, int) and value > 0 else default


def str_list(value: object) -> list[str]:
    """Return string items from ``value`` when it is a list."""
    if not isinstance(value, list):
        return []
    return [item for item in value if isinstance(item, str)]


__all__ = [
    "apply_artifact_report",
    "apply_report_fields",
    "artifact_proposals_for_update",
    "artifact_runs_for_update",
    "artifact_tracking_for_update",
    "assessed_loop_snapshot",
    "bug_template",
    "clean",
    "create_artifact_proposal",
    "current_hashes",
    "detail_or_raise",
    "dict_or_empty",
    "ensure_newline",
    "find_run",
    "find_run_by_id",
    "get_plan_or_raise",
    "initialized_loop_snapshot",
    "int_or_default",
    "loop_run_id",
    "loop_status",
    "loop_status_from_assessment",
    "loop_status_reason",
    "manifest_or_raise",
    "mark_run_failed",
    "mark_run_running",
    "next_bug_path",
    "next_id",
    "next_loop_child_id",
    "next_run_id",
    "now_iso",
    "proposal_for_update",
    "require_executable",
    "run_status",
    "running_loop_snapshot",
    "select_run",
    "str_list",
    "str_or_empty",
    "str_or_none",
    "summary_template",
    "tracking_kind",
    "upsert_artifact_entry",
]
