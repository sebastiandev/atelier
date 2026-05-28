"""Compact an agent's provider context without replacing the agent."""

from __future__ import annotations

import logging
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import TYPE_CHECKING, Any, Literal

from src.domain.agents import (
    SPECS,
    AgentStartContext,
    CommonAgentConfig,
    detect_shared_envs,
    render_system_prompt,
)
from src.domain.agents.compactions import (
    CompactionSessionClient,
    trim_transcript_to_char_cap,
)
from src.domain.agents.configs import AgentConfig
from src.domain.agents.handoffs import (
    Summarizer,
    SummaryContext,
    format_summary_prompt,
)
from src.domain.commands.agents import resume
from src.domain.models import Agent, AgentStatus, Provider
from src.domain.sharedfolders.ports import SharedFolderStore, ShareProvisioner
from src.domain.workstore.ports import WorkStore
from src.domain.worktrees import WorktreeManager, WorktreeState
from src.settings import Settings

if TYPE_CHECKING:
    from src.domain.supervisor import AgentSupervisorService

CompactionReason = Literal["manual", "forced_context_limit"]

_log = logging.getLogger(__name__)

@dataclass(frozen=True)
class CompactAgentRequest:
    agent_slug: str
    reason: CompactionReason = "manual"


@dataclass(frozen=True)
class CompactAgentResult:
    agent_slug: str
    work_slug: str
    provider: Provider
    old_session_id: str
    new_session_id: str
    summary_path: str
    breadcrumb_written: bool
    breadcrumb_error: str | None = None


class AgentNotFound(ValueError):
    pass


class AgentNotCompactable(ValueError):
    pass


class AgentBusy(ValueError):
    pass


