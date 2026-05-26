import {
  type FormEvent,
  type KeyboardEvent as ReactKeyboardEvent,
  type ReactNode,
  useEffect,
  useLayoutEffect,
  useMemo,
  useRef,
  useState,
} from "react";
import { diffLines } from "diff";
import { codeToTokensBase, type ThemedToken } from "shiki";

// Render the most recent N events of a transcript by default. Long-lived
// agents accumulate thousands of events (every status change, every
// MessageDelta, every tool call/result), and rendering them all bloats
// the DOM until scrolling and selection lag noticeably. The cap keeps
// the working set bounded; the user can expand by 500 at a time when
// they want to scroll into history.
const TRANSCRIPT_CAP_INITIAL = 500;
const TRANSCRIPT_CAP_STEP = 500;

import {
  compactAgent,
  getAgentCompactionSummary,
  type Connection,
  type ConnectionType,
  type ContextEntry,
  type ModelMeta,
  PERSONA_GLYPH,
  type Persona,
  listConnections,
  patchAgent,
  switchAgentThread,
} from "./api";
import { useConnectionDescriptors } from "./connectionDescriptors";
import { ContextRow } from "./ContextRow";
import { useDragHandle } from "./dragHandleContext";
import { MarkdownText } from "./MarkdownText";
import { lookupModelMeta, useProviderDescriptors } from "./providerDescriptors";
import { SimpleContextRow, type SimpleContextType } from "./SimpleContextRow";
import { useArtifactsRefresh } from "./state/artifactsRefresh";
import {
  type AgentEvent,
  type PendingPermission,
  type PermissionDecision,
  useAgentStream,
} from "./useAgentStream";
import { shortenPath } from "./WorkView";

const COMPOSER_MAX_HEIGHT = 200;
const COMPACTION_NOTICE_PCT = 70;
const COMPACTION_RECOMMENDED_PCT = 85;
const COMPACTION_URGENT_PCT = 95;
const COMPACTION_BLOCKED_PCT = 100;

const SIMPLE_PICKER_TYPES: { id: SimpleContextType; label: string }[] = [
  { id: "text", label: "Text" },
  { id: "url", label: "URL" },
  { id: "file", label: "File" },
];

const SIMPLE_CONTEXT_TYPES: ReadonlySet<string> = new Set(["text", "url", "file"]);

function isSimpleType(type: string): type is SimpleContextType {
  return SIMPLE_CONTEXT_TYPES.has(type);
}

