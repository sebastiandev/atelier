"""Shared planning invariants and mutation helpers."""

from __future__ import annotations

import re
from datetime import UTC, datetime
from typing import Any

from src.domain.loop.dtos import LoopStatus
from src.domain.loop.ports import LoopRunRepository
from src.domain.planning.dtos import (
    AcceptPlanArtifactRequest,
    PlanArtifactDetail,
    PlanArtifactKind,
    PlanArtifactSummary,
    PlanRunStatus,
    PlanTrackingKind,
    WorkPlanView,
)
from src.domain.planning.loop import loop_status_for_run_status
from src.domain.planning.ports import PlanningFiles
from src.domain.planning.service import (
    PlanArtifactNotExecutable,
    PlanArtifactNotFound,
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


def get_plan_or_raise(
    files: PlanningFiles, loop_runs: LoopRunRepository, work_slug: str
) -> WorkPlanView:
    """Project the current source-backed plan.

    Preconditions: planning has been started for ``work_slug``.
    Postconditions: no source files are changed.
    """
    plan = PlanningService(files, loop_runs).get_plan(work_slug)
    if plan is None:
        raise PlanningNotStarted(f"planning not started: {work_slug}")
    return plan


def detail_or_raise(
    files: PlanningFiles,
    loop_runs: LoopRunRepository,
    work_slug: str,
    artifact_id: str,
) -> PlanArtifactDetail:
    """Read one source-backed artifact detail.

    Preconditions: planning exists and ``artifact_id`` is indexed.
    Postconditions: no source files are changed.
    """
    detail = PlanningService(files, loop_runs).get_artifact(work_slug, artifact_id)
    if detail is None:
        raise PlanArtifactNotFound(f"plan artifact not found: {artifact_id}")
    return detail


def require_executable(artifact: PlanArtifactSummary) -> None:
    """Ensure a planning artifact can be assigned to an agent.

    Preconditions: ``artifact`` is an indexed planning artifact.
    Postconditions: raises if the artifact is not executable.
    """
    if not artifact.executable:
        raise PlanArtifactNotExecutable(f"plan artifact is not executable: {artifact.id}")


def current_hashes(
    files: PlanningFiles, loop_runs: LoopRunRepository, work_slug: str
) -> dict[str, str]:
    """Return current source hashes for every indexed plan artifact.

    Preconditions: planning files may exist for ``work_slug``.
    Postconditions: no source files are changed.
    """
    plan = PlanningService(files, loop_runs).get_plan(work_slug)
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


def record_artifact_run_id(
    manifest: dict[str, Any], artifact_id: str, run_id: str
) -> None:
    """Note a run id against its story in the manifest.

    Preconditions: ``manifest`` is mutable; the caller writes it back.
    Postconditions: ``artifact_runs[artifact_id]`` contains ``run_id`` once.

    The manifest lives in the user's repository and describes the plan, so
    it carries ids only -- enough to see which stories have been run
    without holding machine state that is rewritten on every monitor tick.
    SQL owns the run itself.
    """
    all_runs = manifest.setdefault("artifact_runs", {})
    if not isinstance(all_runs, dict):
        all_runs = {}
        manifest["artifact_runs"] = all_runs
    ids = all_runs.setdefault(artifact_id, [])
    if not isinstance(ids, list):
        ids = []
    # Manifests written before SQL became canonical hold whole run bodies.
    # Normalise them to ids on the next write so the list never ends up a
    # mix of dicts and strings.
    ids = [
        str(item.get("id")) if isinstance(item, dict) else item
        for item in ids
        if not isinstance(item, dict) or item.get("id")
    ]
    all_runs[artifact_id] = ids
    if run_id not in ids:
        ids.append(run_id)


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
    loop_runs: LoopRunRepository,
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
    detail = detail_or_raise(files, loop_runs, work_slug, artifact_id)
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
    return detail_or_raise(files, loop_runs, work_slug, artifact_id)


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


def select_run(runs: list[dict[str, Any]], agent_slug: str | None) -> dict[str, Any] | None:
    """Select an explicit or latest artifact run row."""
    if agent_slug:
        return find_run(runs, agent_slug)
    return runs[-1] if runs else None


def apply_report_fields(run: dict[str, Any], req: AcceptPlanArtifactRequest) -> None:
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


__all__ = [
    "apply_report_fields",
    "artifact_proposals_for_update",
    "artifact_tracking_for_update",
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
    "loop_status",
    "manifest_or_raise",
    "next_bug_path",
    "next_id",
    "next_run_id",
    "now_iso",
    "proposal_for_update",
    "record_artifact_run_id",
    "require_executable",
    "run_status",
    "select_run",
    "str_or_empty",
    "str_or_none",
    "summary_template",
    "tracking_kind",
    "upsert_artifact_entry",
]
