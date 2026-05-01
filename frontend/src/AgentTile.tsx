import { type FormEvent, useEffect, useMemo, useRef, useState } from "react";

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
  | { kind: "user"; key: number; text: string }
  | { kind: "tool_call"; key: number; name: string; args: string }
  | { kind: "tool_result"; key: number; content: string; isError: boolean }
  | { kind: "status"; key: number; status: string }
  | { kind: "artifact"; key: number; payload: unknown }
  | { kind: "error"; key: number; message: string };

function groupEvents(events: AgentEvent[]): RenderUnit[] {
  const out: RenderUnit[] = [];
  let pending:
    | { kind: "assistant"; key: number; text: string; complete: boolean }
    | null = null;

  for (const ev of events) {
    if (ev.type === "message_delta") {
      const text = stringField(ev, "text");
      if (pending) {
        pending.text += text;
      } else {
        pending = { kind: "assistant", key: ev.seq, text, complete: false };
        out.push(pending);
      }
    } else if (ev.type === "message_complete") {
      const text = stringField(ev, "text");
      if (pending) {
        pending.text = text;
        pending.complete = true;
        pending = null;
      } else {
        out.push({ kind: "assistant", key: ev.seq, text, complete: true });
      }
    } else {
      pending = null;
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
  if (last.kind === "assistant") return last.text;
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
          {unit.text}
          {!unit.complete && <span className="cursor">▍</span>}
        </div>
      );
    case "user":
      return <div className="msg msg-user">{unit.text}</div>;
    case "tool_call":
      return (
        <div className="msg msg-tool">
          ▸ {unit.name}({unit.args})
        </div>
      );
    case "tool_result":
      return (
        <div className={`msg msg-tool${unit.isError ? " msg-error" : ""}`}>
          {"  → "}
          {unit.content}
        </div>
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
