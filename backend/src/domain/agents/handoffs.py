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

import json
from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime
from typing import Any, Protocol

from src.domain.models import Handoff
from src.domain.workstore.dtos import RecordHandoffRequest
from src.domain.workstore.ports import TranscriptLog, WorkStore

# Cap the summarizer's input by serialized size, not event count — a
# session of 50 huge tool results matters more than 50 status pings.
# 750K chars ≈ 200K tokens at ~3.75 chars/token, which sits comfortably
# under the 200K-token context window of the smallest mainstream model
# we'd target for summarization. Headroom covers the system prompt and
# the model's response. We tail the cap (keep most recent events) since
# they're the most informative for the next agent.
TRANSCRIPT_CHAR_CAP = 750_000

SUMMARY_SYSTEM_PROMPT = """\
You are summarizing one agent's recent transcript so a fresh agent can pick \
up the work without losing context. Output Markdown only — no preamble or \
sign-off. Use these sections, in this order:

## Goal
## Decisions
## Open questions (only when there are actual unresolved questions)
## Key files
## Blockers

Be concrete. Preserve recent user decisions, corrections, file names, \
commands, and implementation constraints. If Key files or Blockers has \
nothing to report, say so in one sentence rather than padding. Omit Open \
questions entirely when there are no actual unresolved questions."""


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
      2. Read the source agent's full transcript via the log port and
         tail-trim to ``TRANSCRIPT_CHAR_CAP`` of serialized payload —
         the entire conversation goes to the summarizer when it fits.
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

    events = _trim_to_char_cap(
        list(
            transcript_log.read_from_cursor(
                req.work_slug, req.source_agent_slug, 0
            )
        ),
        TRANSCRIPT_CHAR_CAP,
    )

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


def format_summary_prompt(
    events: list[dict[str, Any]], context: SummaryContext
) -> str:
    header = (
        f"Work: {context.work_name}\n"
        f"Description: {context.work_description}\n"
        f"Source agent: {context.source_agent_name} "
        f"({context.source_agent_role})\n\n"
    )
    transcript_lines: list[str] = []
    for ev in events:
        transcript_lines.append(_event_to_line(ev))
    return (
        f"{header}"
        "If a [previous_compaction_summary] entry is present, treat it as the "
        "cumulative state before the latest compaction boundary. Merge it with "
        "the later transcript events; do not quote or nest it verbatim.\n\n"
        f"Transcript ({len(events)} events, oldest first):\n\n"
        + "\n".join(transcript_lines)
    )


def _event_to_line(ev: dict[str, Any]) -> str:
    """Compact per-event projection. Drops fields the summarizer doesn't
    need (seq, ts) and keeps the type + the meaningful text/payload."""
    t = ev.get("type")
    if t == "user_input":
        return f"[user] {ev.get('text', '')}"
    if t == "message_complete":
        return f"[agent] {ev.get('text', '')}"
    if t == "previous_compaction_summary":
        return f"[previous_compaction_summary]\n{ev.get('content', '')}"
    if t == "tool_call":
        name = ev.get("name", "?")
        args = ev.get("arguments") or {}
        return f"[tool_call:{name}] {args}"
    if t == "tool_result":
        is_err = " (error)" if ev.get("is_error") else ""
        return f"[tool_result{is_err}] {ev.get('content', '')}"
    if t == "error":
        return f"[error] {ev.get('message', '')}"
    if t == "permission_decision":
        return (
            f"[permission_decision] "
            f"{ev.get('tool_name', '?')} -> {ev.get('decision', '?')}"
        )
    if t == "artifact_recorded":
        artifact = ev.get("artifact") or {}
        return (
            f"[artifact_recorded] {artifact.get('type', '?')} "
            f"{artifact.get('title', '')} ({artifact.get('status', '')})"
        )
    return f"[{t}]"


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
    events = _dedupe_events(events)
    user_inputs = [e for e in events if e.get("type") == "user_input"]
    agent_messages = [e for e in events if e.get("type") == "message_complete"]
    tool_calls = [e for e in events if e.get("type") == "tool_call"]
    errors = [e for e in events if e.get("type") == "error"]
    previous_summaries = [
        str(e.get("content", "")).strip()
        for e in events
        if e.get("type") == "previous_compaction_summary"
        and str(e.get("content", "")).strip()
    ]
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
    if previous_summaries:
        lines.append("Previous compacted context:")
        lines.append(_truncate(previous_summaries[-1], 1400))
        lines.append("")
    if user_inputs:
        lines.append("Latest user instructions to the source agent:")
        for text in _unique_tail_texts(user_inputs, "text", limit=5):
            if text:
                lines.append(f"- {_truncate(text, 240)}")
    else:
        lines.append("_No user instructions were recorded in the captured slice._")
    recent_agent_messages = _unique_tail_texts(agent_messages, "text", limit=5)
    if recent_agent_messages:
        lines.append("")
        lines.append("Recent agent findings and handoff points:")
        for text in recent_agent_messages:
            lines.append(f"- {_truncate(text, 1400)}")
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