type AgentTileProps = {
  agentSlug: string;
  /** Set when mounted under a WorkView so artifact_recorded events bump
   *  the work's rail revision. Standalone /agents/{slug} mounts can
   *  omit it — there's no rail to refresh. */
  workSlug?: string;
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
  /** Open the handoff flow with this tile's agent as the source. The
   *  parent (WorkView) generates the handoff doc + opens NewAgentDialog
   *  pre-filled. When omitted (e.g. standalone /agents/{slug} mount),
   *  the handoff button is hidden. */
  onHandoff?: () => void;
  /** Open the agent's worktree (or source folder fallback) in the OS
   *  file browser. The path is shown in the tooltip so the user can
   *  also copy it from there. When omitted, the folder button is
   *  hidden. */
  onRevealWorktree?: () => void;
  /** Open the agent's worktree in an editor (VSCode/Cursor) so the
   *  user can review diffs without leaving their normal IDE flow.
   *  When omitted, the IDE button is hidden. */
  onOpenInIde?: () => void;
  /** Called with the agent's new name after a successful PATCH. Parent
   *  uses it to keep its ``agents`` state in sync so the rail / tile
   *  re-render with the new label. When omitted, the inline rename
   *  affordance on the tile header is disabled (double-click is a
   *  no-op) — useful for the standalone ``/agents/{slug}`` mount that
   *  doesn't have a parent list to update. */
  onRename?: (name: string) => void;
  /** Open the agent's worktree in a terminal session — same target as
   *  ``onRevealWorktree`` but lands the user at a shell prompt rather
   *  than a file browser. When omitted, the console button is hidden. */
  onOpenInConsole?: () => void;
  /** Reveal Atelier's per-agent bookkeeping dir
   *  (``~/Atelier/works/<work>/agents/<agent>/`` — transcript, agent.json,
   *  contexts/) in the OS file browser. Surfaced via a right-click menu
   *  on the folder pill; when omitted the menu item is hidden. */
  onRevealAtelierDir?: () => void;
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
  workSlug,
  mode = "page",
  persona,
  agentName,
  provider,
  model,
  onClose,
  onDetach,
  onHandoff,
  onRevealWorktree,
  onOpenInIde,
  onOpenInConsole,
  onRevealAtelierDir,
  onRename,
  worktreePath,
}: AgentTileProps) {
  const {
    events,
    status,
    sendInput,
    sendStop,
    sendPermission,
    pendingPermissions,
    pendingHandoff,
  } = useAgentStream(agentSlug);
  const [handoffSwitching, setHandoffSwitching] = useState(false);
  const [handoffError, setHandoffError] = useState<string | null>(null);
  // Reset transient switching/error state whenever the offer goes away
  // (handoff_accepted arrived) or a fresh offer appears. Without this,
  // a second handoff in the same agent session would inherit the first
  // attempt's error message.
  useEffect(() => {
    setHandoffSwitching(false);
    setHandoffError(null);
  }, [pendingHandoff?.new_thread_id]);
  // Provided by SortableCanvasCell when the tile is mounted on the
  // WorkView canvas; absent on the standalone /agents/{slug} page.
  const dragHandle = useDragHandle();

  // A single subtle hint slot in the tile header replaces native
  // tooltips on the header's buttons + pills. Buttons set it via
  // mouseenter, clear via mouseleave — single predictable location for
  // hover descriptions, no positioning headaches, no native ~700ms delay.
  const [hint, setHint] = useState<string | null>(null);

  // Folder-pill right-click menu. Anchored at the cursor position so
  // it renders next to the actual click rather than the pill — same
  // shape as a native OS context menu. Closes on any click outside
  // (or a click on a menu item). Esc is intentionally NOT bound:
  // Esc is reserved for stop-agent across the app.
  const [folderMenu, setFolderMenu] = useState<{ x: number; y: number } | null>(
    null,
  );
  useEffect(() => {
    if (!folderMenu) return;
    const handler = () => setFolderMenu(null);
    window.addEventListener("click", handler);
    window.addEventListener("scroll", handler, true);
    return () => {
      window.removeEventListener("click", handler);
      window.removeEventListener("scroll", handler, true);
    };
  }, [folderMenu]);

  // Inline rename of the agent's display name. Triggered by double-click
  // on the title; same Enter-saves / Esc-cancels semantics as the rail
  // row in WorkView. Skipped entirely when the parent didn't wire
  // ``onRename`` (e.g. standalone /agents/{slug} mount).
  const [editingName, setEditingName] = useState(false);
  const [draftName, setDraftName] = useState<string>(agentName ?? "");
  const [renameError, setRenameError] = useState<string | null>(null);
  const nameInputRef = useRef<HTMLInputElement>(null);
  useEffect(() => {
    if (editingName) {
      nameInputRef.current?.focus();
      nameInputRef.current?.select();
    }
  }, [editingName]);
  // Keep the local draft in sync with parent updates (e.g. the rail
  // renamed the same agent) so reopening the editor uses the current
  // name as the default value.
  useEffect(() => {
    if (!editingName) setDraftName(agentName ?? "");
  }, [agentName, editingName]);

  function startRename() {
    if (!onRename) return;
    setDraftName(agentName ?? "");
    setRenameError(null);
    setEditingName(true);
  }

  function cancelRename() {
    setEditingName(false);
    setRenameError(null);
  }

  async function commitRename() {
    if (!onRename) {
      cancelRename();
      return;
    }
    const next = draftName.trim();
    if (!next || next === (agentName ?? "")) {
      cancelRename();
      return;
    }
    try {
      const updated = await patchAgent(agentSlug, { name: next });
      onRename(updated.name);
      setEditingName(false);
      setRenameError(null);
    } catch (err) {
      setRenameError(err instanceof Error ? err.message : String(err));
    }
  }
  const hintHandlers = (text: string) => ({
    onMouseEnter: () => setHint(text),
    onMouseLeave: () => setHint((current) => (current === text ? null : current)),
    onFocus: () => setHint(text),
    onBlur: () => setHint((current) => (current === text ? null : current)),
  });

  // Bump the work's rail revision when this agent's stream surfaces an
  // ``artifact_recorded`` event so the Artifacts section refetches. We
  // track the highest seq we've notified on so reopen-from-closed (which
  // replays from cursor 0) doesn't double-bump for events the rail has
  // already shown. Top-level rather than inside another effect because
  // the rail-revision write must be commit-time, not render-time.
  const lastArtifactSeqRef = useRef(0);
  useEffect(() => {
    if (!workSlug) return;
    let highest = lastArtifactSeqRef.current;
    let bumped = false;
    for (const ev of events) {
      if (ev.type === "artifact_recorded" && ev.seq > lastArtifactSeqRef.current) {
        bumped = true;
        if (ev.seq > highest) highest = ev.seq;
      }
    }
    if (bumped) {
      lastArtifactSeqRef.current = highest;
      useArtifactsRefresh.getState().bump(workSlug);
    }
  }, [events, workSlug]);
  const [draft, setDraft] = useState("");
  // Optimistic "thinking" between Send and the first ``status_change``
  // event back from the adapter, so the dot reacts instantly. Stored
  // as the seq we sent at — when a ``status_change`` with a *later*
  // seq arrives, we know the adapter has begun this turn and the real
  // ``status_change`` stream takes over. Storing a number (not a bool)
  // avoids the bug where the previous turn's status leaks into the new
  // optimistic window because the boolean alone doesn't know "since when".
  const [thinkingSinceSeq, setThinkingSinceSeq] = useState<number | null>(null);

  // Composer-local context attachments. Cleared on send. The user can
  // remove individual entries before sending; once Send is hit they're
  // shipped along with the input frame and the backend writes them into
  // the agent's context dir.
  const [pendingContexts, setPendingContexts] = useState<ContextEntry[]>([]);
  const [pickerOpen, setPickerOpen] = useState(false);
  const [connections, setConnections] = useState<Connection[]>([]);
  const { descriptors: connectionDescriptors } = useConnectionDescriptors();
  const fetchableTypes = useMemo(
    () => (connectionDescriptors ?? []).filter((d) => d.context_fetchable),
    [connectionDescriptors],
  );

  useEffect(() => {
    listConnections()
      .then(setConnections)
      .catch(() => setConnections([]));
  }, []);
  const transcriptRef = useRef<HTMLDivElement>(null);
  const textareaRef = useRef<HTMLTextAreaElement>(null);

  // Cap the rendered slice so multi-thousand-event transcripts don't
  // blow the DOM. Default holds the last 500 events; "Load older"
  // expands the cap by another 500 each click. Reset on agent change
  // so reopening a different agent always starts at the cap.
  const [visibleCap, setVisibleCap] = useState(TRANSCRIPT_CAP_INITIAL);
  useEffect(() => {
    setVisibleCap(TRANSCRIPT_CAP_INITIAL);
  }, [agentSlug]);
  const visibleEvents = useMemo(
    () => events.slice(-visibleCap),
    [events, visibleCap],
  );
  const olderEventCount = events.length - visibleEvents.length;

  const units = useMemo(() => groupEvents(visibleEvents), [visibleEvents]);
  const agentStatus = useMemo(() => latestStatus(events), [events]);
  const lastMetrics = useMemo(() => latestMetrics(events), [events]);
  const rawActivityPhase = useMemo(
    () => deriveActivityPhase(events, isAgentActive(events)),
    [events],
  );
  // Debounce label swaps. Rapid event bursts inside a single turn
  // (tool_call → tool_result → message_delta in 200ms) would otherwise
  // strobe the phase text. The shimmer underneath keeps animating
  // throughout; only the label is held steady.
  const [activityPhase, setActivityPhase] = useState<string | null>(
    rawActivityPhase,
  );
  useEffect(() => {
    if (rawActivityPhase === activityPhase) return;
    // Hide immediately when going inactive — no point holding a stale
    // label after the turn ends.
    if (rawActivityPhase === null) {
      setActivityPhase(null);
      return;
    }
    const handle = window.setTimeout(
      () => setActivityPhase(rawActivityPhase),
      300,
    );
    return () => window.clearTimeout(handle);
  }, [rawActivityPhase, activityPhase]);
  const sessionTotals = useMemo(() => sessionMetrics(events), [events]);
  const { byName: providersByName } = useProviderDescriptors();
  const modelMeta = lookupModelMeta(providersByName, provider, model);
  const latestCompactionSeq = useMemo(
    () => latestEventSeq(events, "context_compacted"),
    [events],
  );
  const [compactedMetricsSeq, setCompactedMetricsSeq] = useState<number | null>(
    null,
  );
  useEffect(() => {
    if (!lastMetrics || latestCompactionSeq <= lastMetrics.seq) return;
    setCompactedMetricsSeq((prev) => Math.max(prev ?? 0, lastMetrics.seq));
  }, [lastMetrics, latestCompactionSeq]);
  const contextSnapshot = useMemo(
    () =>
      lastMetrics &&
      compactedMetricsSeq !== null &&
      lastMetrics.seq <= compactedMetricsSeq
        ? null
        : contextSnapshotFor(lastMetrics, modelMeta),
    [lastMetrics, modelMeta, compactedMetricsSeq],
  );
  const compactionLevel = compactionLevelFor(contextSnapshot?.pct ?? null);
  const compactionSeverity = compactionSeverityFor(compactionLevel);
  const compactionBlocked = compactionLevel === "blocked";
  const [compacting, setCompacting] = useState(false);
  const [manualCompactionOpen, setManualCompactionOpen] = useState(false);
  const [deferredCompactionSeverity, setDeferredCompactionSeverity] =
    useState<number | null>(null);
  const [compactionError, setCompactionError] = useState<string | null>(null);
  const shouldOpenCompactionModal =
    contextSnapshot !== null &&
    (manualCompactionOpen ||
      (compactionSeverity >= compactionSeverityFor("recommended") &&
        (compactionBlocked ||
          deferredCompactionSeverity === null ||
          compactionSeverity > deferredCompactionSeverity)));

  useEffect(() => {
    if (compactionLevel === "none" || compactionLevel === "notice") {
      setDeferredCompactionSeverity(null);
      setCompactionError(null);
    }
  }, [compactionLevel]);

  async function handleCompact() {
    setCompacting(true);
    setCompactionError(null);
    try {
      await compactAgent(
        agentSlug,
        compactionBlocked ? "forced_context_limit" : "manual",
      );
      setCompactedMetricsSeq((prev) =>
        lastMetrics ? Math.max(prev ?? 0, lastMetrics.seq) : prev,
      );
      setManualCompactionOpen(false);
      setDeferredCompactionSeverity(null);
    } catch (err) {
      setCompactionError(err instanceof Error ? err.message : String(err));
    } finally {
      setCompacting(false);
    }
  }

  function handleDeferCompaction() {
    if (compactionBlocked) return;
    setDeferredCompactionSeverity(compactionSeverity);
    setManualCompactionOpen(false);
    setCompactionError(null);
  }

  // When the user clicks "Load older", capture the pre-expansion scroll
  // metrics so we can restore their reading position after React commits
  // the bigger slice — without this, new content prepended above pushes
  // their view down by the height of the just-mounted nodes. The ref is
  // the cross-effect signal: useLayoutEffect restores scrollTop, then
  // the auto-scroll effect below sees the ref and skips one cycle so it
  // doesn't yank the user back to the bottom.
  const pendingScrollRestoreRef = useRef<{
    scrollHeight: number;
    scrollTop: number;
  } | null>(null);

  function handleLoadOlder() {
    const el = transcriptRef.current;
    if (el) {
      pendingScrollRestoreRef.current = {
        scrollHeight: el.scrollHeight,
        scrollTop: el.scrollTop,
      };
    }
    setVisibleCap((c) => c + TRANSCRIPT_CAP_STEP);
  }

  useLayoutEffect(() => {
    const pending = pendingScrollRestoreRef.current;
    if (!pending) return;
    const el = transcriptRef.current;
    if (el) {
      el.scrollTop =
        el.scrollHeight - pending.scrollHeight + pending.scrollTop;
    }
    // Leave the ref set; the auto-scroll effect below clears it after
    // skipping one round.
  }, [visibleCap]);

  // Auto-scroll to bottom on new content.
  useEffect(() => {
    if (pendingScrollRestoreRef.current !== null) {
      pendingScrollRestoreRef.current = null;
      return;
    }
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

  // Clear the optimistic-thinking gate once any progress event with a
  // seq later than our send-time has landed: a ``status_change`` (any
  // direction), a terminal ``turn_metrics`` / ``error``, OR a
  // ``message_delta`` / ``message_complete`` (the agent has begun
  // producing output, which is what optimistic thinking was a stand-in
  // for). Multiple signals because adapters vary — Amp, in particular,
  // sometimes drops the trailing ``status_change("idle")`` on short
  // turns, so we can't rely on status_change alone.
  useEffect(() => {
    if (thinkingSinceSeq === null) return;
    for (let i = events.length - 1; i >= 0; i--) {
      const ev = events[i];
      if (ev.seq <= thinkingSinceSeq) break;
      if (
        ev.type === "status_change" ||
        ev.type === "turn_metrics" ||
        ev.type === "error" ||
        ev.type === "message_delta" ||
        ev.type === "message_complete"
      ) {
        setThinkingSinceSeq(null);
        return;
      }
    }
  }, [events, thinkingSinceSeq]);

  // Drop entries the user opened but never typed into (empty value) —
  // they'd 422 server-side. Connection-backed rows also need a conn_id
  // (ContextRow auto-selects one once available).
  const submittableContexts = useMemo(
    () =>
      pendingContexts.filter(
        (c) => c.value.trim() !== "" && (c.conn_id !== null || isSimpleType(c.type)),
      ),
    [pendingContexts],
  );

  function submit() {
    const text = draft.trim();
    if (!text) return;
    if (compactionBlocked) {
      setManualCompactionOpen(true);
      return;
    }
    sendInput(text, submittableContexts);
    setDraft("");
    setPendingContexts([]);
    setPickerOpen(false);
    setThinkingSinceSeq(events[events.length - 1]?.seq ?? 0);
  }

  function addSimpleContext(type: SimpleContextType) {
    setPendingContexts((prev) => [...prev, { type, value: "", conn_id: null }]);
    setPickerOpen(false);
  }

  function addConnectionContext(type: ConnectionType) {
    setPendingContexts((prev) => [...prev, { type, value: "", conn_id: null }]);
    setPickerOpen(false);
  }

  function patchPendingContext(index: number, next: ContextEntry) {
    setPendingContexts((prev) => prev.map((c, i) => (i === index ? next : c)));
  }

  function removePendingContext(index: number) {
    setPendingContexts((prev) => prev.filter((_, i) => i !== index));
  }

  function upsertConnection(connection: Connection) {
    setConnections((prev) => {
      const without = prev.filter((c) => c.slug !== connection.slug);
      return [...without, connection];
    });
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
      isCurrentlyActive
    ) {
      e.preventDefault();
      sendStop();
      setThinkingSinceSeq(null);
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

  const dotStatus = thinkingSinceSeq !== null ? "thinking" : agentStatus;
  // "Active right now" — driven off the last event's nature, not the
  // sticky cumulative status. Some adapters (Amp on short turns) leave
  // ``agentStatus`` parked on ``thinking`` because they don't emit a
  // trailing ``status_change("idle")``; we treat *terminal* events
  // (message_complete, turn_metrics, error, status_change(idle))
  // landing as the agent winding down.
  const isCurrentlyActive =
    status === "connected" &&
    (thinkingSinceSeq !== null || isAgentActive(events));
  const isStopped = status === "stopped";
  // Send only works when the WS is OPEN — otherwise sendInput silently
  // no-ops. Disable the composer for every non-connected state so the
  // user never thinks a click landed.
  const composerDisabled = status !== "connected";
  const sendDisabled = composerDisabled || compacting || compactionBlocked;
  const tileClass = `agent-tile mode-${mode}` + (maximized ? " maximized" : "");
  const title = agentName || agentSlug;
  const composerPlaceholder =
    compactionBlocked
      ? "Compact or handoff before sending"
      : status === "stopped"
      ? "Agent unavailable"
      : status === "connecting"
        ? "Connecting to agent…"
        : status === "closed"
          ? "Reconnecting to agent…"
          : status === "error"
            ? "Connection error — retrying…"
            : isCurrentlyActive
              ? "Agent is working — Esc to stop"
              : "Message the agent — Enter sends, Shift+Enter for newline";

  return (
    <div className={tileClass} data-persona={persona}>
      <header
        className={dragHandle ? "tile-drag-header" : undefined}
        {...(dragHandle?.attributes ?? {})}
        {...(dragHandle?.listeners ?? {})}
      >
        <div className="tile-header-left">
          {persona && <span className="persona-pip">{PERSONA_GLYPH[persona]}</span>}
          <span className="status-dot" data-status={dotStatus} />
          {editingName ? (
            <input
              ref={nameInputRef}
              className="tile-name-input"
              value={draftName}
              onChange={(e) => setDraftName(e.target.value)}
              onKeyDown={(e) => {
                if (e.key === "Escape") {
                  e.preventDefault();
                  e.stopPropagation();
                  cancelRename();
                } else if (e.key === "Enter") {
                  e.preventDefault();
                  void commitRename();
                }
              }}
              onBlur={() => void commitRename()}
              onPointerDown={(e) => e.stopPropagation()}
              onClick={(e) => e.stopPropagation()}
              aria-label="Rename agent"
            />
          ) : (
            <h2
              onDoubleClick={
                onRename
                  ? (e) => {
                      e.stopPropagation();
                      startRename();
                    }
                  : undefined
              }
              title={onRename ? "Double-click to rename" : undefined}
              style={onRename ? { cursor: "text" } : undefined}
            >
              {title}
            </h2>
          )}
          {renameError && !editingName && (
            <span className="tile-rename-err">{renameError}</span>
          )}
        </div>
        <div className="tile-header-meta">
          {persona && agentName && <span className="agent-slug mono">{agentSlug}</span>}
          {provider && model && (
            <span
              className="provider-pill mono"
              data-provider={shortProvider(provider)}
              {...hintHandlers(`Provider: ${provider} · Model: ${model}`)}
            >
              {shortProvider(provider)} · {shortModel(model)}
            </span>
          )}
          <span className="conn-status" data-conn-status={status}>{status}</span>
          {worktreePath && (
            <button
              type="button"
              className="folder-pill mono"
              aria-label={`Reveal worktree — ${worktreePath}`}
              onClick={onRevealWorktree}
              onContextMenu={
                onRevealAtelierDir
                  ? (e) => {
                      e.preventDefault();
                      e.stopPropagation();
                      setFolderMenu({ x: e.clientX, y: e.clientY });
                    }
                  : undefined
              }
              disabled={!onRevealWorktree}
              {...hintHandlers(
                onRevealAtelierDir
                  ? `Reveal in Finder · ${worktreePath} · right-click for more`
                  : `Reveal in Finder · ${worktreePath}`,
              )}
            >
              {shortenPath(worktreePath)}
            </button>
          )}
          {folderMenu && (
            <div
              className="folder-pill-menu"
              style={{ left: folderMenu.x, top: folderMenu.y }}
              onClick={(e) => e.stopPropagation()}
            >
              <button
                type="button"
                className="menu-item"
                onClick={() => {
                  setFolderMenu(null);
                  onRevealWorktree?.();
                }}
              >
                Open worktree
              </button>
              <button
                type="button"
                className="menu-item"
                onClick={() => {
                  setFolderMenu(null);
                  onRevealAtelierDir?.();
                }}
              >
                Open Atelier folder
              </button>
            </div>
          )}
        </div>
        <div className="tile-header-right">
          <span
            className={"tile-hint" + (hint ? " visible" : "")}
            aria-hidden="true"
          >
            {hint}
          </span>
          <div className="tile-controls">
          {onOpenInIde && (
            <button
              type="button"
              className="tile-ctl"
              aria-label="Open worktree in editor"
              onClick={onOpenInIde}
              {...hintHandlers("Open in editor")}
            >
              <OpenIdeIcon />
            </button>
          )}
          {onOpenInConsole && (
            <button
              type="button"
              className="tile-ctl"
              aria-label="Open worktree in console"
              onClick={onOpenInConsole}
              {...hintHandlers("Open in console")}
            >
              <OpenConsoleIcon />
            </button>
          )}
          {onHandoff && (
            <button
              type="button"
              className="tile-ctl"
              aria-label="Handoff to agent"
              onClick={onHandoff}
              {...hintHandlers("Handoff to agent")}
            >
              <HandoffIcon />
            </button>
          )}
          <button
            type="button"
            className="tile-ctl"
            aria-label={maximized ? "Restore" : "Maximize"}
            onClick={() => setMaximized((m) => !m)}
            {...hintHandlers(maximized ? "Restore" : "Maximize")}
          >
            {maximized ? <RestoreIcon /> : <MaxIcon />}
          </button>
          {onDetach && (
            <button
              type="button"
              className="tile-ctl"
              aria-label="Detach to terminal"
              onClick={onDetach}
              {...hintHandlers("Detach to CLI")}
            >
              <DetachIcon />
            </button>
          )}
          <button
            type="button"
            className="tile-ctl"
            aria-label={onClose ? "Close" : "Close unavailable"}
            onClick={onClose}
            disabled={!onClose}
            {...hintHandlers(
              onClose ? "Close · pins to sidebar" : "Close unavailable",
            )}
          >
            <CloseIcon />
          </button>
          </div>
        </div>
      </header>
      <div
        className={
          "agent-tile-body" +
          (shouldOpenCompactionModal ? " is-compaction-blurred" : "")
        }
        aria-hidden={shouldOpenCompactionModal}
      >
        {isStopped && (
          <div className="tile-banner">
            This agent slug isn't known to the server. Close it to clear from the rail.
          </div>
        )}
        <div className="transcript" ref={transcriptRef}>
          {olderEventCount > 0 && (
            <button
              type="button"
              className="transcript-load-older"
              onClick={handleLoadOlder}
            >
              Load {Math.min(TRANSCRIPT_CAP_STEP, olderEventCount)} older
              {olderEventCount > TRANSCRIPT_CAP_STEP
                ? ` · ${olderEventCount} hidden`
                : ""}
            </button>
          )}
          <TranscriptUnits units={units} agentSlug={agentSlug} />
        </div>
        {(lastMetrics || isCurrentlyActive) && (
          <TurnMetricsBar
            metrics={lastMetrics}
            session={sessionTotals}
            meta={modelMeta}
            activityPhase={activityPhase}
            context={contextSnapshot}
            onCompact={() => {
              setManualCompactionOpen(true);
              setCompactionError(null);
            }}
          />
        )}
        {contextSnapshot && compactionLevel !== "none" && !shouldOpenCompactionModal && (
          <CompactionInlineWarning
            context={contextSnapshot}
            level={compactionLevel}
            onCompact={() => {
              setManualCompactionOpen(true);
              setCompactionError(null);
            }}
          />
        )}
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
        {pendingHandoff && (
          <HandoffPrompt
            threadId={pendingHandoff.new_thread_id}
            switching={handoffSwitching}
            error={handoffError}
            onSwitch={async () => {
              setHandoffError(null);
              setHandoffSwitching(true);
              try {
                await switchAgentThread(agentSlug, pendingHandoff.new_thread_id);
                // The backend appends `handoff_accepted` to the transcript,
                // which clears `pendingHandoff` via the WS replay. We leave
                // `switching` true until the new event arrives so the
                // button doesn't flash back to "Continue".
              } catch (err) {
                setHandoffError(
                  err instanceof Error ? err.message : String(err),
                );
                setHandoffSwitching(false);
              }
            }}
          />
        )}
        <form className="composer" onSubmit={handleSubmit}>
          {pendingContexts.length > 0 && (
            <div className="composer-contexts">
              {pendingContexts.map((c, i) =>
                isSimpleType(c.type) ? (
                  <SimpleContextRow
                    key={i}
                    context={c}
                    onChange={(next) => patchPendingContext(i, next)}
                    onRemove={() => removePendingContext(i)}
                  />
                ) : (
                  <ContextRow
                    key={i}
                    context={c}
                    connections={connections}
                    onChange={(next) => patchPendingContext(i, next)}
                    onRemove={() => removePendingContext(i)}
                    onConnectionSaved={upsertConnection}
                  />
                ),
              )}
            </div>
          )}
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
            <div className="composer-add-context">
              <button
                type="button"
                className="composer-tool"
                onClick={() => setPickerOpen((o) => !o)}
                title="Attach context to your next message — appended to context.md when you Send"
                aria-expanded={pickerOpen}
              >
                + Add context
              </button>
              {pickerOpen && (
                <div className="composer-context-picker">
                  {SIMPLE_PICKER_TYPES.map((s) => (
                    <button
                      key={s.id}
                      type="button"
                      className="btn sm"
                      data-source={s.id}
                      onClick={() => addSimpleContext(s.id)}
                    >
                      {s.label}
                    </button>
                  ))}
                  {fetchableTypes.map((d) => (
                    <button
                      key={d.type}
                      type="button"
                      className="btn sm"
                      data-source={d.type}
                      onClick={() => addConnectionContext(d.type)}
                    >
                      {d.label}
                    </button>
                  ))}
                </div>
              )}
            </div>
            <span className="spacer" />
            <button
              type="submit"
              className="composer-send"
              disabled={sendDisabled || !draft.trim()}
            >
              Send
            </button>
          </div>
        </form>
      </div>
      {shouldOpenCompactionModal && contextSnapshot && (
        <CompactionModal
          context={contextSnapshot}
          level={compactionLevel}
          compacting={compacting}
          error={compactionError}
          canHandoff={Boolean(onHandoff)}
          onCompact={() => void handleCompact()}
          onDefer={compactionBlocked ? undefined : handleDeferCompaction}
          onHandoff={onHandoff}
        />
      )}
    </div>
  );
}

// ---------------------------------------------------------------------------
// Tile control icons (inline SVG — no icon system yet)
// ---------------------------------------------------------------------------

function OpenIdeIcon() {
  // Code brackets — `<` `>` pointing outward — reads as "open in
  // editor". Distinct from HandoffIcon's inward arrows and DetachIcon's
  // single chevron at 13×13.
  return (
    <svg viewBox="0 0 16 16" width="13" height="13" aria-hidden>
      <path
        d="M6 5L3 8l3 3M10 5l3 3-3 3"
        stroke="currentColor"
        strokeWidth="1.4"
        fill="none"
        strokeLinecap="round"
        strokeLinejoin="round"
      />
    </svg>
  );
}
function OpenConsoleIcon() {
  // Window frame with a chevron prompt — reads as "open a terminal at
  // this folder". Distinct from DetachIcon (no frame, single chevron)
  // and OpenIdeIcon (brackets, no frame).
  return (
    <svg viewBox="0 0 16 16" width="13" height="13" aria-hidden>
      <rect
        x="2"
        y="3"
        width="12"
        height="10"
        rx="1.4"
        stroke="currentColor"
        strokeWidth="1.2"
        fill="none"
      />
      <path
        d="M5 7l2 1.5L5 10"
        stroke="currentColor"
        strokeWidth="1.2"
        fill="none"
        strokeLinecap="round"
        strokeLinejoin="round"
      />
    </svg>
  );
}
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

type ToolResultPayload = { content: string; isError: boolean };

type RenderUnit =
  | { kind: "assistant"; key: number; text: string; complete: boolean }
  | { kind: "thinking"; key: number; text: string; complete: boolean }
  | { kind: "user"; key: number; text: string }
  | {
      kind: "tool_call";
      key: number;
      name: string;
      args: Record<string, unknown>;
      // Set when a tool_result event matching this call's tool_id is
      // seen. Renderers fold the result into the same card so the user
      // sees one paired unit instead of two siblings.
      result?: ToolResultPayload;
    }
  | { kind: "tool_result"; key: number; content: string; isError: boolean }
  | { kind: "todo_list"; key: number; todos: TodoItem[] }
  | { kind: "status"; key: number; status: string }
  | { kind: "artifact"; key: number; payload: unknown }
  | { kind: "error"; key: number; message: string }
  | {
      kind: "compaction";
      key: number;
      oldSessionId: string;
      newSessionId: string;
      summaryPath: string;
    }
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
  // tool_id → tool_call unit, so a later tool_result with the same id
  // can attach into the same paired card instead of pushing a sibling
  // "→ result" line.
  const toolCallByTd = new Map<
    string,
    Extract<RenderUnit, { kind: "tool_call" }>
  >();

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
        if (unit) {
          out.push(unit);
          if (unit.kind === "tool_call") {
            const tid = stringField(ev, "tool_id");
            if (tid) toolCallByTd.set(tid, unit);
          }
        }
      }
    } else if (
      ev.type === "tool_result" &&
      suppressedToolResults.has(stringField(ev, "tool_id"))
    ) {
      // Drop: we already showed the rich render of the corresponding call.
      pendingAssistant = null;
      pendingThinking = null;
    } else if (ev.type === "tool_result") {
      pendingAssistant = null;
      pendingThinking = null;
      const tid = stringField(ev, "tool_id");
      const matched = tid ? toolCallByTd.get(tid) : undefined;
      const payload: ToolResultPayload = {
        content: stringField(ev, "content"),
        isError: ev.is_error === true,
      };
      if (matched) {
        matched.result = payload;
      } else {
        // Orphan: result arrived without a tool_call we recognise
        // (replay race, suppressed-call edge cases). Show standalone so
        // the user still sees the output.
        out.push({
          kind: "tool_result",
          key: ev.seq,
          content: payload.content,
          isError: payload.isError,
        });
      }
    } else if (ev.type === "tool_call") {
      pendingAssistant = null;
      pendingThinking = null;
      const unit = renderUnitFor(ev);
      if (unit && unit.kind === "tool_call") {
        out.push(unit);
        const tid = stringField(ev, "tool_id");
        if (tid) toolCallByTd.set(tid, unit);
      } else if (unit) {
        out.push(unit);
      }
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
        args:
          ev.arguments && typeof ev.arguments === "object"
            ? (ev.arguments as Record<string, unknown>)
            : {},
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
    case "context_compacted":
      return {
        kind: "compaction",
        key: ev.seq,
        oldSessionId: stringField(ev, "old_session_id"),
        newSessionId: stringField(ev, "new_session_id"),
        summaryPath: stringField(ev, "summary_path"),
      };
    case "compaction_failed":
      return {
        kind: "error",
        key: ev.seq,
        message: `Compaction failed: ${stringField(ev, "message")}`,
      };
    default:
      return null;
  }
}

