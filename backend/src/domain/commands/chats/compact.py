"""Compact a chat's provider context without changing chat identity."""

from __future__ import annotations

import dataclasses
import logging
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Literal

from src.domain.agents.compactions import (
    CompactionSessionClient,
    trim_transcript_to_char_cap,
)
from src.domain.agents.handoffs import (
    Summarizer,
    SummaryContext,
    format_summary_prompt,
)
from src.domain.chatstore import ChatRecord, ChatStore
from src.domain.commands.chats.connect import build_chat_runtime_config
from src.domain.models import Provider
from src.domain.projectstore.ports import ProjectStore
from src.domain.supervisor import AgentSupervisorService
from src.domain.workstore.ports import WorkStore
from src.settings import Settings

CompactionReason = Literal["manual", "forced_context_limit"]

_log = logging.getLogger(__name__)


@dataclass(frozen=True)
class CompactChatRequest:
    chat_slug: str
    reason: CompactionReason = "manual"


@dataclass(frozen=True)
class CompactChatResult:
    chat_slug: str
    provider: Provider
    old_session_id: str
    new_session_id: str
    summary_path: str
    breadcrumb_written: bool
    breadcrumb_error: str | None = None


class ChatNotFound(ValueError):
    pass


class ChatNotCompactable(ValueError):
    pass


class ChatBusy(ValueError):
    pass


async def execute(
    chatstore: ChatStore,
    supervisor: AgentSupervisorService,
    workstore: WorkStore,
    projectstore: ProjectStore,
    settings: Settings,
    summarizer: Summarizer,
    session_client: CompactionSessionClient,
    req: CompactChatRequest,
    *,
    clock: Callable[[], datetime] = lambda: datetime.now(UTC),
) -> CompactChatResult:
    record = chatstore.get_chat(req.chat_slug)
    if record is None:
        raise ChatNotFound(f"chat not found: {req.chat_slug}")
    if record.chat.session_id is None:
        raise ChatNotCompactable(
            f"chat {req.chat_slug} has no provider session to compact"
        )
    old_session_id = record.chat.session_id

    raw_events = list(chatstore.read_transcript_from_cursor(req.chat_slug, 0))
    if _last_runtime_status(raw_events) in {"live", "thinking"}:
        raise ChatBusy(
            "chat appears to be mid-turn; stop the turn or wait for idle before compacting"
        )

    config, context, runtime = build_chat_runtime_config(
        record, workstore, projectstore, settings
    )
    maintenance_context = dataclasses.replace(context, session_id=None)

    # Stop before transcript writes. A reconnect can still register a lazy
    # state during the long provider-summary step; stop again at the end so
    # the next stream uses the new session id and replays the boundary.
    await supervisor.stop_agent(req.chat_slug)

    now = clock()
    chatstore.append_transcript_event_with_seq(
        req.chat_slug,
        {
            "type": "compaction_requested",
            "ts": now.isoformat(),
            "reason": req.reason,
            "old_session_id": old_session_id,
            "provider": record.chat.provider,
        },
    )

    summary_events = _summary_source_events(
        chatstore=chatstore,
        chat_slug=req.chat_slug,
        events=[ev for ev in (_event_for_summary(e) for e in raw_events) if ev],
    )
    summary_context = SummaryContext(
        work_name=record.chat.title,
        work_description=_chat_description(record),
        source_agent_name=record.chat.slug or req.chat_slug,
        source_agent_role="Exploratory chat",
    )
    summary_body = await _summarize_with_provider(
        session_client=session_client,
        config=config,
        context=maintenance_context,
        events=summary_events,
        summary_context=summary_context,
        fallback=summarizer,
    )
    summary_doc = _format_summary_doc(
        record=record,
        reason=req.reason,
        old_session_id=old_session_id,
        workdir=runtime.workdir,
        grounding_label=runtime.working_label,
        grounding_details=_format_runtime_context(runtime),
        summary_body=summary_body,
        transcript_hint="transcript.ndjson in this chat directory",
        created_at=now,
    )
    filename = f"{now.strftime('%Y%m%d-%H%M%S')}.md"
    summary_path = chatstore.write_chat_compaction_doc(
        req.chat_slug, filename, summary_doc
    )
    transcript_path = str(Path(summary_path).parent.parent / "transcript.ndjson")

    chatstore.append_transcript_event_with_seq(
        req.chat_slug,
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
            context=maintenance_context,
            seed_message=seed_message,
        )
    except Exception as exc:
        chatstore.append_transcript_event_with_seq(
            req.chat_slug,
            {
                "type": "compaction_failed",
                "ts": clock().isoformat(),
                "old_session_id": old_session_id,
                "summary_path": summary_path,
                "reason": req.reason,
                "error": repr(exc),
            },
        )
        await supervisor.stop_agent(req.chat_slug)
        raise

    breadcrumb = _old_session_breadcrumb(
        new_session_id=started.session_id,
        summary_path=summary_path,
        transcript_path=transcript_path,
    )
    breadcrumb_result = await session_client.write_breadcrumb(
        config=config,
        context=maintenance_context,
        old_session_id=old_session_id,
        breadcrumb=breadcrumb,
    )

    chatstore.set_chat_session_id(req.chat_slug, started.session_id)
    chatstore.append_transcript_event_with_seq(
        req.chat_slug,
        {
            "type": "compaction_old_session_breadcrumb",
            "ts": clock().isoformat(),
            "old_session_id": old_session_id,
            "new_session_id": started.session_id,
            "written": breadcrumb_result.written,
            "error": breadcrumb_result.error,
        },
    )
    chatstore.append_transcript_event_with_seq(
        req.chat_slug,
        {
            "type": "context_compacted",
            "ts": clock().isoformat(),
            "old_session_id": old_session_id,
            "new_session_id": started.session_id,
            "summary_path": summary_path,
            "reason": req.reason,
            "provider": record.chat.provider,
        },
    )

    await supervisor.stop_agent(req.chat_slug)

    return CompactChatResult(
        chat_slug=req.chat_slug,
        provider=record.chat.provider,
        old_session_id=old_session_id,
        new_session_id=started.session_id,
        summary_path=summary_path,
        breadcrumb_written=breadcrumb_result.written,
        breadcrumb_error=breadcrumb_result.error,
    )


