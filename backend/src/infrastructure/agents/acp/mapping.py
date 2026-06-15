"""ACP ``session/update`` → ``AgentEvent`` mapping.

``AcpUpdateMapper`` is a small state machine fed one typed update at a
time by the adapter's ``session_update`` handler. It owns the per-turn
assembly state the protocol requires:

- message / thought chunks merge by ``message_id`` (a changed id starts
  a new message; the previous one flushes as ``MessageComplete`` /
  ``ThinkingComplete``),
- tool calls merge by ``tool_call_id`` across ``tool_call`` +
  ``tool_call_update`` frames. Emission of the ``ToolCall`` event is
  *deferred* until the call has meaningful arguments — agents (the
  Claude wrapper among them) open with an empty ``raw_input`` and only
  populate it on the first update. Terminal status flushes a
  ``ToolResult``; in-between transitions emit coalesced
  ``ToolCallUpdate`` events,
- ``usage_update`` frames are retained (context fill + cumulative cost)
  and folded into the end-of-turn ``TurnMetrics`` by the adapter.

The mapper never touches the wire — pure translation, fully unit-testable
with hand-built ``acp.schema`` models.
"""

import json
import logging
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any

from acp.schema import (
    AgentMessageChunk,
    AgentPlanUpdate,
    AgentThoughtChunk,
    CurrentModeUpdate,
    ToolCallProgress,
    ToolCallStart,
    UsageUpdate,
)

from src.domain.agents import (
    AgentEvent,
    ArtifactMarker,
    MessageComplete,
    MessageDelta,
    ModeChange,
    PlanUpdate,
    ThinkingComplete,
    ThinkingDelta,
    ToolCall,
    ToolCallUpdate,
    ToolResult,
)
from src.infrastructure.agents.atelier_mcp_tools import (
    marker_payload_for_tool,
    scan_text_for_artifact_markers,
)
from src.infrastructure.agents.tool_canonical import canonicalize_tool

logger = logging.getLogger(__name__)


@dataclass
class _ToolState:
    """Merged view of one tool call across its start/update frames."""

    tool_id: str
    title: str | None = None
    kind: str | None = None
    status: str | None = None
    raw_input: dict[str, Any] | None = None
    provider_tool_name: str | None = None
    locations: tuple[dict[str, Any], ...] | None = None
    diff: dict[str, Any] | None = None
    output_parts: list[str] = field(default_factory=list)
    call_emitted: bool = False
    result_emitted: bool = False


@dataclass
class _UsageState:
    """Latest ``usage_update`` snapshot (cumulative within the session)."""

    used: int = 0
    size: int | None = None
    cost_usd: float | None = None


def _now() -> datetime:
    return datetime.now(UTC)


def _meta_tool_name(meta: dict[str, Any] | None) -> str | None:
    """Fish the provider's real tool name out of ``_meta``.

    Not standardized: the Claude wrapper nests it as
    ``claudeCode.toolName``; other adapters may use a flat ``toolName``.
    One level of nesting is searched — deeper shapes can register here
    as they're discovered.
    """
    if not meta:
        return None
    for value in (meta, *(v for v in meta.values() if isinstance(v, dict))):
        name = value.get("toolName") if isinstance(value, dict) else None
        if isinstance(name, str) and name:
            return name
    return None


def _content_fragments(
    content: list[Any] | None,
) -> tuple[list[str], dict[str, Any] | None, tuple[dict[str, Any], ...] | None]:
    """Split tool-call content into (text parts, structured diff, locations).

    ACP tool content is a list of ``content`` wrappers (ContentBlock),
    ``diff`` items, and ``terminal`` refs. Terminals aren't supported in
    v1 (we don't advertise the capability) but are stringified defensively.
    """
    if not content:
        return [], None, None
    texts: list[str] = []
    diff: dict[str, Any] | None = None
    for item in content:
        item_type = getattr(item, "type", None)
        if item_type == "diff":
            diff = {
                "path": item.path,
                "old_text": item.old_text,
                "new_text": item.new_text,
            }
        elif item_type == "content":
            block = item.content
            text = getattr(block, "text", None)
            if text:
                texts.append(str(text))
        elif item_type == "terminal":
            texts.append(f"[terminal {getattr(item, 'terminal_id', '?')}]")
    return texts, diff, None


def _locations_of(update: Any) -> tuple[dict[str, Any], ...] | None:
    locations = getattr(update, "locations", None)
    if not locations:
        return None
    out: list[dict[str, Any]] = []
    for loc in locations:
        entry: dict[str, Any] = {"path": loc.path}
        if loc.line is not None:
            entry["line"] = loc.line
        out.append(entry)
    return tuple(out)


def _stringify_raw_output(raw: Any) -> str:
    if raw is None:
        return ""
    if isinstance(raw, str):
        return raw
    try:
        return json.dumps(raw)
    except (TypeError, ValueError):
        return str(raw)