function stringField(ev: AgentEvent, key: string): string {
  const value = ev[key];
  return typeof value === "string" ? value : "";
}

function isAgentActive(events: AgentEvent[]): boolean {
  // True iff the agent appears to be doing work *right now*. Reads off
  // the last event's nature, not the cumulative ``agentStatus``, so it
  // recovers gracefully when an adapter forgets to emit the trailing
  // ``status_change("idle")`` (Amp on short turns). Terminal events
  // (message_complete, turn_metrics, error, status_change(idle)) flip
  // back to inactive even if the cumulative status remains "thinking".
  const last = events[events.length - 1];
  if (!last) return false;
  switch (last.type) {
    case "message_delta":
    case "thinking_delta":
    case "tool_call":
    case "tool_result":
    case "user_input":
      return true;
    case "status_change": {
      const s = stringField(last, "status");
      return s === "thinking" || s === "live";
    }
    default:
      return false;
  }
}

function latestStatus(events: AgentEvent[]): string {
  // Walk from the tail. ``status_change`` is authoritative; ``turn_metrics``
  // implies idle as a fallback (per protocol it's emitted right before
  // ``status_change("idle")``, so when an adapter drops the trailing
  // status_change — observed with Amp on short turns — the metrics
  // event is the next-best terminal signal). ``error`` also terminates
  // the turn.
  for (let i = events.length - 1; i >= 0; i--) {
    const ev = events[i];
    if (ev.type === "status_change") {
      return stringField(ev, "status");
    }
    if (ev.type === "turn_metrics" || ev.type === "error") {
      return "idle";
    }
  }
  return "idle";
}