async def _summarize_with_provider(
    *,
    session_client: CompactionSessionClient,
    config: Any,
    context: Any,
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
            "provider chat compaction summary failed; falling back: %r",
            exc,
        )
        return fallback(events, summary_context)


def _summary_source_events(
    *,
    chatstore: ChatStore,
    chat_slug: str,
    events: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    previous_index = _last_compaction_boundary_index(events)
    if previous_index is None:
        return trim_transcript_to_char_cap(events)

    selected = list(events[previous_index + 1 :])
    previous_summary = _read_previous_summary(
        chatstore=chatstore,
        chat_slug=chat_slug,
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
    *, chatstore: ChatStore, chat_slug: str, boundary: dict[str, Any]
) -> str | None:
    summary_path = boundary.get("summary_path")
    if not isinstance(summary_path, str) or not summary_path.strip():
        return None
    summary = chatstore.read_chat_compaction_doc(chat_slug, Path(summary_path).name)
    if summary is None:
        return None
    return _extract_compacted_summary_body(summary[1]).strip()


def _extract_compacted_summary_body(summary_doc: str) -> str:
    marker = "\n## Compacted Summary\n"
    start = summary_doc.find(marker)
    if start < 0:
        return summary_doc
    body_start = start + len(marker)
    end = summary_doc.find("\n## Runtime context", body_start)
    if end < 0:
        end = summary_doc.find("\n## Grounding", body_start)
    if end < 0:
        return summary_doc[body_start:]
    return summary_doc[body_start:end]


def _event_for_summary(event: dict[str, Any]) -> dict[str, Any] | None:
    event_type = event.get("type")
    if isinstance(event_type, str):
        if event_type in {
            "chat_initial_prompt_delivered",
            "compaction_requested",
            "compaction_summary_created",
            "compaction_old_session_breadcrumb",
        }:
            return None
        return event

    role = event.get("role")
    body = event.get("body")
    created_at = event.get("created_at")
    if role == "user" and isinstance(body, str):
        return {"type": "user_input", "ts": created_at, "text": body}
    if role == "assistant" and isinstance(body, str):
        return {"type": "message_complete", "ts": created_at, "text": body}
    return None


def _last_runtime_status(events: list[dict[str, Any]]) -> str | None:
    status: str | None = None
    for event in events:
        if event.get("type") != "status_change":
            continue
        value = event.get("status")
        if isinstance(value, str):
            status = value
    return status


def _chat_description(record: ChatRecord) -> str:
    chat = record.chat
    link = "none"
    if chat.grounding_kind and chat.grounding_ref:
        link = f"{chat.grounding_kind}:{chat.grounding_ref}"
    working_directory = chat.working_directory or "default"
    return (
        f"Exploratory chat {chat.slug or ''} using {chat.provider}/{chat.model}. "
        f"Linked to: {link}. Working folder: {working_directory}."
    )


def _format_summary_doc(
    *,
    record: ChatRecord,
    reason: CompactionReason,
    old_session_id: str,
    workdir: Path,
    grounding_label: str,
    grounding_details: str,
    summary_body: str,
    transcript_hint: str,
    created_at: datetime,
) -> str:
    chat = record.chat
    lines = [
        f"# Compacted Context for {chat.title}",
        "",
        f"Created: {created_at.isoformat()}",
        f"Reason: {reason}",
        f"Chat: {chat.slug or 'unsaved'} - {chat.title}",
        f"Provider: {chat.provider}",
        f"Model: {chat.model}",
        f"Old provider session/thread: {old_session_id}",
        f"Working directory: {workdir}",
        f"Transcript: {transcript_hint}",
        "",
        "## Compacted Summary",
        _clean_compaction_summary_body(summary_body),
        "",
        "## Runtime context",
        f"Working folder: {grounding_label}",
        grounding_details.strip() or "(not set)",
    ]
    return "\n".join(lines).rstrip() + "\n"


def _format_runtime_context(runtime: Any) -> str:
    working_details = getattr(runtime, "working_details", "")
    link_label = getattr(runtime, "link_label", "none")
    link_details = getattr(runtime, "link_details", "")
    return "\n".join(
        part
        for part in (
            str(working_details).strip(),
            "",
            f"Linked to: {link_label}",
            str(link_details).strip(),
        )
        if part != ""
    )


def _clean_compaction_summary_body(summary_body: str) -> str:
    cleaned = summary_body.strip()
    return cleaned or "(empty summary)"


def _fresh_session_seed(
    *, summary: str, summary_path: str, transcript_path: str
) -> str:
    return (
        "Atelier compacted the previous provider session for this same "
        "exploratory chat.\n\n"
        "You are continuing the same chat. Do not treat this as a new task.\n\n"
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
        "Atelier compacted this exploratory chat.\n\n"
        f"New provider session/thread: {new_session_id}\n"
        f"Summary file: {summary_path}\n"
        f"Full Atelier transcript: {transcript_path}\n\n"
        "If this old session is resumed manually, continue in the new "
        "session/thread instead."
    )


__all__ = [
    "ChatBusy",
    "ChatNotCompactable",
    "ChatNotFound",
    "CompactChatRequest",
    "CompactChatResult",
    "execute",
]