async def execute(
    workstore: WorkStore,
    supervisor: AgentSupervisorService,
    worktree_manager: WorktreeManager,
    sharestore: SharedFolderStore,
    share_provisioner: ShareProvisioner,
    settings: Settings,
    summarizer: Summarizer,
    session_client: CompactionSessionClient,
    req: CompactAgentRequest,
    *,
    clock: Callable[[], datetime] = lambda: datetime.now(UTC),
) -> CompactAgentResult:
    work_slug = workstore.get_work_slug_for_agent(req.agent_slug)
    if work_slug is None:
        raise AgentNotFound(f"agent not found: {req.agent_slug}")
    record = workstore.get_work(work_slug)
    if record is None:
        raise AgentNotFound(f"work not found: {work_slug}")
    agent = _find_agent(workstore, work_slug, req.agent_slug)
    if agent.session_id is None:
        raise AgentNotCompactable(
            f"agent {req.agent_slug} has no provider session to compact"
        )
    old_session_id = agent.session_id

    if _last_runtime_status(workstore, work_slug, req.agent_slug) in {
        "live",
        "thinking",
    }:
        raise AgentBusy(
            "agent appears to be mid-turn; stop the turn or wait for idle before compacting"
        )

    workdir = worktree_manager.ensure(
        work_slug=work_slug,
        agent_slug=req.agent_slug,
        source=agent.folder,
    )

    from src.domain.commands.agents.start import (
        _agent_writable_roots,
        _mount_project_shares,
    )

    mounted_shares = _mount_project_shares(
        sharestore=sharestore,
        provisioner=share_provisioner,
        project_slug=record.work.project_slug,
        work_slug=work_slug,
        agent_slug=req.agent_slug,
    )
    common = CommonAgentConfig(
        workdir=workdir,
        writable_roots=_agent_writable_roots(
            mounted_shares, worktree_manager, workdir
        ),
        system_prompt=render_system_prompt(
            agent.persona,
            agent.role,
            workdir=workdir,
            shares=mounted_shares.summaries,
            is_detached_worktree=worktree_manager.is_detached(workdir),
            shared_envs=detect_shared_envs(workdir),
        ),
    )
    config = SPECS[agent.provider].build(
        common, agent.model, dict(agent.options or {})
    )
    context = AgentStartContext(
        workdir=common.workdir,
        model=agent.model,
        system_prompt=common.system_prompt,
        session_id=None,
    )

    # Stop before transcript writes so compaction markers use the
    # WorkStore's single-writer seq helper without racing the supervisor.
    await supervisor.stop_agent(req.agent_slug)
    workstore.set_agent_status(req.agent_slug, AgentStatus.IDLE)
    all_events = list(workstore.read_transcript_from_cursor(work_slug, req.agent_slug, 0))
    source_events = _summary_source_events(
        workstore=workstore,
        work_slug=work_slug,
        agent_slug=req.agent_slug,
        events=all_events,
    )

    now = clock()
    workstore.append_transcript_event_with_seq(
        work_slug,
        req.agent_slug,
        {
            "type": "compaction_requested",
            "ts": now.isoformat(),
            "reason": req.reason,
            "old_session_id": old_session_id,
            "provider": agent.provider,
        },
    )

    repo_state = worktree_manager.describe_state(workdir)
    summary_context = SummaryContext(
        work_name=record.work.name,
        work_description=record.work.description,
        source_agent_name=agent.name,
        source_agent_role=agent.role,
    )
    summary_body = await _summarize_with_provider(
        session_client=session_client,
        config=config,
        context=context,
        events=source_events,
        summary_context=summary_context,
        fallback=summarizer,
    )
    summary_doc = _format_summary_doc(
        agent=agent,
        work_name=record.work.name,
        work_description=record.work.description,
        work_slug=work_slug,
        workdir=workdir,
        reason=req.reason,
        old_session_id=old_session_id,
        repo_state=repo_state,
        summary_body=summary_body,
        transcript_hint="transcript.ndjson in this agent directory",
        created_at=now,
    )
    filename = f"{now.strftime('%Y%m%d-%H%M%S')}.md"
    summary_path = workstore.write_agent_compaction_doc(
        work_slug, req.agent_slug, filename, summary_doc
    )
    transcript_path = str(Path(summary_path).parent.parent / "transcript.ndjson")

    workstore.append_transcript_event_with_seq(
        work_slug,
        req.agent_slug,
        {
            "type": "compaction_summary_created",
            "ts": clock().isoformat(),
            "summary_path": summary_path,
            "old_session_id": old_session_id,
            "reason": req.reason,
        },
    )

    seed_message = _fresh_session_seed(
        summary=summary_doc,
        summary_path=summary_path,
        transcript_path=transcript_path,
    )
    try:
        started = await session_client.start_fresh_session(
            config=config,
            context=context,
            seed_message=seed_message,
        )
    except Exception as exc:
        workstore.append_transcript_event_with_seq(
            work_slug,
            req.agent_slug,
            {
                "type": "compaction_failed",
                "ts": clock().isoformat(),
                "old_session_id": old_session_id,
                "summary_path": summary_path,
                "reason": req.reason,
                "error": repr(exc),
            },
        )
        # A browser reconnect can race the long-running compaction window and
        # lazily register the agent after the initial stop. Evict that stale
        # state so resume below seeds from the transcript we just wrote.
        await supervisor.stop_agent(req.agent_slug)
        await _reregister(
            workstore,
            supervisor,
            worktree_manager,
            sharestore,
            share_provisioner,
            settings,
            work_slug,
            req.agent_slug,
        )
        raise

    breadcrumb = _old_session_breadcrumb(
        new_session_id=started.session_id,
        summary_path=summary_path,
        transcript_path=transcript_path,
    )
    breadcrumb_result = await session_client.write_breadcrumb(
        config=config,
        context=context,
        old_session_id=old_session_id,
        breadcrumb=breadcrumb,
    )

    workstore.set_agent_session_id(
        req.agent_slug, started.session_id, mirror_agent_json=True
    )
    workstore.set_agent_status(req.agent_slug, AgentStatus.IDLE)
    workstore.append_transcript_event_with_seq(
        work_slug,
        req.agent_slug,
        {
            "type": "compaction_old_session_breadcrumb",
            "ts": clock().isoformat(),
            "old_session_id": old_session_id,
            "new_session_id": started.session_id,
            "written": breadcrumb_result.written,
            "error": breadcrumb_result.error,
        },
    )
    workstore.append_transcript_event_with_seq(
        work_slug,
        req.agent_slug,
        {
            "type": "context_compacted",
            "ts": clock().isoformat(),
            "old_session_id": old_session_id,
            "new_session_id": started.session_id,
            "summary_path": summary_path,
            "reason": req.reason,
            "provider": agent.provider,
        },
    )

    # See the failure path above: if the frontend reconnected while we were
    # summarizing or seeding, the supervisor may now hold a lazy state whose
    # replay high-water predates the final compaction boundary. Stop it before
    # re-registering so the next WS replay includes `context_compacted`.
    await supervisor.stop_agent(req.agent_slug)
    await _reregister(
        workstore,
        supervisor,
        worktree_manager,
        sharestore,
        share_provisioner,
        settings,
        work_slug,
        req.agent_slug,
    )

    return CompactAgentResult(
        agent_slug=req.agent_slug,
        work_slug=work_slug,
        provider=agent.provider,
        old_session_id=old_session_id,
        new_session_id=started.session_id,
        summary_path=summary_path,
        breadcrumb_written=breadcrumb_result.written,
        breadcrumb_error=breadcrumb_result.error,
    )