type TurnRollup = {
  seq: number;
  durationMs: number;
  inputTokens: number;
  outputTokens: number;
  cacheReadTokens: number;
  cacheCreationTokens: number;
  /** Prompt size of the last AssistantMessage in the turn. Despite the
   *  name, this is the running total of context currently in the
   *  model's window — every sub-call's prompt replays the full
   *  conversation history, so the last sub-call's prompt = everything
   *  loaded right now. The "should I /clear?" number. Zero means the
   *  provider did not expose a reliable snapshot. */
  lastPromptTokens: number;
  model: string | null;
  contextWindow: number;
};

type ContextSnapshot = {
  pct: number;
  promptTokens: number;
  contextWindow: number;
};

type CompactionLevel = "none" | "notice" | "recommended" | "urgent" | "blocked";

/**
 * Best-effort label for what the agent is doing *right now*, derived
 * from the tail of the event log. Returns `null` when the agent isn't
 * actively working (so the indicator hides on idle/error/stopped).
 *
 * Heuristics, in priority order:
 * 1. An unfinished `tool_call` at the tail → "running <Tool>" or, for
 *    file-shaped tools, "editing <basename>" / "reading <basename>".
 * 2. `thinking_delta` recently → "thinking…"
 * 3. `message_delta` recently → "generating…"
 * 4. Otherwise → "working…"
 *
 * "Recently" means within the last 5 events; rapid tool/thinking
 * interleaving inside a turn shouldn't strand us on a stale label.
 */