def _trim_to_char_cap(
    events: list[dict[str, Any]], cap: int
) -> list[dict[str, Any]]:
    """Keep the tail of ``events`` whose total serialized size is ≤ ``cap``.

    Walk from the end backwards, accumulating events until the next one
    would push us over. Always include at least one event so a single
    huge final event still produces a (truncated by the LLM if needed)
    summary rather than an empty input. Reverse at the end so callers
    receive seq-order.
    """
    selected: list[dict[str, Any]] = []
    total = 0
    for ev in reversed(events):
        size = len(json.dumps(ev, ensure_ascii=False))
        if total + size > cap and selected:
            break
        selected.append(ev)
        total += size
    selected.reverse()
    return selected


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


def _dedupe_events(events: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Drop exact replay duplicates while preserving first-seen order.

    CLI catch-up can replay already-recorded events with new seq/timestamp
    values. For a structural summary, repeated semantic content is noise:
    it crowds out the actual final decisions.
    """
    seen: set[tuple[str, str]] = set()
    out: list[dict[str, Any]] = []
    for event in events:
        key = _event_semantic_key(event)
        if key in seen:
            continue
        seen.add(key)
        out.append(event)
    return out


def _event_semantic_key(event: dict[str, Any]) -> tuple[str, str]:
    event_type = str(event.get("type", ""))
    if event_type in {"user_input", "message_complete", "error"}:
        return event_type, _normalize_text(str(event.get("text") or event.get("message") or ""))
    if event_type == "tool_call":
        return event_type, json.dumps(
            {
                "name": event.get("name"),
                "arguments": event.get("arguments"),
            },
            sort_keys=True,
            ensure_ascii=False,
        )
    if event_type == "tool_result":
        return event_type, _normalize_text(str(event.get("content") or ""))
    return event_type, json.dumps(event, sort_keys=True, ensure_ascii=False)


def _unique_tail_texts(
    events: list[dict[str, Any]], field: str, *, limit: int
) -> list[str]:
    selected: list[str] = []
    seen: set[str] = set()
    for event in reversed(events):
        text = str(event.get(field, "")).strip()
        key = _normalize_text(text)
        if not key or key in seen:
            continue
        seen.add(key)
        selected.append(text)
        if len(selected) >= limit:
            break
    selected.reverse()
    return selected


def _normalize_text(text: str) -> str:
    return " ".join(text.split())


def _truncate(text: str, limit: int) -> str:
    text = _normalize_text(text)
    if len(text) <= limit:
        return text
    return text[: limit - 1].rstrip() + "…"


__all__ = [
    "SUMMARY_SYSTEM_PROMPT",
    "TRANSCRIPT_CHAR_CAP",
    "BuildHandoffRequest",
    "Summarizer",
    "SummaryContext",
    "build_handoff",
    "format_summary_prompt",
    "structural_summarizer",
]
