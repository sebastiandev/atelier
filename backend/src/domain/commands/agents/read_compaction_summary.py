"""Read a saved same-agent compaction summary."""

from __future__ import annotations

from dataclasses import dataclass

from src.domain.workstore.ports import WorkStore


@dataclass(frozen=True)
class ReadCompactionSummaryRequest:
    agent_slug: str
    filename: str


@dataclass(frozen=True)
class ReadCompactionSummaryResult:
    agent_slug: str
    work_slug: str
    filename: str
    summary_path: str
    content: str


class AgentNotFound(ValueError):
    pass


class CompactionSummaryNotFound(ValueError):
    pass


def execute(
    workstore: WorkStore, req: ReadCompactionSummaryRequest
) -> ReadCompactionSummaryResult:
    work_slug = workstore.get_work_slug_for_agent(req.agent_slug)
    if work_slug is None:
        raise AgentNotFound(f"agent not found: {req.agent_slug}")

    summary = workstore.read_agent_compaction_doc(
        work_slug, req.agent_slug, req.filename
    )
    if summary is None:
        raise CompactionSummaryNotFound(
            f"compaction summary not found: {req.filename}"
        )

    return ReadCompactionSummaryResult(
        agent_slug=req.agent_slug,
        work_slug=work_slug,
        filename=req.filename,
        summary_path=summary[0],
        content=summary[1],
    )