function deriveActivityPhase(
  events: AgentEvent[], isActive: boolean,
): string | null {
  if (!isActive) return null;
  // Walk back to find the latest unmatched tool_call. Tool results
  // include the tool_use_id of the call they're answering; if the
  // latest tool_call hasn't seen its result yet, it's in-flight.
  const seenResultIds = new Set<string>();
  let latestPendingCall: AgentEvent | null = null;
  const windowStart = Math.max(0, events.length - 30);
  for (let i = events.length - 1; i >= windowStart; i--) {
    const ev = events[i];
    if (ev.type === "tool_result") {
      const id = stringField(ev, "tool_use_id");
      if (id) seenResultIds.add(id);
    } else if (ev.type === "tool_call") {
      const id = stringField(ev, "tool_use_id");
      if (id && !seenResultIds.has(id)) {
        latestPendingCall = ev;
        break;
      }
    }
  }
  if (latestPendingCall) {
    const name = stringField(latestPendingCall, "name") || "tool";
    const args = (latestPendingCall.arguments as Record<string, unknown>) ?? {};
    if (name === "Edit" || name === "MultiEdit" || name === "Write") {
      const path = (args.file_path as string) ?? (args.path as string) ?? "";
      const base = path ? path.split("/").pop() : "";
      return base ? `editing ${base}` : "editing…";
    }
    if (name === "Read") {
      const path = (args.file_path as string) ?? (args.path as string) ?? "";
      const base = path ? path.split("/").pop() : "";
      return base ? `reading ${base}` : "reading…";
    }
    if (name === "Bash") return "running Bash";
    if (name === "Grep" || name === "Glob") return "searching";
    return `running ${name}`;
  }
  const tailStart = Math.max(0, events.length - 5);
  for (let i = events.length - 1; i >= tailStart; i--) {
    const t = events[i].type;
    if (t === "thinking_delta") return "thinking…";
    if (t === "message_delta") return "generating…";
  }
  return "working…";
}

function latestMetrics(events: AgentEvent[]): TurnRollup | null {
  for (let i = events.length - 1; i >= 0; i--) {
    const ev = events[i];
    if (ev.type !== "turn_metrics") continue;
    return {
      seq: ev.seq,
      durationMs: numberField(ev, "duration_ms"),
      inputTokens: numberField(ev, "input_tokens"),
      outputTokens: numberField(ev, "output_tokens"),
      cacheReadTokens: numberField(ev, "cache_read_input_tokens"),
      cacheCreationTokens: numberField(ev, "cache_creation_input_tokens"),
      lastPromptTokens: numberField(ev, "last_prompt_tokens"),
      model: typeof ev.model === "string" ? ev.model : null,
      contextWindow: numberField(ev, "context_window"),
    };
  }
  return null;
}

function latestEventSeq(events: AgentEvent[], type: string): number {
  for (let i = events.length - 1; i >= 0; i--) {
    if (events[i].type === type) return events[i].seq;
  }
  return 0;
}

type SessionTotals = {
  inputTokens: number;
  outputTokens: number;
  cacheReadTokens: number;
  cacheCreationTokens: number;
};

function sessionMetrics(events: AgentEvent[]): SessionTotals {
  const totals: SessionTotals = {
    inputTokens: 0,
    outputTokens: 0,
    cacheReadTokens: 0,
    cacheCreationTokens: 0,
  };
  for (const ev of events) {
    if (ev.type !== "turn_metrics") continue;
    totals.inputTokens += numberField(ev, "input_tokens");
    totals.outputTokens += numberField(ev, "output_tokens");
    totals.cacheReadTokens += numberField(ev, "cache_read_input_tokens");
    totals.cacheCreationTokens += numberField(ev, "cache_creation_input_tokens");
  }
  return totals;
}

function numberField(ev: AgentEvent, key: string): number {
  const value = ev[key];
  return typeof value === "number" ? value : 0;
}

function contextSnapshotFor(
  metrics: TurnRollup | null,
  meta: ModelMeta | null,
): ContextSnapshot | null {
  if (!metrics) return null;
  const promptTokens = metrics.lastPromptTokens > 0 ? metrics.lastPromptTokens : null;
  const contextWindow =
    metrics.contextWindow > 0
      ? metrics.contextWindow
      : meta?.context_window && meta.context_window > 0
        ? meta.context_window
        : null;
  if (promptTokens === null || contextWindow === null) return null;
  return {
    pct: (promptTokens / contextWindow) * 100,
    promptTokens,
    contextWindow,
  };
}

function compactionLevelFor(pct: number | null): CompactionLevel {
  if (pct === null) return "none";
  if (pct >= COMPACTION_BLOCKED_PCT) return "blocked";
  if (pct >= COMPACTION_URGENT_PCT) return "urgent";
  if (pct >= COMPACTION_RECOMMENDED_PCT) return "recommended";
  if (pct >= COMPACTION_NOTICE_PCT) return "notice";
  return "none";
}