async def _summarize_with_provider(
    *,
    session_client: CompactionSessionClient,
    config: AgentConfig,
    context: AgentStartContext,
    events: list[dict[str, Any]],
    summary_context: SummaryContext,
    fallback: Summarizer,
) -> str:
    try:
        summary = await session_client.summarize_transcript(
            config=config,
            context=context,
            prompt=format_summary_prompt(events, summary_context),
        )
        if summary.strip():
            return summary
        raise ValueError("provider summary was empty")
    except Exception as exc:
        _log.warning(
            "provider compaction summary failed; falling back to app summarizer: %r",
            exc,
        )
        return fallback(events, summary_context)


def _summary_source_events(
    *,
    workstore: WorkStore,
    work_slug: str,
    agent_slug: str,
    events: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    previous_index = _last_compaction_boundary_index(events)
    if previous_index is None:
        return trim_transcript_to_char_cap(events)

    selected = list(events[previous_index + 1 :])
    previous_summary = _read_previous_summary(
        workstore=workstore,
        work_slug=work_slug,
        agent_slug=agent_slug,
        boundary=events[previous_index],
    )
    if previous_summary:
        selected.insert(
            0,
            {
                "type": "previous_compaction_summary",
                "content": previous_summary,
            },
        )
    return trim_transcript_to_char_cap(selected)


def _last_compaction_boundary_index(events: list[dict[str, Any]]) -> int | None:
    for index in range(len(events) - 1, -1, -1):
        event = events[index]
        if event.get("type") == "context_compacted" and event.get("summary_path"):
            return index
    return None


def _read_previous_summary(
    *,
    workstore: WorkStore,
    work_slug: str,
    agent_slug: str,
    boundary: dict[str, Any],
) -> str | None:
    summary_path = boundary.get("summary_path")
    if not isinstance(summary_path, str) or not summary_path.strip():
        return None
    summary = workstore.read_agent_compaction_doc(
        work_slug, agent_slug, Path(summary_path).name
    )
    if summary is None:
        return None
    return _previous_summary_context(summary[1])


def _previous_summary_context(summary_doc: str) -> str:
    body = _extract_compacted_summary_body(summary_doc)
    body = _remove_previous_compacted_context_block(body)
    return body.strip()


def _extract_compacted_summary_body(summary_doc: str) -> str:
    marker = "\n## Compacted Summary\n"
    start = summary_doc.find(marker)
    if start < 0:
        return summary_doc
    body_start = start + len(marker)
    end = summary_doc.find("\n## Repository State", body_start)
    if end < 0:
        return summary_doc[body_start:]
    return summary_doc[body_start:end]


def _remove_previous_compacted_context_block(summary_body: str) -> str:
    lines = summary_body.splitlines()
    cleaned: list[str] = []
    skipping_previous_context = False

    for line in lines:
        if line.strip() == "Previous compacted context:":
            skipping_previous_context = True
            continue
        if skipping_previous_context:
            if not line.strip():
                skipping_previous_context = False
            continue
        cleaned.append(line)

    return "\n".join(cleaned)


def _find_agent(workstore: WorkStore, work_slug: str, agent_slug: str) -> Agent:
    agent = next(
        (a for a in workstore.list_agents_for_work(work_slug) if a.slug == agent_slug),
        None,
    )
    if agent is None:
        raise AgentNotFound(f"agent not found: {agent_slug}")
    return agent


def _last_runtime_status(
    workstore: WorkStore, work_slug: str, agent_slug: str
) -> str | None:
    status: str | None = None
    for event in workstore.read_transcript_from_cursor(work_slug, agent_slug, 0):
        if event.get("type") != "status_change":
            continue
        value = event.get("status")
        if isinstance(value, str):
            status = value
    return status


def _format_summary_doc(
    *,
    agent: Agent,
    work_name: str,
    work_description: str,
    work_slug: str,
    workdir: Path,
    reason: CompactionReason,
    old_session_id: str,
    repo_state: WorktreeState,
    summary_body: str,
    transcript_hint: str,
    created_at: datetime,
) -> str:
    lines = [
        f"# Compacted Context for {agent.name}",
        "",
        f"Created: {created_at.isoformat()}",
        f"Reason: {reason}",
        f"Work: {work_slug} - {work_name}",
        f"Agent: {agent.slug}",
        f"Provider: {agent.provider}",
        f"Old provider session/thread: {old_session_id}",
        f"Worktree: {workdir}",
        f"Transcript: {transcript_hint}",
        "",
        "## Work Description",
        work_description.strip() or "(not set)",
        "",
        "## Compacted Summary",
        _clean_compaction_summary_body(summary_body),
        "",
        "## Repository State",
        _format_repo_state(repo_state),
    ]
    return "\n".join(lines).rstrip() + "\n"


def _clean_compaction_summary_body(summary_body: str) -> str:
    cleaned = summary_body.strip()
    if not cleaned:
        return "(empty summary)"

    legacy_open_questions = (
        "## Open questions\n"
        "_The structural summarizer can't infer open questions. "
        "Edit this section before the new agent reads it._"
    )
    cleaned = cleaned.replace(legacy_open_questions, "").strip()
    return cleaned or "(empty summary)"


def _format_repo_state(state: WorktreeState) -> str:
    lines = [f"Workdir: `{state.workdir}`"]
    if state.error:
        lines.append(f"Error: {state.error}")
    if not state.is_git_repo:
        lines.append("Git: not a git worktree")
        return "\n".join(lines)
    lines.append(f"Git: {state.branch or '(detached HEAD)'} @ {state.head or '?'}")
    if state.status:
        lines.append("")
        lines.append("Status:")
        lines.append("```text")
        lines.append(state.status)
        lines.append("```")
    else:
        lines.append("Status: clean")
    if state.changed_files:
        lines.append("")
        lines.append("Changed files:")
        lines.extend(f"- `{path}`" for path in state.changed_files[:50])
    if state.untracked_files:
        lines.append("")
        lines.append("Untracked files:")
        lines.extend(f"- `{path}`" for path in state.untracked_files[:50])
    return "\n".join(lines)


def _fresh_session_seed(
    *, summary: str, summary_path: str, transcript_path: str
) -> str:
    return (
        "Atelier compacted the previous provider session for this same agent.\n\n"
        "You are continuing the same work in the same worktree. Do not treat "
        "this as a new task.\n\n"
        "Read this compacted context carefully and continue from it. The full "
        f"Atelier transcript remains available at:\n{transcript_path}\n\n"
        f"Summary file:\n{summary_path}\n\n"
        "<COMPACTED_CONTEXT>\n"
        f"{summary.rstrip()}\n"
        "</COMPACTED_CONTEXT>"
    )


def _old_session_breadcrumb(
    *, new_session_id: str, summary_path: str, transcript_path: str
) -> str:
    return (
        "Atelier compacted this conversation.\n\n"
        f"New provider session/thread: {new_session_id}\n"
        f"Summary file: {summary_path}\n"
        f"Full Atelier transcript: {transcript_path}\n\n"
        "If this old session is resumed manually, continue in the new "
        "session/thread instead."
    )


async def _reregister(
    workstore: WorkStore,
    supervisor: AgentSupervisorService,
    worktree_manager: WorktreeManager,
    sharestore: SharedFolderStore,
    share_provisioner: ShareProvisioner,
    settings: Settings,
    work_slug: str,
    agent_slug: str,
) -> None:
    await resume.execute(
        workstore,
        supervisor,
        worktree_manager,
        sharestore,
        share_provisioner,
        settings,
        resume.ResumeAgentRequest(work_slug=work_slug, agent_slug=agent_slug),
    )


__all__ = [
    "AgentBusy",
    "AgentNotCompactable",
    "AgentNotFound",
    "CompactAgentRequest",
    "CompactAgentResult",
    "execute",
]