class AcpUpdateMapper:
    """Folds a stream of ACP session updates into AgentEvents."""

    def __init__(self) -> None:
        self._message_id: str | None = None
        self._message_parts: list[str] = []
        self._thought_id: str | None = None
        self._thought_parts: list[str] = []
        self._tools: dict[str, _ToolState] = {}
        self.usage = _UsageState()
        self.current_mode_id: str | None = None

    # -- public ------------------------------------------------------------

    def handle(self, update: Any) -> list[AgentEvent]:
        """Map one session update onto zero or more AgentEvents."""
        if isinstance(update, AgentMessageChunk):
            return self._on_message_chunk(update)
        if isinstance(update, AgentThoughtChunk):
            return self._on_thought_chunk(update)
        if isinstance(update, ToolCallStart):
            return self._on_tool_call(update)
        if isinstance(update, ToolCallProgress):
            return self._on_tool_update(update)
        if isinstance(update, AgentPlanUpdate):
            return self._on_plan(update)
        if isinstance(update, CurrentModeUpdate):
            return self._on_mode(update)
        if isinstance(update, UsageUpdate):
            self._on_usage(update)
            return []
        # user_message_chunk (live echo / replay), available_commands_update,
        # config_option_update, session_info_update, anything newer than us:
        # deliberately ignored. Never crash the pump on an unknown frame.
        logger.debug(
            "ignoring ACP session update: %s",
            getattr(update, "session_update", type(update).__name__),
        )
        return []

    def flush_turn(self) -> list[AgentEvent]:
        """End-of-turn flush: complete open messages/thoughts and close
        any tools the agent left dangling (cancelled turns do this)."""
        events = self._flush_thought() + self._flush_message()
        for state in self._tools.values():
            if state.call_emitted and not state.result_emitted:
                state.result_emitted = True
                events.append(
                    ToolResult(
                        ts=_now(),
                        tool_id=state.tool_id,
                        content="\n".join(state.output_parts),
                        is_error=False,
                        diff=state.diff,
                    )
                )
        self._tools.clear()
        return events

    def provider_tool_name_for(self, tool_id: str | None) -> str | None:
        """Return the provider's logical tool name for a live ACP tool call.

        ACP permission requests carry a ``ToolCallUpdate`` whose ``title`` may be
        a human action label (for example a WebSearch query). The earlier
        ``tool_call`` frame can include the actual provider tool name in
        metadata; keep that available so permission prompts are labelled by the
        capability, not the argument.
        """
        if tool_id is None:
            return None
        state = self._tools.get(tool_id)
        if state is None:
            return None
        return state.provider_tool_name

    # -- messages / thoughts -------------------------------------------------

    def _on_message_chunk(self, update: AgentMessageChunk) -> list[AgentEvent]:
        # A thought→text transition is a boundary in BOTH directions: the
        # frontend closes the open bubble on the opposite chunk type, so
        # the mapper must too — otherwise a message that resumes after an
        # interleaved thinking block re-accumulates here and the final
        # MessageComplete carries the full joined text into a *second*
        # frontend bubble (duplicated-response bug, found with claude-acp
        # + haiku where thinking interleaves with text).
        events: list[AgentEvent] = self._flush_thought()
        text = getattr(update.content, "text", None)
        if update.message_id != self._message_id and self._message_parts:
            events.extend(self._flush_message())
        self._message_id = update.message_id
        if text:
            self._message_parts.append(text)
            events.append(MessageDelta(ts=_now(), text=text))
        return events

    def _on_thought_chunk(self, update: AgentThoughtChunk) -> list[AgentEvent]:
        # Mirror of _on_message_chunk: an open message flushes before
        # thinking starts streaming.
        events: list[AgentEvent] = self._flush_message()
        text = getattr(update.content, "text", None)
        if update.message_id != self._thought_id and self._thought_parts:
            events.extend(self._flush_thought())
        self._thought_id = update.message_id
        if text:
            self._thought_parts.append(text)
            events.append(ThinkingDelta(ts=_now(), text=text))
        return events

    def _flush_message(self) -> list[AgentEvent]:
        if not self._message_parts:
            return []
        text = "".join(self._message_parts)
        self._message_parts = []
        self._message_id = None
        events: list[AgentEvent] = [MessageComplete(ts=_now(), text=text)]
        # Same belt-and-suspenders fallback the other adapters carry: a
        # model that emits the ``atelier_artifact`` JSON line in chat
        # instead of calling the MCP tool still gets recorded.
        for payload in scan_text_for_artifact_markers(text):
            events.append(ArtifactMarker(ts=_now(), payload=payload))
        return events

    def _flush_thought(self) -> list[AgentEvent]:
        if not self._thought_parts:
            return []
        text = "".join(self._thought_parts)
        self._thought_parts = []
        self._thought_id = None
        return [ThinkingComplete(ts=_now(), text=text)]

    # -- tools ----------------------------------------------------------------

    def _on_tool_call(self, update: ToolCallStart) -> list[AgentEvent]:
        # A tool call is a message boundary: agents that stream chunks
        # without message ids (codex-acp) would otherwise concatenate
        # the text before and after the tool into one bubble.
        events = self._flush_thought() + self._flush_message()
        state = _ToolState(tool_id=update.tool_call_id)
        self._tools[update.tool_call_id] = state
        self._merge_tool_frame(state, update)
        # Defer emission until the call carries real arguments — the
        # Claude wrapper opens with ``raw_input: {}`` and fills it on the
        # first update. Emitting now would freeze an empty tool card.
        if state.raw_input:
            events.extend(self._emit_tool_call(state))
        return events

    def _on_tool_update(self, update: ToolCallProgress) -> list[AgentEvent]:
        state = self._tools.get(update.tool_call_id)
        if state is None:
            # Update for a call we never saw (replay edge) — synthesize.
            state = _ToolState(tool_id=update.tool_call_id)
            self._tools[update.tool_call_id] = state
        changed = self._merge_tool_frame(state, update)
        events: list[AgentEvent] = []
        if not state.call_emitted:
            events.extend(self._emit_tool_call(state))
            changed = set()  # the emission already carries current state
        if state.status in ("completed", "failed") and not state.result_emitted:
            state.result_emitted = True
            output = _stringify_raw_output(update.raw_output) or "\n".join(
                state.output_parts
            )
            events.append(
                ToolResult(
                    ts=_now(),
                    tool_id=state.tool_id,
                    content=output,
                    is_error=state.status == "failed",
                    diff=state.diff,
                )
            )
            return events
        # Coalesce: only surface frames that change something the
        # frontend renders (status transition, locations, title, kind).
        if changed & {"status", "locations", "title", "kind"}:
            events.append(
                ToolCallUpdate(
                    ts=_now(),
                    tool_id=state.tool_id,
                    status=state.status if "status" in changed else None,
                    title=state.title if "title" in changed else None,
                    kind=state.kind if "kind" in changed else None,
                    locations=state.locations if "locations" in changed else None,
                )
            )
        return events

    def _merge_tool_frame(self, state: _ToolState, update: Any) -> set[str]:
        """Merge one start/update frame into the tool state; return the
        set of field names that actually changed."""
        changed: set[str] = set()
        title = getattr(update, "title", None)
        if title and title != state.title:
            state.title = title
            changed.add("title")
        kind = getattr(update, "kind", None)
        if kind and kind != state.kind:
            state.kind = kind
            changed.add("kind")
        status = getattr(update, "status", None)
        if status and status != state.status:
            state.status = status
            changed.add("status")
        raw_input = getattr(update, "raw_input", None)
        if isinstance(raw_input, dict) and raw_input and raw_input != state.raw_input:
            state.raw_input = raw_input
            changed.add("raw_input")
        meta_name = _meta_tool_name(getattr(update, "field_meta", None))
        if meta_name:
            state.provider_tool_name = meta_name
        locations = _locations_of(update)
        if locations and locations != state.locations:
            state.locations = locations
            changed.add("locations")
        texts, diff, _ = _content_fragments(getattr(update, "content", None))
        if texts:
            state.output_parts.extend(texts)
        if diff is not None:
            state.diff = diff
        return changed

    def _emit_tool_call(self, state: _ToolState) -> list[AgentEvent]:
        state.call_emitted = True
        provider_name = state.provider_tool_name or state.title or "tool"
        raw_args = state.raw_input or {}
        events: list[AgentEvent] = []
        # Atelier artifact-tool calls produce a marker on the side; the
        # regular ToolCall still flows so the transcript shows the
        # agent's exact invocation (same contract as the other adapters).
        marker_payload = marker_payload_for_tool(provider_name, dict(raw_args))
        if marker_payload is not None:
            events.append(ArtifactMarker(ts=_now(), payload=marker_payload))
        canon_name, canon_args = canonicalize_tool(provider_name, dict(raw_args))
        events.append(
            ToolCall(
                ts=_now(),
                tool_id=state.tool_id,
                name=canon_name,
                arguments=canon_args,
                kind=state.kind,
                title=state.title,
                locations=state.locations,
            )
        )
        return events

    # -- plan / mode / usage ----------------------------------------------------

    def _on_plan(self, update: AgentPlanUpdate) -> list[AgentEvent]:
        # The frontend treats plan rows as bubble boundaries; flush open
        # buffers so streamed text doesn't split across the plan.
        events = self._flush_thought() + self._flush_message()
        entries = tuple(
            {
                "content": entry.content,
                "priority": entry.priority,
                "status": entry.status,
            }
            for entry in update.entries
        )
        events.append(PlanUpdate(ts=_now(), entries=entries))
        return events

    def _on_mode(self, update: CurrentModeUpdate) -> list[AgentEvent]:
        if update.current_mode_id == self.current_mode_id:
            return []
        self.current_mode_id = update.current_mode_id
        events = self._flush_thought() + self._flush_message()
        events.append(ModeChange(ts=_now(), mode_id=update.current_mode_id))
        return events

    def _on_usage(self, update: UsageUpdate) -> None:
        self.usage.used = update.used
        self.usage.size = update.size
        cost = getattr(update, "cost", None)
        if cost is not None and getattr(cost, "currency", None) == "USD":
            self.usage.cost_usd = float(cost.amount)


__all__ = ["AcpUpdateMapper"]
