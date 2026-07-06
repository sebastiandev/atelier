"""Ingest a structured artifact report from an agent transcript."""

import re
from dataclasses import dataclass
from typing import Any

from src.domain.planning import actions
from src.domain.planning.dtos import (
    PlanArtifactDetail,
    SubmitPlanArtifactReportRequest,
)
from src.domain.planning.ports import PlanningFiles
from src.domain.planning.service import (
    PlanArtifactNotExecutable,
    PlanArtifactNotFound,
    PlanningNotStarted,
)
from src.domain.workstore.ports import WorkStore


class WorkNotFound(ValueError):
    """The work_slug doesn't exist."""


class AgentNotFound(ValueError):
    """The agent_slug doesn't belong to the work."""


class TranscriptReportNotFound(ValueError):
    """No assistant report could be found in the agent transcript."""


@dataclass(frozen=True)
class IngestArtifactReportRequest:
    """Command input for transcript-backed report ingestion."""

    work_slug: str
    artifact_id: str
    agent_slug: str


def execute(
    workstore: WorkStore,
    files: PlanningFiles,
    req: IngestArtifactReportRequest,
) -> PlanArtifactDetail:
    """Extract the latest assistant report and assess the artifact loop.

    Preconditions: Work and linked agent exist, and transcript contains a report.
    Postconditions: run report fields, loop state, and optional proposal are stored.
    """
    if workstore.get_work(req.work_slug) is None:
        raise WorkNotFound(f"work not found: {req.work_slug}")
    if workstore.get_work_slug_for_agent(req.agent_slug) != req.work_slug:
        raise AgentNotFound(f"agent not found on work: {req.agent_slug}")
    events = list(workstore.read_transcript_from_cursor(req.work_slug, req.agent_slug, 0))
    report = _extract_report(events)
    if report is None:
        raise TranscriptReportNotFound(f"no report found for {req.agent_slug}")
    dto = SubmitPlanArtifactReportRequest(
        artifact_id=req.artifact_id,
        agent_slug=req.agent_slug,
        summary=report["summary"],
        divergences=report["divergences"],
        skipped_scope=report["skipped_scope"],
        blockers=report["blockers"],
        decisions=report["decisions"],
        changes=report["changes"],
        validation_evidence=report["validation_evidence"],
        report_source="transcript_markdown",
    )
    manifest = actions.manifest_or_raise(files, req.work_slug)
    detail = actions.detail_or_raise(files, req.work_slug, req.artifact_id)
    now = actions.now_iso()
    actions.apply_artifact_report(manifest, detail.artifact, dto, now=now)
    manifest["updated_at"] = now
    files.write_manifest(req.work_slug, manifest)
    detail = actions.detail_or_raise(files, req.work_slug, req.artifact_id)
    if report["proposed_source"]:
        detail = _create_proposal(
            files,
            req.work_slug,
            req.artifact_id,
            f"Proposed source update from {req.agent_slug}",
            report["proposed_source"],
        )
    return detail


def _create_proposal(
    files: PlanningFiles,
    work_slug: str,
    artifact_id: str,
    title: str,
    proposed_content: str,
) -> PlanArtifactDetail:
    manifest = actions.manifest_or_raise(files, work_slug)
    detail = actions.detail_or_raise(files, work_slug, artifact_id)
    now = actions.now_iso()
    proposals = actions.artifact_proposals_for_update(manifest, artifact_id)
    proposals.append(
        {
            "id": actions.next_id("proposal", proposals),
            "artifact_id": artifact_id,
            "title": title,
            "path": detail.artifact.path,
            "source_hash": detail.artifact.source_hash,
            "proposed_content": actions.ensure_newline(proposed_content),
            "status": "pending",
            "created_at": now,
            "resolved_at": None,
        }
    )
    manifest["updated_at"] = now
    files.write_manifest(work_slug, manifest)
    return actions.detail_or_raise(files, work_slug, artifact_id)


def _extract_report(events: list[dict[str, Any]]) -> dict[str, str] | None:
    texts = [
        text
        for event in events
        if event.get("type") == "message_complete"
        and isinstance((text := event.get("text")), str)
        and text.strip()
    ]
    if not texts:
        return None
    text = texts[-1]
    return {
        "summary": _section(text, "summary") or text.strip(),
        "divergences": _section(text, "divergences"),
        "skipped_scope": _section(text, "skipped scope"),
        "blockers": _section(text, "blockers"),
        "decisions": _section(text, "decisions"),
        "changes": _section(text, "changes"),
        "validation_evidence": _section(text, "validation evidence"),
        "proposed_source": _strip_fence(
            _section_to_end(text, "proposed source")
            or _section_to_end(text, "proposed source update")
        ),
    }


def _section(content: str, name: str) -> str:
    pattern = re.compile(rf"^##\s+{re.escape(name)}\s*$", re.I | re.M)
    match = pattern.search(content)
    if match is None:
        return ""
    start = match.end()
    next_heading = re.search(r"^##\s+", content[start:], re.M)
    end = start + next_heading.start() if next_heading else len(content)
    return content[start:end].strip()


def _section_to_end(content: str, name: str) -> str:
    pattern = re.compile(rf"^##\s+{re.escape(name)}\s*$", re.I | re.M)
    match = pattern.search(content)
    if match is None:
        return ""
    return content[match.end() :].strip()


def _strip_fence(value: str) -> str:
    stripped = value.strip()
    if not stripped.startswith("```"):
        return stripped
    lines = stripped.splitlines()
    if len(lines) >= 2 and lines[-1].strip() == "```":
        return "\n".join(lines[1:-1]).strip()
    return stripped


__all__ = [
    "AgentNotFound",
    "IngestArtifactReportRequest",
    "PlanArtifactNotExecutable",
    "PlanArtifactNotFound",
    "PlanningNotStarted",
    "TranscriptReportNotFound",
    "WorkNotFound",
    "execute",
]
