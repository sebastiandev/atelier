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
  type PendingPermission,
  type PermissionDecision,
  useAgentStream,
} from "./useAgentStream";

const COMPOSER_MAX_HEIGHT = 200;

type AgentTileProps = {
  agentSlug: string;
  mode?: "page" | "tile";
  persona?: Persona;
  agentName?: string;
  provider?: string;
  model?: string;
  onClose?: () => void;
  /** Hand the agent off to the user's terminal CLI. The supervisor's
   *  SDK process is stopped server-side; this callback is responsible
   *  for any client-side cleanup (typically: close-to-rail, surface a
   *  toast on the launch result). When omitted, the detach button is
   *  hidden — the parent didn't wire it for this surface. */
  onDetach?: () => void;
  /** Open the agent's worktree (or source folder fallback) in the OS
   *  file browser. The path is shown in the tooltip so the user can
   *  also copy it from there. When omitted, the folder button is
   *  hidden. */
  onRevealWorktree?: () => void;
  /** The worktree path (or source-folder fallback) — used purely for
   *  the reveal button's tooltip. */
  worktreePath?: string;
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
  provider,
  model,
  onClose,
  onDetach,
  onRevealWorktree,
  worktreePath,
}: AgentTileProps) {
  const {
    events,
    status,
    sendInput,
    sendStop,
    sendPermission,
    pendingPermissions,
  } = useAgentStream(agentSlug);
  const [draft, setDraft] = useState("");
  // Optimistic "thinking" between Send and the first status_change event
  // back from the adapter, so the dot reacts instantly.
  const [optimisticThinking, setOptimisticThinking] = useState(false);
  const transcriptRef = useRef<HTMLDivElement>(null);
  const textareaRef = useRef<HTMLTextAreaElement>(null);

  const units = useMemo(() => groupEvents(events), [events]);
  const agentStatus = useMemo(() => latestStatus(events), [events]);
  const lastMetrics = useMemo(() => latestMetrics(events), [events]);

  // Auto-scroll to bottom on new content.
  useEffect(() => {
    const el = transcriptRef.current;
    if (el) el.scrollTop = el.scrollHeight;
  }, [units.length, lastUnitText(units)]);

  // Auto-grow textarea up to COMPOSER_MAX_HEIGHT. Show the scrollbar
  // only once content exceeds the cap — the CSS default is `hidden` so
  // the macOS overlay scrollbar doesn't paint a sliver in the empty
  // state.
  useEffect(() => {
    const el = textareaRef.current;
    if (!el) return;
    el.style.height = "auto";
    const natural = el.scrollHeight;
    el.style.height = Math.min(natural, COMPOSER_MAX_HEIGHT) + "px";
    el.style.overflowY = natural > COMPOSER_MAX_HEIGHT ? "auto" : "hidden";
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
      return;
    }
    // Plain Esc while the agent is producing a turn = "stop". Modifier
    // combinations (Shift/Cmd/Ctrl+Esc) are reserved for the maximize
    // exit shortcut handled at the window level.
    if (
      e.key === "Escape" &&
      !e.shiftKey &&
      !e.metaKey &&
      !e.ctrlKey &&
      dotStatus === "thinking"
    ) {
      e.preventDefault();
      sendStop();
      setOptimisticThinking(false);
    }
  }

  const [maximized, setMaximized] = useState(false);

  // Plain Esc inside the composer means "stop the agent's current turn"
  // (handled by handleKeyDown below; matches the Claude Code / Amp CLI
  // conventions). The maximize-exit shortcut therefore needs a modifier:
  // Shift+Esc on every platform, Cmd+Esc on macOS, Ctrl+Esc on the rest.
  useEffect(() => {
    if (!maximized) return;
    function onKey(e: KeyboardEvent) {
      if (e.key !== "Escape") return;
      if (e.shiftKey || e.metaKey || e.ctrlKey) setMaximized(false);
    }
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [maximized]);

  const dotStatus = optimisticThinking ? "thinking" : agentStatus;
  const isStopped = status === "stopped";
  // Send only works when the WS is OPEN — otherwise sendInput silently
  // no-ops. Disable the composer for every non-connected state so the
  // user never thinks a click landed.
  const composerDisabled = status !== "connected";
  const tileClass = `agent-tile mode-${mode}` + (maximized ? " maximized" : "");
  const title = agentName || agentSlug;
  const composerPlaceholder =
    status === "stopped"
      ? "Agent unavailable"
      : status === "connecting"
        ? "Connecting to agent…"
        : status === "closed"
          ? "Reconnecting to agent…"
          : status === "error"
            ? "Connection error — retrying…"
            : dotStatus === "thinking"
              ? "Agent is working — Esc to stop"
              : "Message the agent — Enter sends, Shift+Enter for newline";

  return (
    <div className={tileClass} data-persona={persona}>
      <header>
        {persona && <span className="persona-pip">{PERSONA_GLYPH[persona]}</span>}
        <span className="status-dot" data-status={dotStatus} />
        <h2>{title}</h2>
        {persona && agentName && <span className="agent-slug mono">{agentSlug}</span>}
        {provider && model && (
          <span
            className="provider-pill mono"
            data-provider={shortProvider(provider)}
            title={`Provider: ${provider} · Model: ${model}`}
          >
            {shortProvider(provider)} · {shortModel(model)}
          </span>
        )}
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
            title={maximized ? "Restore (Shift+Esc / Cmd+Esc)" : "Maximize"}
            onClick={() => setMaximized((m) => !m)}
          >
            {maximized ? <RestoreIcon /> : <MaxIcon />}
          </button>
          {onRevealWorktree && (
            <button
              type="button"
              className="tile-ctl"
              title={
                worktreePath
                  ? `Open worktree in finder — ${worktreePath}`
                  : "Open worktree in finder"
              }
              onClick={onRevealWorktree}
            >
              <FolderIcon />
            </button>
          )}
          {onDetach && (
            <button
              type="button"
              className="tile-ctl"
              title="Detach to terminal — opens the CLI mid-conversation, closes this tile to the rail"
              onClick={onDetach}
            >
              <DetachIcon />
            </button>
          )}
          <button
            type="button"
            className="tile-ctl"
            title={
              onClose
                ? "Close — pins to sidebar; reopen any time to resume"
                : "Close unavailable in this view"
            }
            onClick={onClose}
            disabled={!onClose}
          >
            <CloseIcon />
          </button>
        </div>
      </header>
      {isStopped && (
        <div className="tile-banner">
          This agent slug isn't known to the server. Close it to clear from the rail.
        </div>
      )}
      <div className="transcript" ref={transcriptRef}>
        {units.map((unit) => (
          <Unit key={unit.key} unit={unit} />
        ))}
      </div>
      {lastMetrics && <TurnMetricsBar metrics={lastMetrics} />}
      {pendingPermissions.length > 0 && (
        <div className="permission-prompts">
          {pendingPermissions.map((p) => (
            <PermissionPrompt
              key={p.request_id}
              prompt={p}
              onDecide={sendPermission}
            />
          ))}
        </div>
      )}
      <form className="composer" onSubmit={handleSubmit}>
        <textarea
          ref={textareaRef}
          value={draft}
          onChange={(e) => setDraft(e.target.value)}
          onKeyDown={handleKeyDown}
          placeholder={composerPlaceholder}
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
function FolderIcon() {
  return (
    <svg viewBox="0 0 16 16" width="13" height="13" aria-hidden>
      <path
        d="M2 4.5a1 1 0 0 1 1-1h3.4l1.2 1.5H13a1 1 0 0 1 1 1V12a1 1 0 0 1-1 1H3a1 1 0 0 1-1-1V4.5z"
        stroke="currentColor"
        strokeWidth="1.4"
        fill="none"
        strokeLinejoin="round"
      />
    </svg>
  );
}
function DetachIcon() {
  // Terminal prompt glyph — chevron + cursor bar — communicates "this
  // is leaving for a CLI." Distinct enough from CloseIcon (×) and
  // MaxIcon (square) at 13×13.
  return (
    <svg viewBox="0 0 16 16" width="13" height="13" aria-hidden>
      <path
        d="M3 4l3 4-3 4M9 12h4"
        stroke="currentColor"
        strokeWidth="1.4"
        fill="none"
        strokeLinecap="round"
        strokeLinejoin="round"
      />
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

type TodoItem = {
  content: string;
  status: "pending" | "in_progress" | "completed";
  activeForm?: string;
};

type RenderUnit =
  | { kind: "assistant"; key: number; text: string; complete: boolean }
  | { kind: "thinking"; key: number; text: string; complete: boolean }
  | { kind: "user"; key: number; text: string }
  | { kind: "tool_call"; key: number; name: string; args: string }
  | { kind: "tool_result"; key: number; content: string; isError: boolean }
  | { kind: "todo_list"; key: number; todos: TodoItem[] }
  | { kind: "status"; key: number; status: string }
  | { kind: "artifact"; key: number; payload: unknown }
  | { kind: "error"; key: number; message: string }
  | { kind: "permission_resolved"; key: number; decision: PermissionDecision; tool_name: string };

function groupEvents(events: AgentEvent[]): RenderUnit[] {
  const out: RenderUnit[] = [];
  let pendingAssistant:
    | { kind: "assistant"; key: number; text: string; complete: boolean }
    | null = null;
  let pendingThinking:
    | { kind: "thinking"; key: number; text: string; complete: boolean }
    | null = null;
  // tool_use_ids whose matching tool_result we want to drop, because we
  // already rendered the call in a richer form (e.g. TodoWrite as a
  // checklist) — the generic "→ result ok" line would just be noise.
  const suppressedToolResults = new Set<string>();
  // Resolved permission_decision lines need the tool_name from the
  // earlier permission_request — backend doesn't echo it on the
  // decision. Build the lookup as we walk.
  const permissionTools = new Map<string, string>();

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
    } else if (ev.type === "tool_call" && stringField(ev, "name") === "TodoWrite") {
      pendingAssistant = null;
      pendingThinking = null;
      const todos = parseTodos(ev.arguments);
      if (todos) {
        const toolId = stringField(ev, "tool_id");
        if (toolId) suppressedToolResults.add(toolId);
        out.push({ kind: "todo_list", key: ev.seq, todos });
      } else {
        // Malformed arguments — fall back to the generic tool_call view.
        const unit = renderUnitFor(ev);
        if (unit) out.push(unit);
      }
    } else if (
      ev.type === "tool_result" &&
      suppressedToolResults.has(stringField(ev, "tool_id"))
    ) {
      // Drop: we already showed the rich render of the corresponding call.
      pendingAssistant = null;
      pendingThinking = null;
    } else if (ev.type === "permission_request") {
      // Don't push a transcript line — the prompt UI lives above the
      // composer for unresolved requests. We only record the tool_name
      // so the matching decision can render with it.
      const rid = stringField(ev, "request_id");
      const tool = stringField(ev, "tool_name");
      if (rid) permissionTools.set(rid, tool);
      pendingAssistant = null;
      pendingThinking = null;
    } else if (ev.type === "permission_decision") {
      pendingAssistant = null;
      pendingThinking = null;
      const rid = stringField(ev, "request_id");
      const decision = stringField(ev, "decision") as PermissionDecision;
      if (decision === "allow" || decision === "allow_always" || decision === "deny") {
        out.push({
          kind: "permission_resolved",
          key: ev.seq,
          decision,
          tool_name: permissionTools.get(rid) ?? "(unknown tool)",
        });
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

function parseTodos(raw: unknown): TodoItem[] | null {
  if (!raw || typeof raw !== "object") return null;
  const todos = (raw as { todos?: unknown }).todos;
  if (!Array.isArray(todos)) return null;
  const out: TodoItem[] = [];
  for (const t of todos) {
    if (!t || typeof t !== "object") return null;
    const content = (t as { content?: unknown }).content;
    const status = (t as { status?: unknown }).status;
    const activeForm = (t as { activeForm?: unknown }).activeForm;
    if (typeof content !== "string") return null;
    if (status !== "pending" && status !== "in_progress" && status !== "completed") {
      return null;
    }
    out.push({
      content,
      status,
      activeForm: typeof activeForm === "string" ? activeForm : undefined,
    });
  }
  return out;
}

function renderUnitFor(ev: AgentEvent): RenderUnit | null {
  switch (ev.type) {
    case "user_input":
      return { kind: "user", key: ev.seq, text: stringField(ev, "text") };
    case "user_stop":
      return { kind: "status", key: ev.seq, status: "stopped" };
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

type TurnRollup = {
  durationMs: number;
  inputTokens: number;
  outputTokens: number;
  cacheReadTokens: number;
  cacheCreationTokens: number;
  model: string | null;
};

function latestMetrics(events: AgentEvent[]): TurnRollup | null {
  for (let i = events.length - 1; i >= 0; i--) {
    const ev = events[i];
    if (ev.type !== "turn_metrics") continue;
    return {
      durationMs: numberField(ev, "duration_ms"),
      inputTokens: numberField(ev, "input_tokens"),
      outputTokens: numberField(ev, "output_tokens"),
      cacheReadTokens: numberField(ev, "cache_read_input_tokens"),
      cacheCreationTokens: numberField(ev, "cache_creation_input_tokens"),
      model: typeof ev.model === "string" ? ev.model : null,
    };
  }
  return null;
}

function numberField(ev: AgentEvent, key: string): number {
  const value = ev[key];
  return typeof value === "number" ? value : 0;
}

function formatDuration(ms: number): string {
  if (ms < 1000) return `${ms}ms`;
  const totalSec = Math.round(ms / 1000);
  if (totalSec < 60) return `${totalSec}s`;
  const m = Math.floor(totalSec / 60);
  const s = totalSec % 60;
  return s === 0 ? `${m}m` : `${m}m ${s}s`;
}

function formatTokens(n: number): string {
  if (n < 1000) return `${n}`;
  if (n < 10_000) return `${(n / 1000).toFixed(1)}k`;
  if (n < 1_000_000) return `${Math.round(n / 1000)}k`;
  return `${(n / 1_000_000).toFixed(1)}M`;
}

function TurnMetricsBar({ metrics }: { metrics: TurnRollup }) {
  // Total tokens charged on the response side: input + cached lookups
  // count toward the prompt; output is what the model wrote. We surface
  // their sum because that's the "cost-shaped" number users care about
  // at a glance, mirroring Claude Code's "↓ N tokens" rollup.
  const totalTokens =
    metrics.inputTokens +
    metrics.outputTokens +
    metrics.cacheReadTokens +
    metrics.cacheCreationTokens;
  const tooltip =
    `Duration: ${formatDuration(metrics.durationMs)}\n` +
    `Input: ${metrics.inputTokens.toLocaleString()}\n` +
    `Output: ${metrics.outputTokens.toLocaleString()}\n` +
    `Cache read: ${metrics.cacheReadTokens.toLocaleString()}\n` +
    `Cache write: ${metrics.cacheCreationTokens.toLocaleString()}`;
  return (
    <div className="turn-metrics" title={tooltip}>
      <span className="turn-metrics-item">{formatDuration(metrics.durationMs)}</span>
      <span className="turn-metrics-sep">·</span>
      <span className="turn-metrics-item">↓ {formatTokens(totalTokens)} tokens</span>
    </div>
  );
}

function shortProvider(provider: string): string {
  if (provider === "claude-code") return "claude";
  return provider;
}

function shortModel(model: string): string {
  // Claude model ids are prefixed (claude-opus-4-7, claude-sonnet-4-6);
  // strip the redundant prefix for display since the provider pill
  // sits next to it. Amp modes (smart/rush/deep/large) display as-is.
  return model.startsWith("claude-") ? model.slice("claude-".length) : model;
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
    case "todo_list":
      return <TodoList todos={unit.todos} />;
    case "status":
      return <div className="msg msg-status">[{unit.status}]</div>;
    case "artifact":
      return (
        <div className="msg msg-artifact">⚑ {JSON.stringify(unit.payload)}</div>
      );
    case "error":
      return <div className="msg msg-error">{unit.message}</div>;
    case "permission_resolved":
      return (
        <div className="msg msg-permission" data-decision={unit.decision}>
          {unit.decision === "deny" ? "✗ denied" : "✓ allowed"}{" "}
          <span className="tool-name">{unit.tool_name}</span>
          {unit.decision === "allow_always" && (
            <span className="hint"> · always</span>
          )}
        </div>
      );
  }
}

function PermissionPrompt({
  prompt,
  onDecide,
}: {
  prompt: PendingPermission;
  onDecide: (request_id: string, decision: PermissionDecision) => void;
}) {
  const [showInput, setShowInput] = useState(false);
  const summary = summariseToolInput(prompt.tool_name, prompt.tool_input);
  return (
    <div className="permission-prompt" role="alertdialog" aria-label={`Permission for ${prompt.tool_name}`}>
      <div className="permission-prompt-hd">
        <span className="permission-prompt-icon" aria-hidden>🔒</span>
        <span className="permission-prompt-title">
          Allow <span className="tool-name">{prompt.tool_name}</span>?
        </span>
        <button
          type="button"
          className="permission-prompt-toggle"
          onClick={() => setShowInput((v) => !v)}
          aria-expanded={showInput}
        >
          {showInput ? "Hide details" : "Show details"}
        </button>
      </div>
      {summary && <div className="permission-prompt-summary mono">{summary}</div>}
      {showInput && (
        <pre className="permission-prompt-input">
          {JSON.stringify(prompt.tool_input, null, 2)}
        </pre>
      )}
      <div className="permission-prompt-actions">
        <button
          type="button"
          className="btn sm primary"
          onClick={() => onDecide(prompt.request_id, "allow")}
        >
          Allow once
        </button>
        <button
          type="button"
          className="btn sm"
          onClick={() => onDecide(prompt.request_id, "allow_always")}
          title={`Allow ${prompt.tool_name} for the rest of this session`}
        >
          Allow always
        </button>
        <button
          type="button"
          className="btn sm ghost"
          onClick={() => onDecide(prompt.request_id, "deny")}
        >
          Deny
        </button>
      </div>
    </div>
  );
}

function summariseToolInput(toolName: string, input: Record<string, unknown>): string {
  // Pull the most informative single field per tool so the user can
  // decide without expanding the full JSON. Falls through to empty on
  // tools we don't have a special case for; the user can click "Show
  // details" to see everything.
  if (toolName === "Bash") {
    const cmd = input.command;
    return typeof cmd === "string" ? cmd : "";
  }
  if (toolName === "Edit" || toolName === "Write" || toolName === "Read") {
    const path = input.file_path ?? input.path;
    return typeof path === "string" ? path : "";
  }
  if (toolName === "WebFetch") {
    const url = input.url;
    return typeof url === "string" ? url : "";
  }
  return "";
}

function TodoList({ todos }: { todos: TodoItem[] }) {
  const completed = todos.filter((t) => t.status === "completed").length;
  return (
    <div className="msg msg-todos" role="group" aria-label="Plan checklist">
      <div className="todo-summary">
        ✓ Plan · {completed}/{todos.length} done
      </div>
      <ul className="todo-list">
        {todos.map((t, i) => (
          <li key={i} className={`todo todo-${t.status}`}>
            <span className="todo-marker" aria-hidden="true">
              {t.status === "completed" ? "✓" : t.status === "in_progress" ? "▸" : "○"}
            </span>
            <span className="todo-text">
              {t.status === "in_progress" && t.activeForm ? t.activeForm : t.content}
            </span>
          </li>
        ))}
      </ul>
    </div>
  );
}
