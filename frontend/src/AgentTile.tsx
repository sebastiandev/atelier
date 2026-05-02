import {
  type FormEvent,
  type KeyboardEvent as ReactKeyboardEvent,
  useEffect,
  useMemo,
  useRef,
  useState,
} from "react";

import { PERSONA_GLYPH, type Persona } from "./api";
import { MarkdownText } from "./MarkdownText";
import {
  type AgentEvent,
  useAgentStream,
} from "./useAgentStream";

const COMPOSER_MAX_HEIGHT = 200;

type AgentTileProps = {
  agentSlug: string;
  mode?: "page" | "tile";
  persona?: Persona;
  agentName?: string;
};

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
export function AgentTile({
  agentSlug,
  mode = "page",
  persona,
  agentName,
}: AgentTileProps) {
  const { events, status, sendInput } = useAgentStream(agentSlug);
  const [draft, setDraft] = useState("");
  // Optimistic "thinking" between Send and the first status_change event
  // back from the adapter, so the dot reacts instantly.
  const [optimisticThinking, setOptimisticThinking] = useState(false);
  const transcriptRef = useRef<HTMLDivElement>(null);
  const textareaRef = useRef<HTMLTextAreaElement>(null);

  const units = useMemo(() => groupEvents(events), [events]);
  const agentStatus = useMemo(() => latestStatus(events), [events]);

  // Auto-scroll to bottom on new content.
  useEffect(() => {
    const el = transcriptRef.current;
    if (el) el.scrollTop = el.scrollHeight;
  }, [units.length, lastUnitText(units)]);

  // Auto-grow textarea up to COMPOSER_MAX_HEIGHT.
  useEffect(() => {
    const el = textareaRef.current;
    if (!el) return;
    el.style.height = "auto";
    el.style.height = Math.min(el.scrollHeight, COMPOSER_MAX_HEIGHT) + "px";
  }, [draft]);

  // Clear optimistic thinking once a real status_change lands.
  useEffect(() => {
    if (!optimisticThinking) return;
    const last = events[events.length - 1];
    if (last && last.type === "status_change") setOptimisticThinking(false);
  }, [events, optimisticThinking]);

  function submit() {
    const text = draft.trim();
    if (!text) return;
    sendInput(text);
    setDraft("");
    setOptimisticThinking(true);
  }

  function handleSubmit(e: FormEvent) {
    e.preventDefault();
    submit();
  }

  function handleKeyDown(e: ReactKeyboardEvent<HTMLTextAreaElement>) {
    if (e.key === "Enter" && !e.shiftKey && !e.metaKey && !e.ctrlKey) {
      e.preventDefault();
      submit();
    }
  }

  const [maximized, setMaximized] = useState(false);

  const dotStatus = optimisticThinking ? "thinking" : agentStatus;
  const isStopped = status === "stopped";
  const composerDisabled = isStopped || status === "error";
  const tileClass = `agent-tile mode-${mode}` + (maximized ? " maximized" : "");
  const title = agentName || agentSlug;

  return (
    <div className={tileClass} data-persona={persona}>
      <header>
        {persona && <span className="persona-pip">{PERSONA_GLYPH[persona]}</span>}
        <span className="status-dot" data-status={dotStatus} />
        <h2>{title}</h2>
        {persona && agentName && <span className="agent-slug mono">{agentSlug}</span>}
        <span className="conn-status" data-conn-status={status}>{status}</span>
        <div className="tile-controls">
          <button
            type="button"
            className="tile-ctl"
            title="Hand off to new agent — coming in Sprint 3"
            disabled
          >
            <HandoffIcon />
          </button>
          <button
            type="button"
            className="tile-ctl"
            title="Minimize to sidebar — coming with persistence"
            disabled
          >
            <MinusIcon />
          </button>
          <button
            type="button"
            className="tile-ctl"
            title={maximized ? "Restore" : "Maximize"}
            onClick={() => setMaximized((m) => !m)}
          >
            {maximized ? <RestoreIcon /> : <MaxIcon />}
          </button>
          <button
            type="button"
            className="tile-ctl"
            title="Close — coming with persistence"
            disabled
          >
            <CloseIcon />
          </button>
        </div>
      </header>
      {isStopped && (
        <div className="tile-banner">
          Agent isn't running. The supervisor lost its session — launch a new agent or restart the backend.
        </div>
      )}
      <div className="transcript" ref={transcriptRef}>
        {units.map((unit) => (
          <Unit key={unit.key} unit={unit} />
        ))}
      </div>
      <form className="composer" onSubmit={handleSubmit}>
        <textarea
          ref={textareaRef}
          value={draft}
          onChange={(e) => setDraft(e.target.value)}
          onKeyDown={handleKeyDown}
          placeholder={
            isStopped
              ? "Agent stopped — launch a new one to continue"
              : "Message the agent — Enter sends, Shift+Enter for newline"
          }
          rows={1}
          disabled={composerDisabled}
          autoFocus={mode === "page"}
        />
        <div className="composer-actions">
          <button
            type="button"
            className="composer-tool"
            disabled
            title="Add context — coming in Sprint 3"
          >
            + Add context
          </button>
          <span className="spacer" />
          <button
            type="submit"
            className="composer-send"
            disabled={composerDisabled || !draft.trim()}
          >
            Send
          </button>
        </div>
      </form>
    </div>
  );
}

// ---------------------------------------------------------------------------
// Tile control icons (inline SVG — no icon system yet)
// ---------------------------------------------------------------------------

function HandoffIcon() {
  return (
    <svg viewBox="0 0 16 16" width="13" height="13" aria-hidden>
      <path
        d="M3 5h7m0 0L7 2m3 3L7 8M13 11H6m0 0l3-3m-3 3l3 3"
        stroke="currentColor"
        strokeWidth="1.4"
        fill="none"
        strokeLinecap="round"
        strokeLinejoin="round"
      />
    </svg>
  );
}
function MinusIcon() {
  return (
    <svg viewBox="0 0 16 16" width="13" height="13" aria-hidden>
      <path d="M3 8h10" stroke="currentColor" strokeWidth="1.4" strokeLinecap="round" />
    </svg>
  );
}
function MaxIcon() {
  return (
    <svg viewBox="0 0 16 16" width="13" height="13" aria-hidden>
      <rect
        x="3"
        y="3"
        width="10"
        height="10"
        rx="1"
        stroke="currentColor"
        strokeWidth="1.4"
        fill="none"
      />
    </svg>
  );
}
function RestoreIcon() {
  return (
    <svg viewBox="0 0 16 16" width="13" height="13" aria-hidden>
      <rect x="3" y="5" width="8" height="8" rx="1" stroke="currentColor" strokeWidth="1.4" fill="none" />
      <path d="M5 5V3h8v8h-2" stroke="currentColor" strokeWidth="1.4" fill="none" strokeLinecap="round" />
    </svg>
  );
}
function CloseIcon() {
  return (
    <svg viewBox="0 0 16 16" width="13" height="13" aria-hidden>
      <path
        d="M4 4l8 8M12 4l-8 8"
        stroke="currentColor"
        strokeWidth="1.4"
        strokeLinecap="round"
      />
    </svg>
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
