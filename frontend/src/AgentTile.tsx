import { type FormEvent, useEffect, useMemo, useRef, useState } from "react";

import { MarkdownText } from "./MarkdownText";
import {
  type AgentEvent,
  useAgentStream,
} from "./useAgentStream";

/**
 * Streaming agent view (walking-skeleton scope).
 *
 * Renders every event from useAgentStream as a transcript line. Streaming
 * deltas are accumulated into one growing assistant message so the user
 * sees the message build up, not seven individual chunks. Other event
 * types (tool_call, tool_result, status_change, artifact_marker, error,
 * user_input) get their own line with type-specific formatting.
 *
 * Phase B will layer markdown rendering, persona theming, and transcript
 * virtualization on top of this.
 */
export function AgentTile({ agentSlug }: { agentSlug: string }) {
  const { events, status, sendInput } = useAgentStream(agentSlug);
  const [draft, setDraft] = useState("");
  const transcriptRef = useRef<HTMLDivElement>(null);

  const units = useMemo(() => groupEvents(events), [events]);
  const agentStatus = useMemo(() => latestStatus(events), [events]);

  // Auto-scroll to bottom on new content.
  useEffect(() => {
    const el = transcriptRef.current;
    if (el) el.scrollTop = el.scrollHeight;
  }, [units.length, lastUnitText(units)]);

  function handleSubmit(e: FormEvent) {
    e.preventDefault();
    const text = draft.trim();
    if (!text) return;
    sendInput(text);
    setDraft("");
  }

  return (
    <div className="agent-tile">
      <header>
        <span className="status-dot" data-status={agentStatus} />
        <h2>{agentSlug}</h2>
        <span className="conn-status">{status}</span>
      </header>
      <div className="transcript" ref={transcriptRef}>
        {units.map((unit) => (
          <Unit key={unit.key} unit={unit} />
        ))}
      </div>
      <form className="composer" onSubmit={handleSubmit}>
        <input
          type="text"
          value={draft}
          onChange={(e) => setDraft(e.target.value)}
          placeholder="Type a message…"
          autoFocus
        />
        <button type="submit" disabled={!draft.trim()}>
          Send
        </button>
      </form>
    </div>
  );
}

// ---------------------------------------------------------------------------
// Event grouping
// ---------------------------------------------------------------------------

type RenderUnit =
  | { kind: "assistant"; key: number; text: string; complete: boolean }
  | { kind: "thinking"; key: number; text: string; complete: boolean }
  | { kind: "user"; key: number; text: string }
  | { kind: "tool_call"; key: number; name: string; args: string }
  | { kind: "tool_result"; key: number; content: string; isError: boolean }
  | { kind: "status"; key: number; status: string }
  | { kind: "artifact"; key: number; payload: unknown }
  | { kind: "error"; key: number; message: string };

function groupEvents(events: AgentEvent[]): RenderUnit[] {
  const out: RenderUnit[] = [];
  let pendingAssistant:
    | { kind: "assistant"; key: number; text: string; complete: boolean }
    | null = null;
  let pendingThinking:
    | { kind: "thinking"; key: number; text: string; complete: boolean }
    | null = null;

  for (const ev of events) {
    if (ev.type === "message_delta") {
      const text = stringField(ev, "text");
      pendingThinking = null;
      if (pendingAssistant) {
        pendingAssistant.text += text;
      } else {
        pendingAssistant = { kind: "assistant", key: ev.seq, text, complete: false };
        out.push(pendingAssistant);
      }
    } else if (ev.type === "message_complete") {
      const text = stringField(ev, "text");
      pendingThinking = null;
      if (pendingAssistant) {
        pendingAssistant.text = text;
        pendingAssistant.complete = true;
        pendingAssistant = null;
      } else {
        out.push({ kind: "assistant", key: ev.seq, text, complete: true });
      }
    } else if (ev.type === "thinking_delta") {
      const text = stringField(ev, "text");
      pendingAssistant = null;
      if (pendingThinking) {
        pendingThinking.text += text;
      } else {
        pendingThinking = { kind: "thinking", key: ev.seq, text, complete: false };
        out.push(pendingThinking);
      }
    } else if (ev.type === "thinking_complete") {
      const text = stringField(ev, "text");
      pendingAssistant = null;
      if (pendingThinking) {
        pendingThinking.text = text;
        pendingThinking.complete = true;
        pendingThinking = null;
      } else {
        out.push({ kind: "thinking", key: ev.seq, text, complete: true });
      }
    } else {
      pendingAssistant = null;
      pendingThinking = null;
      const unit = renderUnitFor(ev);
      if (unit) out.push(unit);
    }
  }

  return out;
}