function compactionSeverityFor(level: CompactionLevel): number {
  switch (level) {
    case "blocked":
      return 4;
    case "urgent":
      return 3;
    case "recommended":
      return 2;
    case "notice":
      return 1;
    case "none":
      return 0;
  }
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

function TurnMetricsBar({
  metrics,
  session,
  meta,
  activityPhase,
  context,
  onCompact,
}: {
  metrics: TurnRollup | null;
  session: SessionTotals;
  meta: ModelMeta | null;
  activityPhase: string | null;
  context: ContextSnapshot | null;
  onCompact: () => void;
}) {
  // Persona-tinted wave that spans from the last metric segment to
  // the right margin. Renders only while the agent is mid-turn so a
  // settled bar reads as "done".
  const activityNode = activityPhase ? (
    <span
      className="turn-metrics-activity"
      aria-hidden
      title={activityPhase}
    >
      <span className="turn-metrics-activity-track">
        <span className="turn-metrics-activity-fill" />
      </span>
    </span>
  ) : null;
  // First-turn-in-progress path: no ``turn_metrics`` event yet so the
  // rollup is null. Render an em-dash placeholder for each segment so
  // the bar still shows up (with the activity phase + shimmer); the
  // numbers fill in once the turn lands.
  if (metrics === null) {
    return (
      <div className="turn-metrics" title="Metrics will appear when the first turn completes.">
        <span className="turn-metrics-item">—</span>
        <span className="turn-metrics-sep">·</span>
        <span className="turn-metrics-item">↓ — tokens</span>
        <span className="turn-metrics-sep">·</span>
        <span className="turn-metrics-item turn-metrics-ctx">ctx —</span>
        {activityNode}
      </div>
    );
  }
  // Total tokens charged on the response side: input + cached lookups
  // count toward the prompt; output is what the model wrote. We surface
  // their sum because that's the "cost-shaped" number users care about
  // at a glance, mirroring Claude Code's "↓ N tokens" rollup.
  const totalTokens =
    metrics.inputTokens +
    metrics.outputTokens +
    metrics.cacheReadTokens +
    metrics.cacheCreationTokens;
  // Context %: prompt size of the *last model call* in the turn over the
  // model's window. Do not fall back to cumulative input/cache tokens:
  // those are billing totals across sub-calls and can exceed the window.
  const promptTokens = context?.promptTokens ?? null;
  const contextWindow = context?.contextWindow ?? null;
  const ctxPct = context?.pct ?? null;
  const ctxLevel = ctxLevelFor(ctxPct);
  const sessionCost = computeSessionCost(session, meta);
  const tooltipLines = [
    `Duration: ${formatDuration(metrics.durationMs)}`,
    `Input: ${metrics.inputTokens.toLocaleString()}`,
    `Output: ${metrics.outputTokens.toLocaleString()}`,
    `Cache read: ${metrics.cacheReadTokens.toLocaleString()}`,
    `Cache write: ${metrics.cacheCreationTokens.toLocaleString()}`,
  ];
  if (promptTokens !== null && ctxPct !== null && contextWindow !== null) {
    tooltipLines.push(
      `Context: ${promptTokens.toLocaleString()} / ${contextWindow.toLocaleString()} (${ctxPct.toFixed(1)}%)`,
    );
  } else if (contextWindow !== null) {
    tooltipLines.push("Context: unavailable for this turn");
  }
  if (sessionCost !== null) {
    tooltipLines.push(
      `Session: ${formatTokens(
        session.inputTokens +
          session.outputTokens +
          session.cacheReadTokens +
          session.cacheCreationTokens,
      )} tokens · ${formatCost(sessionCost)}`,
    );
  }
  return (
    <div className="turn-metrics" title={tooltipLines.join("\n")}>
      <span className="turn-metrics-item">{formatDuration(metrics.durationMs)}</span>
      <span className="turn-metrics-sep">·</span>
      <span className="turn-metrics-item">↓ {formatTokens(totalTokens)} tokens</span>
      {contextWindow !== null && (
        <>
          <span className="turn-metrics-sep">·</span>
          <span className={`turn-metrics-item turn-metrics-ctx ${ctxLevel}`}>
            {ctxPct !== null ? `ctx ${ctxPct.toFixed(0)}%` : "ctx —"}
          </span>
        </>
      )}
      {sessionCost !== null && (
        <>
          <span className="turn-metrics-sep">·</span>
          <span className="turn-metrics-item">{formatCost(sessionCost)}</span>
        </>
      )}
      {context !== null && (
        <button
          type="button"
          className="turn-metrics-compact"
          onClick={onCompact}
          title="Compact this agent's context"
        >
          Compact
        </button>
      )}
      {activityNode}
    </div>
  );
}

function ctxLevelFor(pct: number | null): string {
  if (pct === null) return "";
  if (pct >= 85) return "is-danger";
  if (pct >= 70) return "is-warn";
  return "";
}

function computeSessionCost(
  session: SessionTotals,
  meta: ModelMeta | null,
): number | null {
  if (!meta) return null;
  // Only emit a cost when at least the input+output rates are known.
  // Cache-write/read fall back to input/output respectively when the
  // provider hasn't broken them out — keeps the number well-defined for
  // any provider that publishes the basics.
  if (meta.input_per_mtok === null || meta.output_per_mtok === null) return null;
  const cacheWriteRate = meta.cache_write_per_mtok ?? meta.input_per_mtok;
  const cacheReadRate = meta.cache_read_per_mtok ?? meta.input_per_mtok;
  return (
    (session.inputTokens / 1_000_000) * meta.input_per_mtok +
    (session.outputTokens / 1_000_000) * meta.output_per_mtok +
    (session.cacheCreationTokens / 1_000_000) * cacheWriteRate +
    (session.cacheReadTokens / 1_000_000) * cacheReadRate
  );
}

function formatCost(usd: number): string {
  if (usd < 0.01) return "<$0.01";
  if (usd < 1) return `$${usd.toFixed(2)}`;
  if (usd < 100) return `$${usd.toFixed(2)}`;
  return `$${Math.round(usd).toLocaleString()}`;
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

function TranscriptUnits({
  units,
  agentSlug,
}: {
  units: RenderUnit[];
  agentSlug: string;
}) {
  const compactionIndex = findLastUnitIndex(units, (unit) => unit.kind === "compaction");
  if (compactionIndex <= 0) {
    return (
      <>
        {units.map((unit) => (
          <Unit key={unit.key} unit={unit} agentSlug={agentSlug} />
        ))}
      </>
    );
  }

  const previousUnits = units.slice(0, compactionIndex);
  const boundary = units[compactionIndex];
  const newUnits = units.slice(compactionIndex + 1);

  return (
    <>
      <details className="transcript-previous-session">
        <summary>
          Previous session before compaction
          <span>{previousUnits.length} items</span>
        </summary>
        <div className="transcript-previous-session-body">
          {previousUnits.map((unit) => (
            <Unit key={unit.key} unit={unit} agentSlug={agentSlug} />
          ))}
        </div>
      </details>
      <Unit unit={boundary} agentSlug={agentSlug} />
      {newUnits.map((unit) => (
        <Unit key={unit.key} unit={unit} agentSlug={agentSlug} />
      ))}
    </>
  );
}

function Unit({ unit, agentSlug }: { unit: RenderUnit; agentSlug: string }) {
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
        <ToolCallView
          name={unit.name}
          args={unit.args}
          result={unit.result}
        />
      );
    case "tool_result":
      return <ToolResultBody content={unit.content} isError={unit.isError} />;
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
    case "compaction":
      return <CompactionBoundary unit={unit} agentSlug={agentSlug} />;
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

function CompactionBoundary({
  unit,
  agentSlug,
}: {
  unit: Extract<RenderUnit, { kind: "compaction" }>;
  agentSlug: string;
}) {
  const [isOpen, setIsOpen] = useState(false);
  const [summary, setSummary] = useState<string | null>(null);
  const [isLoading, setIsLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const filename = compactionSummaryFilename(unit.summaryPath);

  useEffect(() => {
    if (!isOpen || summary !== null || !filename) return;
    let cancelled = false;
    setIsLoading(true);
    setError(null);
    getAgentCompactionSummary(agentSlug, filename)
      .then((result) => {
        if (!cancelled) setSummary(result.content);
      })
      .catch((err) => {
        if (!cancelled) {
          setError(err instanceof Error ? err.message : String(err));
        }
      })
      .finally(() => {
        if (!cancelled) setIsLoading(false);
      });
    return () => {
      cancelled = true;
    };
  }, [agentSlug, filename, isOpen, summary]);

  return (
    <div className="msg msg-compaction">
      <div className="msg-compaction-title">Context compacted</div>
      <div className="msg-compaction-body">
        New session started from summary.
      </div>
      <div className="msg-compaction-meta">
        <span className="mono">{shortSessionId(unit.oldSessionId)}</span>
        <span aria-hidden> → </span>
        <span className="mono">{shortSessionId(unit.newSessionId)}</span>
        {unit.summaryPath && (
          <>
            <span aria-hidden> · </span>
            <span className="mono">{shortenPath(unit.summaryPath)}</span>
          </>
        )}
      </div>
      <button
        type="button"
        className="msg-compaction-summary-toggle"
        disabled={!filename}
        onClick={() => setIsOpen((value) => !value)}
      >
        {isOpen ? "Hide summary" : "View summary"}
      </button>
      {isOpen && (
        <div className="msg-compaction-summary">
          {isLoading && (
            <div className="msg-compaction-summary-state">Loading summary...</div>
          )}
          {error && <div className="msg-compaction-summary-error">{error}</div>}
          {summary !== null && <MarkdownText text={summary} />}
        </div>
      )}
    </div>
  );
}

function findLastUnitIndex(
  units: RenderUnit[],
  predicate: (unit: RenderUnit) => boolean,
): number {
  for (let index = units.length - 1; index >= 0; index -= 1) {
    if (predicate(units[index])) return index;
  }
  return -1;
}

function compactionSummaryFilename(summaryPath: string): string {
  if (!summaryPath) return "";
  const parts = summaryPath.split(/[\\/]/);
  return parts[parts.length - 1] ?? "";
}

function shortSessionId(sessionId: string): string {
  if (!sessionId) return "session";
  if (sessionId.length <= 14) return sessionId;
  return `${sessionId.slice(0, 6)}…${sessionId.slice(-5)}`;
}

// ---------------------------------------------------------------------------
// Per-tool call rendering
// ---------------------------------------------------------------------------

// Each renderer targets the canonical tool shape produced by the
// adapter layer (see backend infrastructure/agents/tool_canonical.py).
// All provider name + key variants (Amp's edit_file/cmd/old_str, etc.)
// are absorbed there, so the FE only knows the canonical names. The
// optional `result` argument is the paired tool_result content folded
// in by groupEvents.
type ToolCallRenderer = (
  args: Record<string, unknown>,
  result?: ToolResultPayload,
) => ReactNode;

const TOOL_RENDERERS: Record<string, ToolCallRenderer> = {
  Bash: BashCallView,
  Edit: EditCallView,
  MultiEdit: MultiEditCallView,
  Write: WriteCallView,
  Read: ReadCallView,
  Grep: GrepCallView,
  Glob: GlobCallView,
};

function ToolCallView({
  name,
  args,
  result,
}: {
  name: string;
  args: Record<string, unknown> | string;
  result?: ToolResultPayload;
}) {
  // Defensive: an older build of this component stored args as a JSON
  // string. After HMR the cached units still carry that shape, and the
  // new per-tool renderers expect an object. Parse on read so a hard
  // reload isn't required to see the new view.
  const parsed = normalizeArgs(args);
  const renderer = TOOL_RENDERERS[name];
  if (renderer) return <>{renderer(parsed, result)}</>;
  return <DefaultToolCallView name={name} args={parsed} result={result} />;
}

function normalizeArgs(
  args: Record<string, unknown> | string,
): Record<string, unknown> {
  if (typeof args === "string") {
    try {
      const obj = JSON.parse(args);
      return obj && typeof obj === "object" ? (obj as Record<string, unknown>) : {};
    } catch {
      return {};
    }
  }
  return args;
}

function BashCallView(
  args: Record<string, unknown>,
  result?: ToolResultPayload,
) {
  const command = stringArg(args, "command");
  const description = stringArg(args, "description");
  const cwd = stringArg(args, "cwd");
  return (
    <details className="msg msg-tool" open={resultLooksLikeDiff(result)}>
      <summary>
        <span className="tool-marker">▸</span>
        <span className="tool-name">Bash</span>
        {command && (
          <span className="tool-summary-detail mono">{command}</span>
        )}
      </summary>
      <div className="tool-call-body">
        {description && (
          <div className="tool-call-meta">{description}</div>
        )}
        {cwd && (
          <div className="tool-call-meta mono">cwd: {shortenPath(cwd)}</div>
        )}
        {command && (
          <MarkdownText text={"```bash\n" + command + "\n```"} />
        )}
        {result && (
          <ToolResultBody content={result.content} isError={result.isError} />
        )}
      </div>
    </details>
  );
}

function EditCallView(
  args: Record<string, unknown>,
  result?: ToolResultPayload,
) {
  const path = stringArg(args, "path");
  const oldStr = stringArg(args, "old_text");
  const newStr = stringArg(args, "new_text");
  const lang = path ? inferLanguage(path) : null;
  return (
    <details className="msg msg-tool" open>
      <summary>
        <span className="tool-marker">▸</span>
        <span className="tool-name">Edit</span>
        {path && (
          <span className="tool-summary-detail mono">
            {shortenPath(path)}
          </span>
        )}
        <DiffStat oldText={oldStr} newText={newStr} />
      </summary>
      <DiffView oldText={oldStr} newText={newStr} lang={lang} />
      {result?.isError && (
        <ToolResultBody content={result.content} isError={result.isError} />
      )}
    </details>
  );
}

function MultiEditCallView(
  args: Record<string, unknown>,
  result?: ToolResultPayload,
) {
  const path = stringArg(args, "path");
  const editsRaw = args.edits;
  const edits: { oldText: string; newText: string }[] = [];
  if (Array.isArray(editsRaw)) {
    for (const e of editsRaw) {
      if (!e || typeof e !== "object") continue;
      const er = e as Record<string, unknown>;
      edits.push({
        oldText: stringArg(er, "old_text"),
        newText: stringArg(er, "new_text"),
      });
    }
  }
  let added = 0;
  let removed = 0;
  for (const e of edits) {
    const s = countDiffStat(e.oldText, e.newText);
    added += s.added;
    removed += s.removed;
  }
  return (
    <details className="msg msg-tool" open>
      <summary>
        <span className="tool-marker">▸</span>
        <span className="tool-name">MultiEdit</span>
        {path && (
          <span className="tool-summary-detail mono">
            {shortenPath(path)}
          </span>
        )}
        <span className="tool-summary-meta">{edits.length} edits</span>
        <DiffStatRaw added={added} removed={removed} />
      </summary>
      <div className="tool-call-body">
        {edits.map((e, i) => (
          <DiffView
            key={i}
            oldText={e.oldText}
            newText={e.newText}
            lang={path ? inferLanguage(path) : null}
          />
        ))}
        {result?.isError && (
          <ToolResultBody content={result.content} isError={result.isError} />
        )}
      </div>
    </details>
  );
}

function WriteCallView(
  args: Record<string, unknown>,
  result?: ToolResultPayload,
) {
  const path = stringArg(args, "path");
  const content = stringArg(args, "content");
  const lineCount = content ? content.split("\n").length : 0;
  return (
    <details className="msg msg-tool">
      <summary>
        <span className="tool-marker">▸</span>
        <span className="tool-name">Write</span>
        {path && (
          <span className="tool-summary-detail mono">
            {shortenPath(path)}
          </span>
        )}
        <span className="tool-summary-meta">{lineCount} lines</span>
      </summary>
      <MarkdownText
        text={"```" + inferLanguage(path) + "\n" + content + "\n```"}
      />
      {result && (
        <ToolResultBody content={result.content} isError={result.isError} />
      )}
    </details>
  );
}

function ReadCallView(
  args: Record<string, unknown>,
  result?: ToolResultPayload,
) {
  const path = stringArg(args, "path");
  const lineRange = stringArg(args, "line_range");
  const range = lineRange ? ` · L${lineRange}` : "";
  return (
    <CompactCallView
      name="Read"
      detail={path ? shortenPath(path) + range : null}
      result={result}
    />
  );
}

function GrepCallView(
  args: Record<string, unknown>,
  result?: ToolResultPayload,
) {
  const pattern = stringArg(args, "pattern");
  const path = stringArg(args, "path");
  return (
    <CompactCallView
      name="Grep"
      detail={pattern}
      meta={path ? "in " + shortenPath(path) : null}
      result={result}
    />
  );
}

function GlobCallView(
  args: Record<string, unknown>,
  result?: ToolResultPayload,
) {
  const pattern = stringArg(args, "pattern");
  const path = stringArg(args, "path");
  return (
    <CompactCallView
      name="Glob"
      detail={pattern}
      meta={path ? "in " + shortenPath(path) : null}
      result={result}
    />
  );
}

function CompactCallView({
  name,
  detail,
  meta,
  result,
}: {
  name: string;
  detail?: string | null;
  meta?: string | null;
  result?: ToolResultPayload;
}) {
  const summary = (
    <>
      <span className="tool-marker">▸</span>
      <span className="tool-name">{name}</span>
      {detail && <span className="tool-summary-detail mono">{detail}</span>}
      {meta && <span className="tool-summary-meta">{meta}</span>}
    </>
  );
  if (!result) {
    return <div className="msg msg-tool tool-call-line">{summary}</div>;
  }
  return (
    <details className="msg msg-tool" open={resultLooksLikeDiff(result)}>
      <summary>{summary}</summary>
      <ToolResultBody content={result.content} isError={result.isError} />
    </details>
  );
}

function ToolResultBody({
  content,
  isError,
}: {
  content: string;
  isError: boolean;
}) {
  // Unwrap Amp's {output, exitCode} wrapper so the user sees the raw
  // stdout instead of escaped JSON. Claude Code returns the stdout
  // directly; that path falls through with `parsed === null`.
  const parsed = parseBashResult(content);
  const text = parsed?.output ?? content;
  const exitCode = parsed?.exitCode;
  const showAsDiff = isUnifiedDiff(text);
  return (
    <div className={`tool-result-section${isError ? " is-error" : ""}`}>
      <div className="tool-result-header">
        <span className="tool-marker">{isError ? "⚠" : "→"}</span>
        <span className="tool-name">result</span>
        {exitCode !== undefined && exitCode !== 0 && (
          <span className="tool-summary-meta">exit {exitCode}</span>
        )}
      </div>
      {showAsDiff ? (
        <UnifiedDiffView text={text} />
      ) : (
        <pre className="tool-result-body">{text}</pre>
      )}
    </div>
  );
}

function parseBashResult(
  content: string,
): { output: string; exitCode: number } | null {
  try {
    const obj = JSON.parse(content) as unknown;
    if (
      obj &&
      typeof obj === "object" &&
      typeof (obj as { output?: unknown }).output === "string" &&
      typeof (obj as { exitCode?: unknown }).exitCode === "number"
    ) {
      const o = obj as { output: string; exitCode: number };
      return { output: o.output, exitCode: o.exitCode };
    }
  } catch {
    // Not JSON — fall through.
  }
  return null;
}

function resultLooksLikeDiff(result?: ToolResultPayload): boolean {
  if (!result) return false;
  const text = parseBashResult(result.content)?.output ?? result.content;
  return isUnifiedDiff(text);
}

function isUnifiedDiff(text: string): boolean {
  return (
    /^diff --git /m.test(text) ||
    /^@@ -\d+(?:,\d+)? \+\d+(?:,\d+)? @@/m.test(text)
  );
}

function UnifiedDiffView({ text }: { text: string }) {
  const lines = useMemo(() => text.split("\n"), [text]);
  // Recover the file's language from the diff header so code inside
  // +/-/context lines picks up syntax highlighting.
  const lang = useMemo(() => {
    const m = text.match(/^diff --git a\/(.+?) b\//m);
    return m ? inferLanguage(m[1]) || null : null;
  }, [text]);
  // Strip diff prefixes and rejoin so Shiki sees a pure-code snippet —
  // preserves multi-line context (strings, blocks) for typical
  // edit-shaped diffs. Meta lines stay as empty placeholders so token
  // line indices align 1:1 with the source lines.
  const codeJoined = useMemo(
    () =>
      lines
        .map((line) =>
          classifyUnifiedLine(line) === "meta" ? "" : line.slice(1),
        )
        .join("\n"),
    [lines],
  );
  const tokens = useShikiLines(codeJoined, lang);
  return (
    <div className="tool-diff" role="figure" aria-label="diff">
      {lines.map((line, i) => {
        const cls = classifyUnifiedLine(line);
        if (cls === "meta") {
          return (
            <div key={i} className="diff-line diff-meta">
              <span className="diff-content">{line || " "}</span>
            </div>
          );
        }
        return (
          <div key={i} className={`diff-line diff-${cls}`}>
            <span className="diff-prefix" aria-hidden>
              {line[0] ?? " "}
            </span>
            <span className="diff-content">
              {renderTokenLine(tokens?.[i], line.slice(1))}
            </span>
          </div>
        );
      })}
    </div>
  );
}

function classifyUnifiedLine(
  line: string,
): "add" | "del" | "ctx" | "meta" {
  // Order matters: +++ / --- (file headers) must be classified as meta
  // before the +/- (added/removed) check.
  if (
    line.startsWith("+++") ||
    line.startsWith("---") ||
    line.startsWith("@@") ||
    line.startsWith("diff ") ||
    line.startsWith("index ") ||
    line.startsWith("new file mode") ||
    line.startsWith("deleted file mode") ||
    line.startsWith("similarity index") ||
    line.startsWith("rename from") ||
    line.startsWith("rename to")
  ) {
    return "meta";
  }
  if (line.startsWith("+")) return "add";
  if (line.startsWith("-")) return "del";
  return "ctx";
}

function DefaultToolCallView({
  name,
  args,
  result,
}: {
  name: string;
  args: Record<string, unknown>;
  result?: ToolResultPayload;
}) {
  return (
    <details className="msg msg-tool" open={resultLooksLikeDiff(result)}>
      <summary>
        <span className="tool-marker">▸</span>
        <span className="tool-name">{name}</span>
      </summary>
      <MarkdownText
        text={"```json\n" + JSON.stringify(args, null, 2) + "\n```"}
      />
      {result && (
        <ToolResultBody content={result.content} isError={result.isError} />
      )}
    </details>
  );
}

function DiffStat({
  oldText,
  newText,
}: {
  oldText: string;
  newText: string;
}) {
  const stat = useMemo(
    () => countDiffStat(oldText, newText),
    [oldText, newText],
  );
  return <DiffStatRaw added={stat.added} removed={stat.removed} />;
}

function DiffStatRaw({
  added,
  removed,
}: {
  added: number;
  removed: number;
}) {
  return (
    <span className="tool-summary-stat">
      <span className="diff-add-text">+{added}</span>{" "}
      <span className="diff-del-text">−{removed}</span>
    </span>
  );
}

function DiffView({
  oldText,
  newText,
  lang,
}: {
  oldText: string;
  newText: string;
  lang?: string | null;
}) {
  const entries = useMemo(
    () => buildDiffLines(oldText, newText),
    [oldText, newText],
  );
  // Highlight each side once as a coherent snippet so multi-line context
  // (strings, comments) is preserved. Tokens align 1:1 with source line
  // indices recorded on each entry.
  const oldTokens = useShikiLines(oldText, lang ?? null);
  const newTokens = useShikiLines(newText, lang ?? null);
  return (
    <div className="tool-diff" role="figure" aria-label="diff">
      {entries.map((l, i) => (
        <div key={i} className={`diff-line diff-${l.kind}`}>
          <span className="diff-prefix" aria-hidden>
            {l.kind === "add" ? "+" : l.kind === "del" ? "−" : " "}
          </span>
          <span className="diff-content">
            {renderTokenLine(
              l.kind === "del" ? oldTokens?.[l.oldIdx] : newTokens?.[l.newIdx],
              l.text,
            )}
          </span>
        </div>
      ))}
    </div>
  );
}

type DiffLineEntry = {
  kind: "add" | "del" | "ctx";
  text: string;
  oldIdx: number;
  newIdx: number;
};

function buildDiffLines(oldText: string, newText: string): DiffLineEntry[] {
  const out: DiffLineEntry[] = [];
  let oldIdx = 0;
  let newIdx = 0;
  for (const part of diffLines(oldText, newText)) {
    const kind: "add" | "del" | "ctx" = part.added
      ? "add"
      : part.removed
        ? "del"
        : "ctx";
    const partLines = part.value.split("\n");
    // diff library's `value` typically ends with a trailing newline; the
    // empty last element from split is artificial — drop it.
    if (partLines.length > 1 && partLines[partLines.length - 1] === "") {
      partLines.pop();
    }
    for (const text of partLines) {
      out.push({ kind, text, oldIdx, newIdx });
      if (kind !== "del") newIdx++;
      if (kind !== "add") oldIdx++;
    }
  }
  return out;
}

const SHIKI_THEME = "github-dark";

function useShikiLines(
  text: string,
  lang: string | null,
): ThemedToken[][] | null {
  const [lines, setLines] = useState<ThemedToken[][] | null>(null);
  useEffect(() => {
    if (!lang || !text) {
      setLines(null);
      return;
    }
    let cancelled = false;
    codeToTokensBase(text, {
      // Shiki's lang param is typed as a closed enum, but its runtime
      // accepts any registered grammar id including the aliases we map
      // in inferLanguage. Cast to bypass the strict type.
      lang: lang as never,
      theme: SHIKI_THEME,
    })
      .then((t) => {
        if (!cancelled) setLines(t);
      })
      .catch(() => {
        if (!cancelled) setLines(null);
      });
    return () => {
      cancelled = true;
    };
  }, [text, lang]);
  return lines;
}

function renderTokenLine(
  tokens: ThemedToken[] | undefined,
  fallback: string,
): ReactNode {
  if (!tokens || tokens.length === 0) return fallback || " ";
  return tokens.map((t, i) => (
    <span key={i} style={t.color ? { color: t.color } : undefined}>
      {t.content}
    </span>
  ));
}

function countDiffStat(
  oldText: string,
  newText: string,
): { added: number; removed: number } {
  let added = 0;
  let removed = 0;
  for (const p of diffLines(oldText, newText)) {
    if (!p.count) continue;
    if (p.added) added += p.count;
    else if (p.removed) removed += p.count;
  }
  return { added, removed };
}

function stringArg(args: Record<string, unknown>, key: string): string {
  const v = args[key];
  return typeof v === "string" ? v : "";
}

function inferLanguage(path: string): string {
  const ext = path.match(/\.([a-z0-9]+)$/i)?.[1]?.toLowerCase() ?? "";
  const map: Record<string, string> = {
    ts: "typescript",
    tsx: "tsx",
    js: "javascript",
    jsx: "jsx",
    py: "python",
    rs: "rust",
    go: "go",
    rb: "ruby",
    java: "java",
    c: "c",
    cpp: "cpp",
    cs: "csharp",
    swift: "swift",
    kt: "kotlin",
    sh: "bash",
    bash: "bash",
    zsh: "bash",
    fish: "bash",
    md: "markdown",
    json: "json",
    yaml: "yaml",
    yml: "yaml",
    toml: "toml",
    html: "html",
    css: "css",
    scss: "scss",
    sql: "sql",
  };
  return map[ext] ?? "";
}

function CompactionInlineWarning({
  context,
  level,
  onCompact,
}: {
  context: ContextSnapshot;
  level: CompactionLevel;
  onCompact: () => void;
}) {
  const text =
    level === "notice"
      ? "Context is getting full."
      : level === "recommended"
        ? "Context compaction is recommended."
        : level === "urgent"
          ? "Context is nearly full."
          : "Context is full.";
  return (
    <div className="compaction-inline-warning" data-level={level}>
      <div>
        <span className="compaction-inline-title">{text}</span>{" "}
        <span className="compaction-inline-meta">
          {formatTokens(context.promptTokens)} /{" "}
          {formatTokens(context.contextWindow)} tokens
        </span>
      </div>
      <button type="button" className="btn sm" onClick={onCompact}>
        Compact
      </button>
    </div>
  );
}

function CompactionModal({
  context,
  level,
  compacting,
  error,
  canHandoff,
  onCompact,
  onDefer,
  onHandoff,
}: {
  context: ContextSnapshot;
  level: CompactionLevel;
  compacting: boolean;
  error: string | null;
  canHandoff: boolean;
  onCompact: () => void;
  onDefer?: () => void;
  onHandoff?: () => void;
}) {
  const blocked = level === "blocked";
  const urgent = level === "urgent" || blocked;
  const title = blocked
    ? "Context limit reached"
    : urgent
      ? "Context is nearly full"
      : "Compact this agent soon";
  const body = blocked
    ? "This agent needs a compacted session before it can receive another message."
    : urgent
      ? "Compacting now will preserve the working state before the next turn risks hitting the model limit."
      : "The transcript is large enough that the next few turns may become brittle or expensive.";
  return (
    <div
      className="compaction-modal-layer"
      role="presentation"
      data-level={level}
    >
      <div
        className="compaction-modal"
        role="dialog"
        aria-modal="true"
        aria-label={title}
      >
        <div className="compaction-modal-hd">
          <CompactionIcon />
          <div>
            <h3>{title}</h3>
            <p>{body}</p>
          </div>
        </div>
        <div className="compaction-modal-meter" aria-hidden>
          <span style={{ width: `${Math.min(context.pct, 100)}%` }} />
        </div>
        <div className="compaction-modal-stats">
          <span>{context.pct.toFixed(0)}% used</span>
          <span className="mono">
            {formatTokens(context.promptTokens)} /{" "}
            {formatTokens(context.contextWindow)}
          </span>
        </div>
        {error && <div className="compaction-modal-error">{error}</div>}
        <div className="compaction-modal-actions">
          {blocked && canHandoff && (
            <button type="button" className="btn sm" onClick={onHandoff}>
              Handoff
            </button>
          )}
          {onDefer && (
            <button
              type="button"
              className="btn sm ghost"
              onClick={onDefer}
              disabled={compacting}
            >
              Defer
            </button>
          )}
          <button
            type="button"
            className="btn sm primary"
            onClick={onCompact}
            disabled={compacting}
          >
            {compacting ? "Compacting…" : "Compact now"}
          </button>
        </div>
      </div>
    </div>
  );
}

function CompactionIcon() {
  return (
    <svg viewBox="0 0 16 16" width="18" height="18" aria-hidden>
      <path
        d="M4 3h8M4 13h8M6 6h4M6 10h4M3 4.5L6 8l-3 3.5M13 4.5L10 8l3 3.5"
        stroke="currentColor"
        strokeWidth="1.3"
        fill="none"
        strokeLinecap="round"
        strokeLinejoin="round"
      />
    </svg>
  );
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

function HandoffPrompt({
  threadId,
  switching,
  error,
  onSwitch,
}: {
  threadId: string;
  switching: boolean;
  error: string | null;
  onSwitch: () => void;
}) {
  return (
    <div
      className="handoff-prompt"
      role="alertdialog"
      aria-label="Continue in new thread"
    >
      <div className="handoff-prompt-hd">
        <span className="handoff-prompt-icon" aria-hidden>
          ↪
        </span>
        <span className="handoff-prompt-title">
          Provider handed off to a new thread
        </span>
      </div>
      <div className="handoff-prompt-body">
        <span className="hint">Continue this agent in</span>
        <span className="handoff-prompt-thread mono">{threadId}</span>
      </div>
      {error && <div className="handoff-prompt-error">{error}</div>}
      <div className="handoff-prompt-actions">
        <button
          type="button"
          className="btn sm primary"
          onClick={onSwitch}
          disabled={switching}
        >
          {switching ? "Switching…" : "Continue here"}
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
