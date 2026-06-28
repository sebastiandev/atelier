import {
  type CSSProperties,
  type ClipboardEvent as ReactClipboardEvent,
  type FormEvent,
  type KeyboardEvent as ReactKeyboardEvent,
  type MouseEvent as ReactMouseEvent,
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
const ACTIVE_EVENT_STALE_MS = 5 * 60 * 1000;

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
  uploadImageAttachment,
} from "./api";
import { useConnectionDescriptors } from "./connectionDescriptors";
import { ContextRow } from "./ContextRow";
import { useDragHandle } from "./dragHandleContext";
import { CheckIcon, SearchIcon } from "./Icons";
import { MarkdownText } from "./MarkdownText";
import {
  appendWithSpacing,
  clipboardHasText,
  imageFilesFromClipboard,
  imageFilesFromSystemClipboard,
  isPasteKeyboardShortcut,
  nextImageLabels,
} from "./pasteImages";
import { shortenPath } from "./pathFormat";
import { PermissionApprovalDialog } from "./PermissionApprovalDialog";
import { lookupModelMeta, useProviderDescriptors } from "./providerDescriptors";
import { SimpleContextRow, type SimpleContextType } from "./SimpleContextRow";
import { useArtifactsRefresh } from "./state/artifactsRefresh";
import {
  type AgentEvent,
  type PermissionDecision,
  useAgentStream,
} from "./useAgentStream";

const COMPOSER_MAX_HEIGHT = 200;
const COMPACTION_NOTICE_PCT = 70;
const COMPACTION_RECOMMENDED_PCT = 75;
const COMPACTION_URGENT_PCT = 86;
const COMPACTION_BLOCKED_PCT = 100;

const SIMPLE_PICKER_TYPES: { id: SimpleContextType; label: string }[] = [
  { id: "text", label: "Text" },
  { id: "url", label: "URL" },
  { id: "file", label: "File" },
];