function renderUnitFor(ev: AgentEvent): RenderUnit | null {
  switch (ev.type) {
    case "user_input":
      return { kind: "user", key: ev.seq, text: stringField(ev, "text") };
    case "tool_call":
      return {
        kind: "tool_call",
        key: ev.seq,
        name: stringField(ev, "name"),
        args: JSON.stringify(ev.arguments ?? {}),
      };
    case "tool_result":
      return {
        kind: "tool_result",
        key: ev.seq,
        content: stringField(ev, "content"),
        isError: ev.is_error === true,
      };
    case "status_change":
      return {
        kind: "status",
        key: ev.seq,
        status: stringField(ev, "status"),
      };
    case "artifact_marker":
      return { kind: "artifact", key: ev.seq, payload: ev.payload };
    case "error":
      return {
        kind: "error",
        key: ev.seq,
        message: stringField(ev, "message"),
      };
    default:
      return null;
  }
}

function prettyJson(raw: string): string {
  try {
    return JSON.stringify(JSON.parse(raw), null, 2);
  } catch {
    return raw;
  }
}

function stringField(ev: AgentEvent, key: string): string {
  const value = ev[key];
  return typeof value === "string" ? value : "";
}

function latestStatus(events: AgentEvent[]): string {
  for (let i = events.length - 1; i >= 0; i--) {
    if (events[i].type === "status_change") {
      return stringField(events[i], "status");
    }
  }
  return "idle";
}

function lastUnitText(units: RenderUnit[]): string {
  const last = units[units.length - 1];
  if (!last) return "";
  if (last.kind === "assistant" || last.kind === "thinking") return last.text;
  return String(last.key);
}

// ---------------------------------------------------------------------------
// Renderers
// ---------------------------------------------------------------------------

function Unit({ unit }: { unit: RenderUnit }) {
  switch (unit.kind) {
    case "assistant":
      return (
        <div className="msg msg-assistant">
          <MarkdownText text={unit.text} />
          {!unit.complete && <span className="cursor">▍</span>}
        </div>
      );
    case "thinking":
      return (
        <details className="msg msg-thinking">
          <summary>
            💭 thinking{!unit.complete && <span className="cursor">▍</span>}
          </summary>
          <div className="thinking-body">
            <MarkdownText text={unit.text} />
          </div>
        </details>
      );
    case "user":
      return <div className="msg msg-user">{unit.text}</div>;
    case "tool_call":
      return (
        <details className="msg msg-tool">
          <summary>
            ▸ <span className="tool-name">{unit.name}</span>
          </summary>
          <MarkdownText text={"```json\n" + prettyJson(unit.args) + "\n```"} />
        </details>
      );
    case "tool_result":
      return (
        <details className={`msg msg-tool${unit.isError ? " msg-error" : ""}`}>
          <summary>{unit.isError ? "  ⚠ result" : "  → result"}</summary>
          <pre className="tool-result-body">{unit.content}</pre>
        </details>
      );
    case "status":
      return <div className="msg msg-status">[{unit.status}]</div>;
    case "artifact":
      return (
        <div className="msg msg-artifact">⚑ {JSON.stringify(unit.payload)}</div>
      );
    case "error":
      return <div className="msg msg-error">{unit.message}</div>;
  }
}
