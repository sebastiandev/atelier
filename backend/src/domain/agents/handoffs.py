"""HandoffService — generate a checkpoint doc and persist a Handoff row.

When an agent has done its piece of the work and the user wants a fresh
agent to continue, the handoff captures what's worth remembering: the
goal, the decisions made along the way, what's still open, the files
that matter, and any blockers. The new agent boots into a worktree
forked from the source's, with this doc as its first message.

The summarizer is a port: a single callable that turns recent transcript
events into a Markdown body. The default Anthropic-backed implementation
lives in ``infrastructure/summarizer/``; tests stub the port directly.
A structural fallback (no LLM) is provided so the feature works without
an API key — useful for offline dev and tests.

This module owns the orchestrator (``build_handoff``); the supervisor
isn't involved — handoffs are a synchronous user-initiated request, not
an event-stream side-effect.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime
from typing import Any, Protocol

from src.domain.models import Handoff
from src.domain.workstore.dtos import RecordHandoffRequest
from src.domain.workstore.ports import TranscriptLog, WorkStore

# Cap the summarizer's input. 200 events covers a typical session
# without blowing the context window. If the source has a longer history,
# we keep the most recent slice — older events are usually superseded.
TRANSCRIPT_EVENT_CAP = 200


@dataclass(frozen=True)
class SummaryContext:
    """Inputs the summarizer can use to frame the doc — beyond the raw
    transcript. Kept narrow so the port stays cheap to call."""

    work_name: str
    work_description: str
    source_agent_name: str
    source_agent_role: str


class Summarizer(Protocol):
    def __call__(
        self, events: list[dict[str, Any]], context: SummaryContext
    ) -> str: ...


@dataclass(frozen=True)
class BuildHandoffRequest:
    work_slug: str
    source_agent_slug: str


def build_handoff(
    req: BuildHandoffRequest,
    *,
    workstore: WorkStore,
    transcript_log: TranscriptLog,
    summarizer: Summarizer,
    clock: Callable[[], datetime],
) -> Handoff:
    """Generate a handoff doc + persist a Handoff row.

    Steps:
      1. Resolve source agent (404 → ValueError).
      2. Read last ``TRANSCRIPT_EVENT_CAP`` events from disk via the
         transcript log port.
      3. Call the summarizer with (events, context).
      4. Persist via ``WorkStore.record_handoff`` — the workstore writes
         the file via WorkspaceFiles and inserts the SQL row inside its
         own lock, so we don't touch persistence here.

    Target is fixed to ``new-agent`` for v1 (handoff-to-existing deferred).
    """
    work = workstore.get_work(req.work_slug)
    if work is None:
        raise ValueError(f"work not found: {req.work_slug}")
    source = next(
        (
            a
            for a in workstore.list_agents_for_work(req.work_slug)
            if a.slug == req.source_agent_slug
        ),
        None,
    )
    if source is None:
        raise ValueError(f"source agent not found: {req.source_agent_slug}")

    events = list(
        transcript_log.read_from_cursor(req.work_slug, req.source_agent_slug, 0)
    )
    # Tail the cap — most-recent events are the most informative for the
    # next agent. ``read_from_cursor`` returns in seq order so a slice from
    # the end gives us the last N.
    if len(events) > TRANSCRIPT_EVENT_CAP:
        events = events[-TRANSCRIPT_EVENT_CAP:]

    doc_text = summarizer(
        events,
        SummaryContext(
            work_name=work.work.name,
            work_description=work.work.description,
            source_agent_name=source.name,
            source_agent_role=source.role,
        ),
    )

    timestamp = clock().strftime("%Y%m%d-%H%M%S")
    filename = f"{req.source_agent_slug}-handoff-{timestamp}.md"

    return workstore.record_handoff(
        RecordHandoffRequest(
            work_slug=req.work_slug,
            source_agent_slug=req.source_agent_slug,
            doc_text=doc_text,
            doc_filename=filename,
            target_dialog="new-agent",
        )
    )


# ---------------------------------------------------------------------------
# Structural fallback summarizer — no LLM required.
# ---------------------------------------------------------------------------


def structural_summarizer(
    events: list[dict[str, Any]], context: SummaryContext
) -> str:
    """No-LLM fallback. Pulls structured signal from event types and
    formats it into the spec's five-section template.

    Used when no Anthropic API key is configured, in tests, and as the
    backstop if the LLM call fails. Quality is mechanical — works best
    when the agent has emitted clear ToolCalls and Errors. The user can
    edit the doc before the new agent reads it.
    """
    user_inputs = [e for e in events if e.get("type") == "user_input"]
    tool_calls = [e for e in events if e.get("type") == "tool_call"]
    errors = [e for e in events if e.get("type") == "error"]
    permission_denies = [
        e
        for e in events
        if e.get("type") == "permission_decision" and e.get("decision") == "deny"
    ]

    file_paths = sorted(
        {
            path
            for tc in tool_calls
            if (path := _path_from_tool_call(tc)) is not None
        }
    )

    lines: list[str] = []
    lines.append(f"# Handoff from {context.source_agent_name}")
    lines.append("")
    lines.append(f"**Work:** {context.work_name}")
    lines.append(f"**Source role:** {context.source_agent_role}")
    lines.append("")

    lines.append("## Goal")
    lines.append(context.work_description.strip() or "(not set)")
    lines.append("")

    lines.append("## Decisions")
    if user_inputs:
        lines.append("Latest user instructions to the source agent:")
        for ev in user_inputs[-5:]:
            text = str(ev.get("text", "")).strip()
            if text:
                lines.append(f"- {_truncate(text, 240)}")
    else:
        lines.append("_No user instructions were recorded in the captured slice._")
    lines.append("")

    lines.append("## Open questions")
    lines.append(
        "_The structural summarizer can't infer open questions. "
        "Edit this section before the new agent reads it._"
    )
    lines.append("")

    lines.append("## Key files")
    if file_paths:
        for p in file_paths[:25]:
            lines.append(f"- `{p}`")
    else:
        lines.append("_No file-touching tool calls in the captured slice._")
    lines.append("")

    lines.append("## Blockers")
    if errors or permission_denies:
        for ev in errors[-5:]:
            msg = str(ev.get("message", "")).strip()
            if msg:
                lines.append(f"- Error: {_truncate(msg, 240)}")
        for ev in permission_denies[-5:]:
            lines.append(
                f"- User denied tool: {ev.get('tool_name', '(unknown)')}"
            )
    else:
        lines.append("_No errors or denied permissions in the captured slice._")
    lines.append("")

    return "\n".join(lines)


def _path_from_tool_call(event: dict[str, Any]) -> str | None:
    """Best-effort path extraction from a ToolCall event. Looks at the
    common ``path`` / ``file_path`` argument keys used by the Edit/Write/
    Read tools across providers; ignores anything else."""
    args = event.get("arguments") or {}
    if not isinstance(args, dict):
        return None
    for key in ("file_path", "path"):
        value = args.get(key)
        if isinstance(value, str) and value.strip():
            return value
    return None


def _truncate(text: str, limit: int) -> str:
    text = " ".join(text.split())
    if len(text) <= limit:
        return text
    return text[: limit - 1].rstrip() + "…"


__all__ = [
    "TRANSCRIPT_EVENT_CAP",
    "BuildHandoffRequest",
    "Summarizer",
    "SummaryContext",
    "build_handoff",
    "structural_summarizer",
]