const SIMPLE_CONTEXT_TYPES: ReadonlySet<string> = new Set(["text", "url", "file"]);
export const EFFORT_SESSION_CONFIG_IDS = [
  "thinking_effort",
  "reasoning_effort",
  "effort",
];

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
  /** Open the agent's worktree in the configured editor so the user
   *  can review diffs without leaving their normal IDE flow.
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
    sendSessionConfig,
    sendSessionConfigRefresh,
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
    if (guardBlockedCompaction()) return;
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
    if (guardBlockedCompaction()) return;
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
  const [contextUploadError, setContextUploadError] = useState<string | null>(null);
  const [uploadingImageCount, setUploadingImageCount] = useState(0);
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
  const clipboardFallbackTimerRef = useRef<number | null>(null);
  const systemClipboardPasteInFlightRef = useRef(false);

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
  const sessionModelConfig = useMemo(
    () => latestSessionConfigOption(events, "model"),
    [events],
  );
  const sessionEffortConfig = useMemo(
    () => latestSessionConfigOptionByIds(events, EFFORT_SESSION_CONFIG_IDS),
    [events],
  );
  const liveSessionModelValue =
    typeof sessionModelConfig?.currentValue === "string"
      ? sessionModelConfig.currentValue
      : null;
  const liveSessionEffortValue =
    typeof sessionEffortConfig?.currentValue === "string"
      ? sessionEffortConfig.currentValue
      : null;
  const displayModel = liveSessionModelValue ?? model;
  const sessionConfigOptionsSeq = useMemo(
    () => latestEventSeq(events, "session_config_options"),
    [events],
  );
  const [modelPickerOpen, setModelPickerOpen] = useState(false);
  const [modelQuery, setModelQuery] = useState("");
  const [modelActiveIndex, setModelActiveIndex] = useState(0);
  const [modelRefreshing, setModelRefreshing] = useState(false);
  const modelRefreshStartedSeqRef = useRef(0);
  const modelPickerRef = useRef<HTMLDivElement>(null);
  const modelSearchRef = useRef<HTMLInputElement>(null);
  const modelResultsRef = useRef<HTMLDivElement>(null);
  const modelPickerId = useMemo(
    () => `composer-model-${agentSlug.replace(/[^a-zA-Z0-9_-]/g, "-")}`,
    [agentSlug],
  );
  const filteredSessionModelChoices = useMemo(() => {
    if (sessionModelConfig === null) return [];
    const query = normalizeModelQuery(modelQuery);
    if (!query) return sessionModelConfig.choices;
    const terms = query.split(" ").filter(Boolean);
    return sessionModelConfig.choices.filter((choice) => {
      const haystack = normalizeModelQuery(
        `${choice.name ?? ""} ${String(choice.value)} ${choice.description ?? ""}`,
      );
      return terms.every((term) => haystack.includes(term));
    });
  }, [modelQuery, sessionModelConfig]);
  const { byName: providersByName } = useProviderDescriptors();
  const modelMeta = lookupModelMeta(providersByName, provider, displayModel);
  const latestCompactionSeq = useMemo(
    () =>
      Math.max(
        latestEventSeq(events, "context_compacted"),
        latestEventSeq(events, "provider_context_compacted"),
      ),
    [events],
  );
  const [compactedMetricsSeq, setCompactedMetricsSeq] = useState<number | null>(
    null,
  );
  useEffect(() => {
    if (!lastMetrics || latestCompactionSeq <= lastMetrics.seq) return;
    setCompactedMetricsSeq((prev) => Math.max(prev ?? 0, lastMetrics.seq));
  }, [lastMetrics, latestCompactionSeq]);
  const staleMetricsSeq = Math.max(latestCompactionSeq, compactedMetricsSeq ?? 0);
  const contextSnapshot = useMemo(
    () =>
      lastMetrics && lastMetrics.seq <= staleMetricsSeq
        ? null
        : contextSnapshotFor(lastMetrics, modelMeta),
    [lastMetrics, modelMeta, staleMetricsSeq],
  );
  const compactionLevel = compactionLevelFor(contextSnapshot?.pct ?? null);
  const compactionBlocked = compactionLevel === "blocked";
  const [compactionModalDismissed, setCompactionModalDismissed] = useState(false);
  const [compacting, setCompacting] = useState(false);
  const [compactionDialog, setCompactionDialog] =
    useState<CompactionDialogState | null>(null);
  const shouldOpenCompactionModal = compactionDialog !== null;
  const compactionActivationKeys = new Set(["Enter", " "]);

  useEffect(() => {
    if (compactionDialog?.phase !== "compacting") return;
    const progress = latestCompactionProgressEvent(
      events,
      compactionDialog.progressStartedAt,
    );
    if (progress === null || progress.phase === compactionDialog.progressPhase) {
      return;
    }
    setCompactionDialog((current) =>
      current?.phase === "compacting"
        ? { ...current, progressPhase: progress.phase }
        : current,
    );
  }, [
    compactionDialog?.phase,
    compactionDialog?.progressPhase,
    compactionDialog?.progressStartedAt,
    events,
  ]);

  useEffect(() => {
    if (!compactionBlocked) {
      setCompactionModalDismissed(false);
      setCompactionDialog((current) =>
        current?.level === "blocked" && current.phase !== "compacting"
          ? null
          : current,
      );
      return;
    }
    if (
      contextSnapshot === null ||
      compactionDialog ||
      compactionModalDismissed
    ) {
      return;
    }
    setCompactionDialog(createCompactionDialog(contextSnapshot, compactionLevel));
  }, [
    compactionBlocked,
    compactionDialog,
    compactionLevel,
    contextSnapshot,
    compactionModalDismissed,
  ]);

  useEffect(() => {
    if (compactionDialog?.phase !== "success") return;
    const handle = window.setTimeout(() => setCompactionDialog(null), 800);
    return () => window.clearTimeout(handle);
  }, [compactionDialog?.phase]);

  function openCompactionModal() {
    if (contextSnapshot === null) return;
    setCompactionModalDismissed(false);
    setCompactionDialog(createCompactionDialog(contextSnapshot, compactionLevel));
  }

  function closeCompactionModal() {
    if (compactionDialog?.level === "blocked") {
      setCompactionModalDismissed(true);
    }
    setCompactionDialog(null);
  }

  function guardBlockedCompaction(): boolean {
    if (!compactionBlocked) return false;
    openCompactionModal();
    return true;
  }

  function guardBlockedCompactionEvent(e: {
    preventDefault: () => void;
    stopPropagation: () => void;
  }): boolean {
    if (!guardBlockedCompaction()) return false;
    e.preventDefault();
    e.stopPropagation();
    return true;
  }

  function guardBlockedCompactionKey(e: ReactKeyboardEvent<HTMLElement>): boolean {
    if (!compactionActivationKeys.has(e.key)) return false;
    return guardBlockedCompactionEvent(e);
  }

  async function handleCompact() {
    if (compactionDialog === null) return;
    const currentDialog = compactionDialog;
    const progressStartedAt = Date.now();
    setCompacting(true);
    setCompactionDialog({
      ...currentDialog,
      phase: "compacting",
      error: null,
      progressPhase: "summarizing",
      progressStartedAt,
    });
    try {
      await compactAgent(
        agentSlug,
        currentDialog.level === "blocked" ? "forced_context_limit" : "manual",
      );
      setCompactedMetricsSeq((prev) =>
        lastMetrics ? Math.max(prev ?? 0, lastMetrics.seq) : prev,
      );
      setCompactionDialog((current) => ({
        ...(current ?? currentDialog),
        phase: "success",
        error: null,
        progressPhase: null,
      }));
    } catch (err) {
      setCompactionDialog((current) => ({
        ...(current ?? currentDialog),
        phase: "error",
        error: err instanceof Error ? err.message : String(err),
      }));
    } finally {
      setCompacting(false);
    }
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
    if (guardBlockedCompaction()) return;
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
  useLayoutEffect(() => {
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
        ev.type === "client_error" ||
        ev.type === "message_delta" ||
        ev.type === "message_complete"
      ) {
        setThinkingSinceSeq(null);
        return;
      }
    }
  }, [events, thinkingSinceSeq]);

  useEffect(() => {
    if (thinkingSinceSeq !== null && status !== "connected") {
      setThinkingSinceSeq(null);
    }
  }, [status, thinkingSinceSeq]);

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
    if (guardBlockedCompaction()) return;
    if (uploadingImageCount > 0) return;
    const text = draft.trim();
    if (!text && submittableContexts.length === 0) return;
    sendInput(text || "Review the attached context.", submittableContexts);
    setDraft("");
    setPendingContexts([]);
    setContextUploadError(null);
    setPickerOpen(false);
    setThinkingSinceSeq(events[events.length - 1]?.seq ?? 0);
  }

  function addSimpleContext(type: SimpleContextType) {
    if (guardBlockedCompaction()) return;
    setPendingContexts((prev) => [...prev, { type, value: "", conn_id: null }]);
    setPickerOpen(false);
  }

  function addConnectionContext(type: ConnectionType) {
    if (guardBlockedCompaction()) return;
    setPendingContexts((prev) => [...prev, { type, value: "", conn_id: null }]);
    setPickerOpen(false);
  }

  function patchPendingContext(index: number, next: ContextEntry) {
    if (guardBlockedCompaction()) return;
    setPendingContexts((prev) => prev.map((c, i) => (i === index ? next : c)));
  }

  function removePendingContext(index: number) {
    if (guardBlockedCompaction()) return;
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
    if (compactionBlocked) {
      e.preventDefault();
      openCompactionModal();
      return;
    }
    if (isPasteKeyboardShortcut(e)) {
      scheduleSystemClipboardImagePaste();
      return;
    }
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

  async function uploadPastedImages(images: File[]) {
    setContextUploadError(null);
    setUploadingImageCount((count) => count + images.length);
    try {
      const uploads = await Promise.all(
        images.map((file) => uploadImageAttachment(file, { workSlug })),
      );
      setPendingContexts((prev) => [
        ...prev,
        ...uploads.map((upload) => ({
          type: "file",
          value: upload.path,
          conn_id: null,
        })),
      ]);
    } catch (err) {
      setContextUploadError(err instanceof Error ? err.message : String(err));
    } finally {
      setUploadingImageCount((count) => Math.max(0, count - images.length));
    }
  }

  function addImageMarkers(count: number) {
    const labels = nextImageLabels(draft, count);
    setDraft((current) => appendWithSpacing(current, labels.join(" ")));
  }

  async function handlePaste(e: ReactClipboardEvent<HTMLTextAreaElement>) {
    cancelSystemClipboardImagePaste();
    if (guardBlockedCompaction()) return;
    let images = imageFilesFromClipboard(e.clipboardData);
    if (images.length > 0) {
      e.preventDefault();
      addImageMarkers(images.length);
      await uploadPastedImages(images);
      return;
    }
    if (clipboardHasText(e.clipboardData)) return;
    try {
      images = await imageFilesFromSystemClipboard();
    } catch (err) {
      setContextUploadError(err instanceof Error ? err.message : String(err));
      return;
    }
    if (images.length === 0) return;
    addImageMarkers(images.length);
    await uploadPastedImages(images);
  }

  function scheduleSystemClipboardImagePaste() {
    cancelSystemClipboardImagePaste();
    clipboardFallbackTimerRef.current = window.setTimeout(() => {
      clipboardFallbackTimerRef.current = null;
      void handleSystemClipboardImagePaste();
    }, 80);
  }

  function cancelSystemClipboardImagePaste() {
    if (clipboardFallbackTimerRef.current === null) return;
    window.clearTimeout(clipboardFallbackTimerRef.current);
    clipboardFallbackTimerRef.current = null;
  }

  async function handleSystemClipboardImagePaste() {
    if (systemClipboardPasteInFlightRef.current) return;
    if (guardBlockedCompaction()) return;
    systemClipboardPasteInFlightRef.current = true;
    try {
      const images = await imageFilesFromSystemClipboard();
      if (images.length === 0) return;
      addImageMarkers(images.length);
      await uploadPastedImages(images);
    } catch (err) {
      setContextUploadError(err instanceof Error ? err.message : String(err));
    } finally {
      systemClipboardPasteInFlightRef.current = false;
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
      if (!(e.shiftKey || e.metaKey || e.ctrlKey)) return;
      if (compactionBlocked) {
        e.preventDefault();
        openCompactionModal();
        return;
      }
      setMaximized(false);
    }
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [compactionBlocked, maximized]);

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
  const composerActivity = isCurrentlyActive ? activityPhase ?? "thinking…" : null;
  const composerTone = contextToneFor(contextSnapshot?.pct ?? null);
  const composerPct = clampPct(contextSnapshot?.pct ?? 0);
  const composerStyle = {
    "--ctx-pct": `${composerPct}%`,
  } as CSSProperties;
  const isStopped = status === "stopped";
  // Send only works when the WS is OPEN — otherwise sendInput silently
  // no-ops. Disable the composer for every non-connected state so the
  // user never thinks a click landed.
  const composerDisabled = status !== "connected";
  const sendDisabled = composerDisabled || compacting;
  const sessionModelValue = liveSessionModelValue;
  const sessionModelLabel =
    sessionModelConfig && sessionModelValue
      ? labelForSessionConfigValue(sessionModelConfig, sessionModelValue)
      : null;
  const showSessionModelSelect =
    sessionModelConfig !== null &&
    sessionModelValue !== null &&
    sessionModelConfig.choices.length > 0;
  const sessionModelDisabled =
    composerDisabled || isCurrentlyActive || compactionBlocked;
  const sessionEffortDisabled = sessionModelDisabled;
  const sessionModelTitle = sessionModelLabel
    ? isCurrentlyActive
      ? `Wait for the current turn to finish before changing model (${sessionModelValue})`
      : `Model: ${sessionModelLabel} (${sessionModelValue})`
    : undefined;
  const sessionEffortLabel =
    sessionEffortConfig && liveSessionEffortValue
      ? labelForSessionConfigValue(sessionEffortConfig, liveSessionEffortValue)
      : null;
  const showSessionEffortSelect =
    sessionEffortConfig !== null &&
    liveSessionEffortValue !== null &&
    sessionEffortConfig.choices.length > 0;
  const sessionEffortTitle = sessionEffortLabel
    ? isCurrentlyActive
      ? `Wait for the current turn to finish before changing effort (${liveSessionEffortValue})`
      : `${sessionEffortConfig?.name ?? "Effort"}: ${sessionEffortLabel}`
    : undefined;
  useEffect(() => {
    if (!modelPickerOpen) return;
    requestAnimationFrame(() => modelSearchRef.current?.focus());
  }, [modelPickerOpen]);
  useEffect(() => {
    if (!modelPickerOpen) return;
    setModelActiveIndex(0);
  }, [filteredSessionModelChoices, modelPickerOpen]);
  useEffect(() => {
    if (!modelPickerOpen) return;
    const active = modelResultsRef.current?.querySelector<HTMLElement>(
      '[data-active="true"]',
    );
    active?.scrollIntoView({ block: "nearest" });
  }, [modelActiveIndex, modelPickerOpen]);
  useEffect(() => {
    if (!modelPickerOpen) return;
    const close = (event: Event) => {
      const target = event.target;
      if (
        target instanceof Node &&
        modelPickerRef.current?.contains(target)
      ) {
        return;
      }
      setModelPickerOpen(false);
    };
    window.addEventListener("mousedown", close);
    window.addEventListener("scroll", close, true);
    return () => {
      window.removeEventListener("mousedown", close);
      window.removeEventListener("scroll", close, true);
    };
  }, [modelPickerOpen]);
  useEffect(() => {
    if (!modelPickerOpen || showSessionModelSelect) return;
    setModelPickerOpen(false);
  }, [modelPickerOpen, showSessionModelSelect]);
  useEffect(() => {
    if (!modelRefreshing) return;
    if (sessionConfigOptionsSeq > modelRefreshStartedSeqRef.current) {
      setModelRefreshing(false);
      return;
    }
    const handle = window.setTimeout(() => setModelRefreshing(false), 1500);
    return () => window.clearTimeout(handle);
  }, [modelRefreshing, sessionConfigOptionsSeq]);

  function openSessionModelPicker() {
    if (sessionModelDisabled || guardBlockedCompaction()) return;
    const opening = !modelPickerOpen;
    setModelPickerOpen(opening);
    setModelQuery("");
    setModelActiveIndex(0);
    if (opening) {
      modelRefreshStartedSeqRef.current = sessionConfigOptionsSeq;
      setModelRefreshing(true);
      sendSessionConfigRefresh("model");
    }
  }

  function chooseSessionModel(choice: SessionConfigChoice) {
    if (sessionModelDisabled || guardBlockedCompaction()) return;
    sendSessionConfig("model", choice.value);
    setModelPickerOpen(false);
    setModelQuery("");
  }

  function changeSessionEffort(value: string) {
    if (!sessionEffortConfig || sessionEffortDisabled || guardBlockedCompaction()) {
      return;
    }
    sendSessionConfig(sessionEffortConfig.id, value);
  }

  function handleModelSearchKeyDown(e: ReactKeyboardEvent<HTMLInputElement>) {
    if (e.key === "Escape") {
      e.preventDefault();
      setModelPickerOpen(false);
      return;
    }
    const maxIndex = filteredSessionModelChoices.length - 1;
    if (maxIndex < 0) return;
    if (e.key === "ArrowDown") {
      e.preventDefault();
      setModelActiveIndex((index) => Math.min(index + 1, maxIndex));
      return;
    }
    if (e.key === "ArrowUp") {
      e.preventDefault();
      setModelActiveIndex((index) => Math.max(index - 1, 0));
      return;
    }
    if (e.key === "Home") {
      e.preventDefault();
      setModelActiveIndex(0);
      return;
    }
    if (e.key === "End") {
      e.preventDefault();
      setModelActiveIndex(maxIndex);
      return;
    }
    if (e.key === "Enter") {
      e.preventDefault();
      const choice =
        filteredSessionModelChoices[Math.min(modelActiveIndex, maxIndex)];
      if (choice) chooseSessionModel(choice);
    }
  }

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
                      if (guardBlockedCompactionEvent(e)) return;
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
          {provider && displayModel && (
            <span
              className="provider-pill mono"
              data-provider={shortProvider(provider)}
              {...hintHandlers(`Provider: ${provider} · Model: ${displayModel}`)}
            >
              {providerPillLabel(provider)} · {shortModel(displayModel)}
            </span>
          )}
          <span className="conn-status" data-conn-status={status}>{status}</span>
          {worktreePath && (
            <button
              type="button"
              className="folder-pill mono"
              aria-label={`Reveal worktree — ${worktreePath}`}
              onClick={(e) => {
                if (guardBlockedCompactionEvent(e)) return;
                onRevealWorktree?.();
              }}
              onContextMenu={
                onRevealAtelierDir
                  ? (e) => {
                      if (guardBlockedCompactionEvent(e)) return;
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
                onClick={(e) => {
                  if (guardBlockedCompactionEvent(e)) return;
                  setFolderMenu(null);
                  onRevealWorktree?.();
                }}
              >
                Open worktree
              </button>
              <button
                type="button"
                className="menu-item"
                onClick={(e) => {
                  if (guardBlockedCompactionEvent(e)) return;
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
              onClick={(e) => {
                if (guardBlockedCompactionEvent(e)) return;
                onOpenInIde();
              }}
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
              onClick={(e) => {
                if (guardBlockedCompactionEvent(e)) return;
                onOpenInConsole();
              }}
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
              onClick={(e) => {
                if (guardBlockedCompactionEvent(e)) return;
                onHandoff();
              }}
              {...hintHandlers("Handoff to agent")}
            >
              <HandoffIcon />
            </button>
          )}
          <button
            type="button"
            className="tile-ctl"
            aria-label={maximized ? "Restore" : "Maximize"}
            onClick={(e) => {
              if (guardBlockedCompactionEvent(e)) return;
              setMaximized((m) => !m);
            }}
            {...hintHandlers(maximized ? "Restore" : "Maximize")}
          >
            {maximized ? <RestoreIcon /> : <MaxIcon />}
          </button>
          {onDetach && (
            <button
              type="button"
              className="tile-ctl"
              aria-label="Detach to terminal"
              onClick={(e) => {
                if (guardBlockedCompactionEvent(e)) return;
                onDetach();
              }}
              {...hintHandlers("Detach to CLI")}
            >
              <DetachIcon />
            </button>
          )}
          <button
            type="button"
            className="tile-ctl"
            aria-label={onClose ? "Close" : "Close unavailable"}
            onClick={(e) => {
              if (guardBlockedCompactionEvent(e)) return;
              onClose?.();
            }}
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
        onClickCapture={(e) => {
          if (compactionBlocked) guardBlockedCompactionEvent(e);
        }}
        onKeyDownCapture={(e) => {
          if (compactionBlocked) guardBlockedCompactionKey(e);
        }}
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
            activityPhase={composerActivity}
            context={contextSnapshot}
            compacting={compacting}
            onCompact={openCompactionModal}
          />
        )}
        {pendingPermissions.length > 0 && (
          <PermissionApprovalDialog
            pendingPermissions={pendingPermissions}
            onDecide={(requestId, decision) => {
              if (guardBlockedCompaction()) return false;
              sendPermission(requestId, decision);
            }}
          />
        )}
        {pendingHandoff && (
          <HandoffPrompt
            threadId={pendingHandoff.new_thread_id}
            switching={handoffSwitching}
            error={handoffError}
            onSwitch={async () => {
              if (guardBlockedCompaction()) return;
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
        <form
          className={`composer${composerActivity ? " is-working" : ""}`}
          data-ctx-tone={composerTone}
          style={composerStyle}
          onSubmit={handleSubmit}
        >
          {contextSnapshot && (
            <div className="composer-context-gauge" aria-hidden>
              <span />
            </div>
          )}
          <div className="composer-activity-rail" aria-hidden>
            {composerActivity && <span />}
          </div>
          {contextUploadError && (
            <div className="form-error">{contextUploadError}</div>
          )}
          {uploadingImageCount > 0 && (
            <div className="composer-upload-status mono">
              uploading {uploadingImageCount} image{uploadingImageCount === 1 ? "" : "s"}...
            </div>
          )}
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
            onChange={(e) => {
              if (compactionBlocked) {
                openCompactionModal();
                return;
              }
              setDraft(e.target.value);
            }}
            onClick={(e) => {
              if (compactionBlocked) guardBlockedCompactionEvent(e);
            }}
            onFocus={() => {
              if (compactionBlocked) openCompactionModal();
            }}
            onKeyDown={handleKeyDown}
            onPaste={(e) => void handlePaste(e)}
            placeholder={composerPlaceholder}
            rows={1}
            disabled={composerDisabled}
            readOnly={compactionBlocked}
            autoFocus={mode === "page"}
          />
          <div className="composer-actions">
            <div className="composer-add-context">
              <button
                type="button"
                className="composer-tool"
                onClick={(e) => {
                  if (guardBlockedCompactionEvent(e)) return;
                  setPickerOpen((o) => !o);
                }}
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
            {showSessionModelSelect && (
              <div
                className="composer-model-picker"
                ref={modelPickerRef}
                title={sessionModelTitle}
              >
                <button
                  type="button"
                  className="composer-model-trigger"
                  disabled={sessionModelDisabled}
                  onClick={openSessionModelPicker}
                  aria-haspopup="listbox"
                  aria-expanded={modelPickerOpen}
                >
                  <span className="composer-model-prefix">Model:</span>
                  <span className="composer-model-current">
                    {sessionModelLabel}
                  </span>
                  <span className="composer-model-caret" aria-hidden>
                    ▾
                  </span>
                </button>
                {modelPickerOpen && (
                  <div className="composer-model-menu">
                    <label className="composer-model-search">
                      <SearchIcon size={12} />
                      <input
                        ref={modelSearchRef}
                        value={modelQuery}
                        onChange={(e) => setModelQuery(e.target.value)}
                        onKeyDown={handleModelSearchKeyDown}
                        placeholder="Search models"
                        aria-controls={`${modelPickerId}-results`}
                        aria-activedescendant={
                          filteredSessionModelChoices[modelActiveIndex]
                            ? `${modelPickerId}-option-${modelActiveIndex}`
                            : undefined
                        }
                      />
                    </label>
                    <div
                      ref={modelResultsRef}
                      id={`${modelPickerId}-results`}
                      className="composer-model-results"
                      role="listbox"
                    >
                      {filteredSessionModelChoices.length === 0 ? (
                        <div className="composer-model-empty">No models found</div>
                      ) : (
                        filteredSessionModelChoices.map((choice, index) => {
                          const selected = choice.value === sessionModelValue;
                          const active = index === modelActiveIndex;
                          return (
                            <button
                              key={String(choice.value)}
                              id={`${modelPickerId}-option-${index}`}
                              type="button"
                              className="composer-model-option"
                              data-active={active ? "true" : undefined}
                              data-selected={selected ? "true" : undefined}
                              role="option"
                              aria-selected={selected}
                              onMouseEnter={() => setModelActiveIndex(index)}
                              onClick={() => chooseSessionModel(choice)}
                            >
                              <span className="composer-model-option-check">
                                {selected ? <CheckIcon size={11} /> : null}
                              </span>
                              <span className="composer-model-option-main">
                                <span className="composer-model-option-name">
                                  {choice.name ?? String(choice.value)}
                                </span>
                                <span className="composer-model-option-value">
                                  {String(choice.value)}
                                </span>
                              </span>
                            </button>
                          );
                        })
                      )}
                    </div>
                    <div className="composer-model-foot">
                      {modelRefreshing ? "Refreshing models..." : "Type to filter"}
                    </div>
                  </div>
                )}
              </div>
            )}
            {showSessionEffortSelect && (
              <label
                className="composer-effort-picker"
                title={sessionEffortTitle}
              >
                <span className="composer-effort-prefix">Effort:</span>
                <select
                  className="composer-effort-select"
                  value={liveSessionEffortValue}
                  disabled={sessionEffortDisabled}
                  onChange={(e) => changeSessionEffort(e.target.value)}
                >
                  {sessionEffortConfig.choices.map((choice) => (
                    <option key={String(choice.value)} value={String(choice.value)}>
                      {choice.name ?? String(choice.value)}
                    </option>
                  ))}
                </select>
              </label>
            )}
            <span className="spacer" />
            <button
              type="submit"
              className="composer-send"
              disabled={
                sendDisabled ||
                uploadingImageCount > 0 ||
                (!compactionBlocked && !draft.trim() && submittableContexts.length === 0)
              }
            >
              {uploadingImageCount > 0 ? "Uploading..." : "Send"}
            </button>
          </div>
        </form>
      </div>
      {compactionDialog && (
        <CompactionModal
          dialog={compactionDialog}
          canHandoff={Boolean(onHandoff)}
          onCompact={() => void handleCompact()}
          onClose={
            compactionDialog.phase === "compacting"
              ? undefined
              : closeCompactionModal
          }
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

// Structured diff carried by ACP tool results — lets the diff viewer
// render even when the tool isn't canonical Edit/MultiEdit (the
// provider's raw args may be opaque, but the diff is first-class).
type ToolDiff = { path: string; old_text: string | null; new_text: string };

type ToolResultPayload = { content: string; isError: boolean; diff?: ToolDiff | null };

export type RenderUnit =
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
      // Live status from ACP tool_call_update frames (pending →
      // in_progress → done is implied by `result` arriving). Absent for
      // providers without mid-flight tool updates.
      status?: string;
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
  | {
      kind: "provider_compaction";
      key: number;
      provider: string;
      reason: string;
    }
  | { kind: "permission_resolved"; key: number; decision: PermissionDecision; tool_name: string };

export function groupEvents(events: AgentEvent[]): RenderUnit[] {
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
        diff: parseToolDiff(ev.diff),
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
    } else if (ev.type === "tool_call_update") {
      // Mid-flight ACP update: fold into the matching card; never a row
      // of its own. Unknown tool_id (replay edges) is dropped silently.
      const tid = stringField(ev, "tool_id");
      const matched = tid ? toolCallByTd.get(tid) : undefined;
      const status = stringField(ev, "status");
      if (matched && status) matched.status = status;
    } else if (ev.type === "plan_update") {
      pendingAssistant = null;
      pendingThinking = null;
      const todos = parsePlanEntries(ev.entries);
      if (todos) out.push({ kind: "todo_list", key: ev.seq, todos });
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

function parseToolDiff(raw: unknown): ToolDiff | null {
  if (!raw || typeof raw !== "object") return null;
  const d = raw as { path?: unknown; old_text?: unknown; new_text?: unknown };
  if (typeof d.path !== "string" || typeof d.new_text !== "string") return null;
  return {
    path: d.path,
    old_text: typeof d.old_text === "string" ? d.old_text : null,
    new_text: d.new_text,
  };
}

// ACP plan entries ({content, priority, status}) reuse the todo-list
// row: same statuses, same render. Priority is dropped — the ordering
// the agent chose already encodes it.
function parsePlanEntries(raw: unknown): TodoItem[] | null {
  if (!Array.isArray(raw) || raw.length === 0) return null;
  const out: TodoItem[] = [];
  for (const entry of raw) {
    if (!entry || typeof entry !== "object") return null;
    const content = (entry as { content?: unknown }).content;
    const status = (entry as { status?: unknown }).status;
    if (typeof content !== "string") return null;
    if (status !== "pending" && status !== "in_progress" && status !== "completed") {
      return null;
    }
    out.push({ content, status });
  }
  return out;
}

function renderUnitFor(ev: AgentEvent): RenderUnit | null {
  switch (ev.type) {
    case "mode_change":
      return {
        kind: "status",
        key: ev.seq,
        status: `mode → ${stringField(ev, "mode_id") || "?"}`,
      };
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
    case "client_error":
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
    case "provider_context_compacted":
      return {
        kind: "provider_compaction",
        key: ev.seq,
        provider: stringField(ev, "provider") || "provider",
        reason: stringField(ev, "reason") || "auto",
      };
    case "compaction_failed":
      return {
        kind: "error",
        key: ev.seq,
        message: `Compaction failed: ${
          stringField(ev, "message") || stringField(ev, "error") || "unknown error"
        }`,
      };
    default:
      return null;
  }
}

function stringField(ev: AgentEvent, key: string): string {
  const value = ev[key];
  return typeof value === "string" ? value : "";
}

export function isAgentActive(events: AgentEvent[]): boolean {
  // True iff the agent appears to be doing work *right now*. Reads off
  // the last event's nature, not the cumulative ``agentStatus``, so it
  // recovers gracefully when an adapter forgets to emit the trailing
  // ``status_change("idle")`` (Amp on short turns). Terminal events
  // (message_complete, turn_metrics, error, status_change(idle)) flip
  // back to inactive even if the cumulative status remains "thinking".
  const last = events[events.length - 1];
  if (!last) return false;
  if (isActiveEventStale(last)) return false;
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

function isActiveEventStale(ev: AgentEvent): boolean {
  const ts = Date.parse(ev.ts);
  return Number.isFinite(ts) && Date.now() - ts > ACTIVE_EVENT_STALE_MS;
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

export type SessionConfigValue = string | boolean;
export type SessionConfigChoice = {
  value: SessionConfigValue;
  name?: string;
  description?: string;
};
export type SessionConfigOption = {
  id: string;
  name: string;
  currentValue: SessionConfigValue | null;
  choices: SessionConfigChoice[];
};

export function latestSessionConfigOption(
  events: AgentEvent[],
  configId: string,
): SessionConfigOption | null {
  let option: SessionConfigOption | null = null;
  for (const ev of events) {
    if (ev.type === "session_config_options" && Array.isArray(ev.options)) {
      const raw = ev.options.find(
        (item) =>
          item &&
          typeof item === "object" &&
          (item as Record<string, unknown>).id === configId,
      );
      option = raw ? parseSessionConfigOption(raw) : option;
    } else if (
      ev.type === "session_config_changed" &&
      ev.config_id === configId &&
      option !== null &&
      isSessionConfigValue(ev.value)
    ) {
      option = {
        id: option.id,
        name: option.name,
        choices: option.choices,
        currentValue: ev.value,
      };
    }
  }
  return option;
}

export function latestSessionConfigOptionByIds(
  events: AgentEvent[],
  configIds: readonly string[],
): SessionConfigOption | null {
  for (const configId of configIds) {
    const option = latestSessionConfigOption(events, configId);
    if (option !== null) return option;
  }
  return null;
}

function parseSessionConfigOption(raw: unknown): SessionConfigOption | null {
  if (!raw || typeof raw !== "object") return null;
  const data = raw as Record<string, unknown>;
  if (typeof data.id !== "string" || !data.id) return null;
  const choices = Array.isArray(data.options)
    ? data.options
        .map(parseSessionConfigChoice)
        .filter((choice): choice is SessionConfigChoice => choice !== null)
    : [];
  return {
    id: data.id,
    name: typeof data.name === "string" && data.name ? data.name : data.id,
    currentValue: isSessionConfigValue(data.current_value)
      ? data.current_value
      : null,
    choices,
  };
}

function parseSessionConfigChoice(raw: unknown): SessionConfigChoice | null {
  if (!raw || typeof raw !== "object") return null;
  const data = raw as Record<string, unknown>;
  if (!isSessionConfigValue(data.value)) return null;
  return {
    value: data.value,
    name: typeof data.name === "string" && data.name ? data.name : undefined,
    description:
      typeof data.description === "string" && data.description
        ? data.description
        : undefined,
  };
}

function isSessionConfigValue(value: unknown): value is SessionConfigValue {
  return typeof value === "string" || typeof value === "boolean";
}

export function labelForSessionConfigValue(
  option: SessionConfigOption,
  value: SessionConfigValue,
): string {
  const choice = option.choices.find((item) => item.value === value);
  return choice?.name ?? String(value);
}

function normalizeModelQuery(value: string): string {
  return value.toLowerCase().replace(/[^a-z0-9./:_-]+/g, " ").trim();
}

export type TurnRollup = {
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
  gitBranch: string | null;
  gitHead: string | null;
  gitDetached: boolean;
};

export type ContextSnapshot = {
  pct: number;
  promptTokens: number;
  contextWindow: number;
};

type CompactionLevel = "none" | "notice" | "recommended" | "urgent" | "blocked";
type CompactionDialogPhase = "confirm" | "compacting" | "success" | "error";
type CompactionProgressPhase =
  | "summarizing"
  | "starting_session"
  | "linking_session";
type CompactionDialogState = {
  context: ContextSnapshot;
  level: CompactionLevel;
  tone: ContextTone;
  phase: CompactionDialogPhase;
  progressPhase: CompactionProgressPhase | null;
  progressStartedAt: number | null;
  error: string | null;
};

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
export function deriveActivityPhase(
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

export function latestMetrics(events: AgentEvent[]): TurnRollup | null {
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
      gitBranch: typeof ev.git_branch === "string" ? ev.git_branch : null,
      gitHead: typeof ev.git_head === "string" ? ev.git_head : null,
      gitDetached: ev.git_detached === true,
    };
  }
  return null;
}

export function latestEventSeq(events: AgentEvent[], type: string): number {
  for (let i = events.length - 1; i >= 0; i--) {
    if (events[i].type === type) return events[i].seq;
  }
  return 0;
}

function latestCompactionProgressEvent(
  events: AgentEvent[],
  startedAt: number | null,
): { phase: CompactionProgressPhase; seq: number } | null {
  const minTs = startedAt === null ? 0 : startedAt - 2000;
  for (let i = events.length - 1; i >= 0; i--) {
    const ev = events[i];
    if (ev.type !== "compaction_progress") continue;
    const phase = stringField(ev, "phase");
    if (!isCompactionProgressPhase(phase)) continue;
    const eventTs = Date.parse(ev.ts);
    if (Number.isFinite(eventTs) && eventTs < minTs) continue;
    return { phase, seq: ev.seq };
  }
  return null;
}

function isCompactionProgressPhase(value: string): value is CompactionProgressPhase {
  return (
    value === "summarizing" ||
    value === "starting_session" ||
    value === "linking_session"
  );
}

export type SessionTotals = {
  inputTokens: number;
  outputTokens: number;
  cacheReadTokens: number;
  cacheCreationTokens: number;
  // Provider-reported cumulative session cost (ACP usage_update). The
  // protocol reports a running total, so the latest value wins — never
  // sum it across turns. Null when the provider doesn't report cost.
  reportedCostUsd: number | null;
};

export function sessionMetrics(events: AgentEvent[]): SessionTotals {
  const totals: SessionTotals = {
    inputTokens: 0,
    outputTokens: 0,
    cacheReadTokens: 0,
    cacheCreationTokens: 0,
    reportedCostUsd: null,
  };
  for (const ev of events) {
    if (ev.type !== "turn_metrics") continue;
    totals.inputTokens += numberField(ev, "input_tokens");
    totals.outputTokens += numberField(ev, "output_tokens");
    totals.cacheReadTokens += numberField(ev, "cache_read_input_tokens");
    totals.cacheCreationTokens += numberField(ev, "cache_creation_input_tokens");
    if (typeof ev.cost_usd === "number") {
      totals.reportedCostUsd = ev.cost_usd;
    }
  }
  return totals;
}

function numberField(ev: AgentEvent, key: string): number {
  const value = ev[key];
  return typeof value === "number" ? value : 0;
}

export function contextSnapshotFor(
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

function shortSha(sha: string | null): string | null {
  if (!sha) return null;
  return sha.length > 8 ? sha.slice(0, 8) : sha;
}

function gitLabelFor(metrics: TurnRollup): string | null {
  if (metrics.gitBranch) return metrics.gitBranch;
  if (metrics.gitDetached) return "DETACHED HEAD";
  return null;
}

function BranchMetricIcon() {
  return (
    <svg
      width="12"
      height="12"
      viewBox="0 0 12 12"
      fill="none"
      stroke="currentColor"
      strokeWidth="1.2"
      strokeLinecap="round"
      strokeLinejoin="round"
      aria-hidden
    >
      <circle cx="3" cy="2.5" r="1" />
      <circle cx="3" cy="9.5" r="1" />
      <circle cx="9" cy="6" r="1" />
      <path d="M3 3.5v5" />
      <path d="M3 6h2a3 3 0 0 0 3-3" />
    </svg>
  );
}

export function TurnMetricsBar({
  metrics,
  session,
  meta,
  activityPhase,
  context,
  compacting,
  onCompact,
  compactTitle = "Compact this agent's context",
}: {
  metrics: TurnRollup | null;
  session: SessionTotals;
  meta: ModelMeta | null;
  activityPhase: string | null;
  context: ContextSnapshot | null;
  compacting: boolean;
  onCompact?: () => void;
  compactTitle?: string;
}) {
  const activityNode = (
    <span
      className="turn-metrics-activity-label"
      data-active={activityPhase ? "true" : "false"}
    >
      {activityPhase ?? "idle"}
    </span>
  );
  const ctxTone = contextToneFor(context?.pct ?? null);
  const showCompact =
    context !== null &&
    context.pct >= COMPACTION_RECOMMENDED_PCT &&
    Boolean(onCompact) &&
    !compacting;
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
  const sessionCost = computeSessionCost(session, meta);
  const gitLabel = gitLabelFor(metrics);
  const gitHead = shortSha(metrics.gitHead);
  const tooltipLines = [
    `Duration: ${formatDuration(metrics.durationMs)}`,
    `Input: ${metrics.inputTokens.toLocaleString()}`,
    `Output: ${metrics.outputTokens.toLocaleString()}`,
    `Cache read: ${metrics.cacheReadTokens.toLocaleString()}`,
    `Cache write: ${metrics.cacheCreationTokens.toLocaleString()}`,
  ];
  if (gitLabel !== null) {
    tooltipLines.unshift(
      `Git: ${gitLabel}${gitHead !== null ? ` @ ${gitHead}` : ""}`,
    );
  }
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
      {gitLabel !== null && (
        <>
          <span className="turn-metrics-item turn-metrics-git">
            <BranchMetricIcon />
            <span className="turn-metrics-git-label">{gitLabel}</span>
          </span>
          <span className="turn-metrics-sep">·</span>
        </>
      )}
      <span className="turn-metrics-item">{formatDuration(metrics.durationMs)}</span>
      <span className="turn-metrics-sep">·</span>
      <span className="turn-metrics-item">↓ {formatTokens(totalTokens)} tokens</span>
      {contextWindow !== null && (
        <>
          <span className="turn-metrics-sep">·</span>
          <span
            className={`turn-metrics-item turn-metrics-ctx is-${ctxTone}${
              compacting ? " is-compacting" : ""
            }`}
          >
            {compacting
              ? "compacting…"
              : ctxPct !== null
                ? `ctx ${ctxPct.toFixed(0)}%`
                : "ctx —"}
          </span>
        </>
      )}
      {sessionCost !== null && (
        <>
          <span className="turn-metrics-sep">·</span>
          <span className="turn-metrics-item">{formatCost(sessionCost)}</span>
        </>
      )}
      {showCompact && (
        <button
          type="button"
          className="turn-metrics-compact"
          data-tone={ctxTone}
          onClick={onCompact}
          title={compactTitle}
        >
          Compact
        </button>
      )}
      {activityNode}
    </div>
  );
}

type ContextTone = "ok" | "warn" | "crit";

export function contextToneFor(pct: number | null): ContextTone {
  if (pct === null) return "ok";
  if (pct >= COMPACTION_URGENT_PCT) return "crit";
  if (pct >= COMPACTION_RECOMMENDED_PCT) return "warn";
  return "ok";
}

function createCompactionDialog(
  context: ContextSnapshot,
  level: CompactionLevel,
): CompactionDialogState {
  return {
    context,
    level,
    tone: contextToneFor(context.pct),
    phase: "confirm",
    progressPhase: null,
    progressStartedAt: null,
    error: null,
  };
}

function clampPct(pct: number): number {
  if (!Number.isFinite(pct)) return 0;
  return Math.max(0, Math.min(100, pct));
}

function compactionProgressCopy(phase: CompactionProgressPhase | null): {
  label: string;
  description: string;
  action: string;
} {
  switch (phase) {
    case "starting_session":
      return {
        label: "Starting fresh session",
        description: "The summary is ready. Starting a fresh provider session from it.",
        action: "Starting session...",
      };
    case "linking_session":
      return {
        label: "Linking previous session",
        description: "The fresh session is ready. Linking the previous session for traceability.",
        action: "Linking session...",
      };
    case "summarizing":
    default:
      return {
        label: "Summarizing transcript",
        description:
          "Summarizing older turns with the provider. Large or legacy sessions can take a couple minutes.",
        action: "Summarizing...",
      };
  }
}

function formatElapsedSeconds(seconds: number): string {
  const safe = Math.max(0, Math.floor(seconds));
  const minutes = Math.floor(safe / 60);
  const remainder = safe % 60;
  if (minutes === 0) return `${remainder}s`;
  return `${minutes}m ${remainder.toString().padStart(2, "0")}s`;
}

function computeSessionCost(
  session: SessionTotals,
  meta: ModelMeta | null,
): number | null {
  // Authoritative provider-reported cost beats any token-math estimate.
  if (session.reportedCostUsd !== null) return session.reportedCostUsd;
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
  // ACP runtimes reuse their base provider's pill tint; the ⌁ prefix in
  // the label (not the tint) is what tells them apart.
  if (provider === "claude-acp") return "claude";
  if (provider === "codex-acp") return "codex";
  return provider;
}

function providerPillLabel(provider: string): string {
  return shortProvider(provider);
}

function shortModel(model: string): string {
  // Claude model ids are prefixed (claude-opus-4-8, claude-sonnet-4-6);
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

type CompactionSummaryLoader = (
  resourceSlug: string,
  filename: string,
) => Promise<{ content: string }>;

export function TranscriptUnits({
  units,
  agentSlug,
  compactionSummaryLoader = getAgentCompactionSummary,
}: {
  units: RenderUnit[];
  agentSlug: string;
  compactionSummaryLoader?: CompactionSummaryLoader;
}) {
  const compactionIndex = findLastUnitIndex(units, (unit) => unit.kind === "compaction");
  if (compactionIndex <= 0) {
    return (
      <>
        {units.map((unit) => (
          <Unit
            key={unit.key}
            unit={unit}
            agentSlug={agentSlug}
            compactionSummaryLoader={compactionSummaryLoader}
          />
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
            <Unit
              key={unit.key}
              unit={unit}
              agentSlug={agentSlug}
              compactionSummaryLoader={compactionSummaryLoader}
            />
          ))}
        </div>
      </details>
      <Unit
        unit={boundary}
        agentSlug={agentSlug}
        compactionSummaryLoader={compactionSummaryLoader}
      />
      {newUnits.map((unit) => (
        <Unit
          key={unit.key}
          unit={unit}
          agentSlug={agentSlug}
          compactionSummaryLoader={compactionSummaryLoader}
        />
      ))}
    </>
  );
}

function Unit({
  unit,
  agentSlug,
  compactionSummaryLoader,
}: {
  unit: RenderUnit;
  agentSlug: string;
  compactionSummaryLoader: CompactionSummaryLoader;
}) {
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
          status={unit.status}
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
      return (
        <CompactionBoundary
          unit={unit}
          agentSlug={agentSlug}
          compactionSummaryLoader={compactionSummaryLoader}
        />
      );
    case "provider_compaction":
      return <ProviderCompactionBoundary unit={unit} />;
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

function ProviderCompactionBoundary({
  unit,
}: {
  unit: Extract<RenderUnit, { kind: "provider_compaction" }>;
}) {
  const providerLabel = unit.provider === "codex" ? "Codex" : unit.provider;
  return (
    <div className="msg msg-compaction is-provider">
      <div className="msg-compaction-title">
        {providerLabel} compacted context automatically
      </div>
      <div className="msg-compaction-body">
        The provider reduced the active prompt context. The local transcript is unchanged.
      </div>
      <div className="msg-compaction-meta">{unit.reason}</div>
    </div>
  );
}

function CompactionBoundary({
  unit,
  agentSlug,
  compactionSummaryLoader,
}: {
  unit: Extract<RenderUnit, { kind: "compaction" }>;
  agentSlug: string;
  compactionSummaryLoader: CompactionSummaryLoader;
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
    compactionSummaryLoader(agentSlug, filename)
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
  }, [agentSlug, compactionSummaryLoader, filename, isOpen, summary]);

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
  status,
}: {
  name: string;
  args: Record<string, unknown> | string;
  result?: ToolResultPayload;
  status?: string;
}) {
  // Defensive: an older build of this component stored args as a JSON
  // string. After HMR the cached units still carry that shape, and the
  // new per-tool renderers expect an object. Parse on read so a hard
  // reload isn't required to see the new view.
  const parsed = normalizeArgs(args);
  const renderer = TOOL_RENDERERS[name];
  if (renderer) return <>{renderer(parsed, result)}</>;
  return (
    <DefaultToolCallView name={name} args={parsed} result={result} status={status} />
  );
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
  status,
}: {
  name: string;
  args: Record<string, unknown>;
  result?: ToolResultPayload;
  status?: string;
}) {
  // ACP structured diff: providers whose raw args are opaque (codex-acp
  // wraps them in internal envelopes) still hand us a first-class
  // {path, old_text, new_text} — render the real diff viewer instead of
  // a JSON dump.
  const diff = result?.diff ?? null;
  const running = !result && (status === "pending" || status === "in_progress");
  return (
    <details className="msg msg-tool" open={diff !== null || resultLooksLikeDiff(result)}>
      <summary>
        <span className="tool-marker">▸</span>
        <span className="tool-name">{name}</span>
        {diff && (
          <span className="tool-summary-detail mono">{shortenPath(diff.path)}</span>
        )}
        {running && <span className="tool-summary-detail">{status}…</span>}
      </summary>
      {diff ? (
        <DiffView
          oldText={diff.old_text ?? ""}
          newText={diff.new_text}
          lang={inferLanguage(diff.path)}
        />
      ) : (
        <MarkdownText
          text={"```json\n" + JSON.stringify(args, null, 2) + "\n```"}
        />
      )}
      {result && (!diff || result.isError) && (
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

function CompactionModal({
  dialog,
  canHandoff,
  onCompact,
  onClose,
  onHandoff,
}: {
  dialog: CompactionDialogState;
  canHandoff: boolean;
  onCompact: () => void;
  onClose?: () => void;
  onHandoff?: () => void;
}) {
  const cardRef = useRef<HTMLDivElement>(null);
  const primaryRef = useRef<HTMLButtonElement>(null);
  const { context, error, level, phase, tone } = dialog;
  const [now, setNow] = useState(() => Date.now());
  const blocked = level === "blocked";
  const canCancel = Boolean(onClose) && phase !== "compacting";
  const progress = compactionProgressCopy(dialog.progressPhase);
  const elapsed =
    phase === "compacting" && dialog.progressStartedAt !== null
      ? formatElapsedSeconds(Math.max(0, Math.floor((now - dialog.progressStartedAt) / 1000)))
      : null;
  const title =
    phase === "compacting"
      ? "Compacting agent..."
      : phase === "success"
        ? "Compacted"
        : phase === "error"
          ? "Couldn't compact"
          : "Compact context";
  const body =
    phase === "compacting"
      ? progress.description
      : phase === "success"
        ? "New session started from summary. The next turn will report updated context."
        : phase === "error"
          ? "The summary call failed. Try again or send a shorter next message."
          : "The agent will summarise older turns and reset its working memory. The conversation continues from where you are.";
  const primaryLabel = phase === "error" ? "Try again" : "Compact now";
  const readout = `ctx ${context.pct.toFixed(0)}% · ${formatTokens(
    context.promptTokens,
  )} tokens`;

  useEffect(() => {
    if (phase === "confirm" || phase === "error") {
      primaryRef.current?.focus();
    }
  }, [phase]);

  useEffect(() => {
    if (phase !== "compacting") return;
    setNow(Date.now());
    const handle = window.setInterval(() => setNow(Date.now()), 1000);
    return () => window.clearInterval(handle);
  }, [phase]);

  function handleLayerMouseDown(e: ReactMouseEvent<HTMLDivElement>) {
    if (e.target === e.currentTarget && canCancel) {
      onClose?.();
    }
  }

  function handleKeyDown(e: ReactKeyboardEvent<HTMLDivElement>) {
    if (e.key === "Escape" && canCancel) {
      e.preventDefault();
      onClose?.();
      return;
    }
    if (e.key !== "Tab") return;
    const buttons = Array.from(
      cardRef.current?.querySelectorAll<HTMLButtonElement>("button:not(:disabled)") ??
        [],
    );
    if (buttons.length === 0) {
      e.preventDefault();
      return;
    }
    const first = buttons[0];
    const last = buttons[buttons.length - 1];
    const active = document.activeElement;
    if (e.shiftKey && active === first) {
      e.preventDefault();
      last.focus();
    } else if (!e.shiftKey && active === last) {
      e.preventDefault();
      first.focus();
    }
  }

  return (
    <div
      className="compaction-modal-layer"
      role="presentation"
      data-tone={tone}
      data-phase={phase}
      onMouseDown={handleLayerMouseDown}
    >
      <div
        ref={cardRef}
        className="compaction-modal"
        role="dialog"
        aria-modal="true"
        aria-label={title}
        onKeyDown={handleKeyDown}
      >
        <div className="compaction-modal-hd">
          <div>
            <h3>{title}</h3>
            <p>{body}</p>
          </div>
          {canCancel && (
            <button
              type="button"
              className="compaction-modal-close"
              aria-label="Close compaction dialog"
              onClick={onClose}
            >
              <CloseIcon />
            </button>
          )}
        </div>
        <div className="compaction-modal-facts">
          <div className="compaction-modal-fact">
            <span className="compaction-modal-dot is-good" aria-hidden />
            <span>Keeps recent turns, decisions, and pinned files</span>
          </div>
          <div className="compaction-modal-fact">
            <span className="compaction-modal-dot is-good" aria-hidden />
            <span>Drops verbose tool output and exploration</span>
          </div>
          <div className="compaction-modal-fact">
            <span className="compaction-modal-dot" aria-hidden />
            <span>Can take a couple minutes on large legacy sessions</span>
          </div>
        </div>
        <div className="compaction-modal-progress">
          <div className="compaction-modal-meter" aria-hidden>
            <span style={{ width: `${phase === "success" ? 100 : clampPct(context.pct)}%` }} />
          </div>
          <span className="compaction-modal-readout mono">{readout}</span>
        </div>
        {phase === "compacting" && (
          <div className="compaction-modal-phase" aria-live="polite">
            <span className="compaction-modal-phase-label">{progress.label}</span>
            {elapsed && <span className="compaction-modal-phase-time">{elapsed}</span>}
          </div>
        )}
        {phase === "error" && error && (
          <div className="compaction-modal-error">{error}</div>
        )}
        {phase !== "success" && (
          <div className="compaction-modal-actions">
            {blocked && canHandoff && phase !== "compacting" && (
              <button
                type="button"
                className="compaction-modal-secondary"
                onClick={onHandoff}
              >
                Handoff
              </button>
            )}
            {phase === "compacting" ? (
              <span className="compaction-modal-spinner" aria-live="polite">
                {progress.action}
              </span>
            ) : (
              <button
                ref={primaryRef}
                type="button"
                className="compaction-modal-primary"
                onClick={onCompact}
              >
                {primaryLabel}
              </button>
            )}
          </div>
        )}
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
