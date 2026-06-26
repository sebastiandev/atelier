import {
  type CSSProperties,
  useEffect,
  useLayoutEffect,
  useMemo,
  useRef,
  useState,
} from "react";

import {
  TranscriptUnits,
  TurnMetricsBar,
  EFFORT_SESSION_CONFIG_IDS,
  type ContextSnapshot,
  contextSnapshotFor,
  contextToneFor,
  deriveActivityPhase,
  groupEvents,
  isAgentActive,
  latestEventSeq,
  labelForSessionConfigValue,
  latestSessionConfigOptionByIds,
  latestMetrics,
  sessionMetrics,
} from "./AgentTile";
import {
  type ChatDetail,
  type ChatGrounding,
  type ChatMessage,
  type ChatSummary,
  type CreateChatPayload,
  type ProjectSummary,
  type ProviderDescriptor,
  type WorkChatContextFolder,
  type WorkSummary,
  compactChat,
  createChat,
  deleteChat,
  getChatCompactionSummary,
  getChat,
  getWorkChatContextDoc,
  listOpenCodeModels,
  listProjects,
  listProviders,
  listWorks,
  patchChat,
  promoteChat,
} from "./api";
import { BrandMark } from "./BrandMark";
import { useDragHandle } from "./dragHandleContext";
import {
  anchoredMenuPosition,
  type FloatingMenuPosition,
} from "./floatingMenu";
import {
  ChatIcon,
  DocIcon,
  FolderIcon,
  SearchIcon,
  SendIcon,
  SlidersIcon,
  SparkIcon,
} from "./Icons";
import { FolderPickerDialog } from "./FolderPickerDialog";
import { ModelPicker } from "./ModelPicker";
import { PermissionApprovalDialog } from "./PermissionApprovalDialog";
import {
  coerceProviderOptionsForModel,
  modelPickerOptions,
  optionLabel,
  providerDefaults,
  providerEffortOption,
  providerOptionsPayload,
  providerPermissionOption,
  lookupModelMeta,
  useProviderDescriptors,
  withOpenCodeModelOptions,
} from "./providerDescriptors";
import { ThemeToggle } from "./ThemeToggle";
import {
  type AgentEvent,
  useAgentStream,
} from "./useAgentStream";

export function ChatView({ chatSlug }: { chatSlug: string }) {
  const [chat, setChat] = useState<ChatDetail | null>(null);
  const [projects, setProjects] = useState<ProjectSummary[]>([]);
  const [works, setWorks] = useState<WorkSummary[]>([]);
  const [draft, setDraft] = useState("");
  const [error, setError] = useState<string | null>(null);
  const [compactError, setCompactError] = useState<string | null>(null);
  const [compacting, setCompacting] = useState(false);
  const [promoteOpen, setPromoteOpen] = useState(false);
  const streamRef = useRef<HTMLDivElement>(null);
  const lastRuntimeSeqRef = useRef(0);
  const { byName: providersByName } = useProviderDescriptors();
  const {
    events,
    status: streamStatus,
    sendInput,
    sendStop,
    sendPermission,
    sendSessionConfig,
    pendingPermissions,
  } = useAgentStream(chatSlug, { resource: "chats" });

  async function refresh() {
    try {
      const [c, p, w] = await Promise.all([
        getChat(chatSlug),
        listProjects(),
        listWorks(),
      ]);
      setChat(c);
      setProjects(p);
      setWorks(w);
      setError(null);
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err));
    }
  }

  useEffect(() => {
    void refresh();
  }, [chatSlug]);

  useLayoutEffect(() => {
    streamRef.current?.scrollTo({ top: streamRef.current.scrollHeight });
  }, [events.length]);

  useEffect(() => {
    const lastSeq = latestSeq(events);
    if (lastSeq <= lastRuntimeSeqRef.current) return;
    lastRuntimeSeqRef.current = lastSeq;
    const transcript = chatMessagesFromEvents(events);
    setChat((current) =>
      current
        ? { ...current, transcript, message_count: transcript.length }
        : current,
    );
  }, [events]);

  const runtimeUnits = useMemo(() => groupEvents(events), [events]);
  const isActive = isAgentActive(events);
  const lastMetrics = useMemo(() => latestMetrics(events), [events]);
  const sessionTotals = useMemo(() => sessionMetrics(events), [events]);
  const activityPhase = useMemo(
    () => deriveActivityPhase(events, isActive),
    [events, isActive],
  );
  const modelMeta = chat
    ? lookupModelMeta(providersByName, chat.provider, chat.model)
    : null;
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

  function send() {
    const body = draft.trim();
    if (!body || !chat) return;
    sendInput(body);
    setDraft("");
  }

  async function compactCurrentChat() {
    if (!chat || compacting || isActive) return;
    setCompacting(true);
    try {
      await compactChat(chat.slug);
      setCompactError(null);
      await refresh();
    } catch (err) {
      setCompactError(err instanceof Error ? err.message : String(err));
    } finally {
      setCompacting(false);
    }
  }

  if (error) {
    return (
      <div className="home">
        <div className="form-error">{error}</div>
        <a href="/" className="hint">← back</a>
      </div>
    );
  }
  if (!chat) return <div className="work-loading hint">Loading…</div>;

  const grounding = resolveGrounding(chat.grounding, projects, works);
  const workingFolder = resolveWorkingFolder(chat);
  const providerLabel = providerLabelFor(chat.provider);
  const composerDisabled = streamStatus !== "connected";

  return (
    <div className="shell-v3 narrow-left chat-v3">
      <aside className="shell-left chat-rail">
        <div className="crown">
          <a className="wordmark" href="/" title="Back to workspace">
            <span className="wm-mark" aria-hidden><BrandMark /></span>
            <span className="wm-rest">telier</span>
          </a>
          <div className="crown-actions">
            <a className="btn-icon" href="/" title="Search">
              <SearchIcon size={12} />
            </a>
            <a className="btn-icon" href="/settings" title="Settings">
              <SlidersIcon size={12} />
            </a>
            <ThemeToggle className="btn-icon" />
          </div>
        </div>
        <div className="crumbs-v3">
          <a className="crumb" href="/">← workspace</a>
          <span className="sep">/</span>
          <span className="now">chat</span>
        </div>

        <div className="chat-hero">
          <div className="kind-line"><ChatIcon size={11} /> exploratory chat · {chat.slug}</div>
          <div className="title">{chat.title}</div>
        </div>
        <div className="v3-rule flush" />

        <div className="scrolly">
          <div className="v3-shd"><span>Linked to</span></div>
          <GroundingCard grounding={grounding} />
          <div className="v3-shd"><span>Working folder</span></div>
          <WorkingFolderCard folder={workingFolder} />
          {grounding.kind !== "none" && (
            <div className="chat-posture">
              <span className="dot-mini" /> Linked context. No worktree, no PR.
            </div>
          )}
          <div className="v3-shd"><span>Model</span></div>
          <div className="chat-model-static">
            <span className="cm-prov">{providerLabel}</span>
            <span className="cm-model mono">{chat.model}</span>
          </div>
          <div className="chat-rail-action">
            {chat.promoted_to_work_slug ? (
              <a className="btn" href={`/works/${chat.promoted_to_work_slug}`}>
                <SparkIcon size={12} /> Open {chat.promoted_to_work_slug}
              </a>
            ) : (
              <button className="btn primary" onClick={() => setPromoteOpen(true)}>
                <SparkIcon size={12} /> Start work from this
              </button>
            )}
            <div className="hint mono">
              {chat.promoted_to_work_slug ? "this chat seeded a work unit" : "turn this thread into a tracked work unit"}
            </div>
            {compactError && <div className="form-error compact">{compactError}</div>}
          </div>
        </div>
      </aside>

      <main className="shell-right chat-right">
        <div className="chat-stream" ref={streamRef}>
          <div className="chat-reading">
            <div className="chat-opening">
              {chat.slug} · talking to {chat.model}
              {grounding.kind !== "none" && <> · linked to {grounding.label}</>}
              {" · "}{streamStatus}
            </div>
            <div className="transcript chat-transcript">
              <TranscriptUnits
                units={runtimeUnits}
                agentSlug={chat.slug}
                compactionSummaryLoader={getChatCompactionSummary}
              />
            </div>
            {chat.promoted_to_work_slug && (
              <div className="chat-promoted-note">
                <SparkIcon size={12} /> This conversation became{" "}
                <a href={`/works/${chat.promoted_to_work_slug}`}>{chat.promoted_to_work_slug}</a>.
              </div>
            )}
          </div>
        </div>
        {(lastMetrics || isActive) && (
          <TurnMetricsBar
            metrics={lastMetrics}
            session={sessionTotals}
            meta={modelMeta}
            activityPhase={activityPhase}
            context={contextSnapshot}
            compacting={compacting}
            onCompact={() => void compactCurrentChat()}
            compactTitle="Compact this chat context"
          />
        )}
        <div className="chat-composer-wrap">
          {pendingPermissions.length > 0 && (
            <PermissionApprovalDialog
              pendingPermissions={pendingPermissions}
              onDecide={sendPermission}
            />
          )}
          <div className="chat-composer">
            <ChatContextGauge context={contextSnapshot} />
            <textarea
              rows={1}
              value={draft}
              onChange={(e) => setDraft(e.target.value)}
              onKeyDown={(e) => {
                if (e.key === "Enter" && !e.shiftKey) {
                  e.preventDefault();
                  send();
                }
                if (e.key === "Escape" && isActive) {
                  e.preventDefault();
                  sendStop();
                }
              }}
              placeholder={`Message ${chat.model}...`}
              disabled={composerDisabled}
            />
            <div className="row">
              <span className="hint mono">Enter send · Shift+Enter newline</span>
              <span className="spacer" />
              <LiveEffortSelect
                events={events}
                disabled={composerDisabled || isActive}
                onChange={sendSessionConfig}
              />
              {!chat.promoted_to_work_slug && (
                <button className="btn sm" onClick={() => setPromoteOpen(true)}>
                  <SparkIcon size={11} /> Start work
                </button>
              )}
              <button
                className="btn primary sm"
                disabled={composerDisabled || !draft.trim()}
                onClick={send}
              >
                <SendIcon size={12} /> Send
              </button>
            </div>
          </div>
        </div>
      </main>

      {promoteOpen && (
        <PromoteChatModal
          chat={chat}
          projects={projects}
          works={works}
          onClose={() => setPromoteOpen(false)}
          onPromoted={(workSlug) => {
            window.location.assign(`/works/${workSlug}`);
          }}
        />
      )}
    </div>
  );
}

export function ChatTile({
  chatSlug,
  chatSummary,
  projects,
  works,
  onClose,
  onStartAgent,
  onUpdated,
}: {
  chatSlug: string;
  chatSummary?: ChatSummary;
  projects: ProjectSummary[];
  works: WorkSummary[];
  onClose: () => void;
  onStartAgent?: (chat: ChatDetail) => Promise<void> | void;
  onUpdated?: (chat: ChatSummary) => void;
}) {
  const [chat, setChat] = useState<ChatDetail | null>(null);
  const [draft, setDraft] = useState("");
  const [error, setError] = useState<string | null>(null);
  const [maximized, setMaximized] = useState(false);
  const [hint, setHint] = useState<string | null>(null);
  const [startingAgent, setStartingAgent] = useState(false);
  const [compacting, setCompacting] = useState(false);
  const [editingTitle, setEditingTitle] = useState(false);
  const [draftTitle, setDraftTitle] = useState("");
  const [renameError, setRenameError] = useState<string | null>(null);
  const streamRef = useRef<HTMLDivElement>(null);
  const titleInputRef = useRef<HTMLInputElement>(null);
  const lastSummarySeqRef = useRef(0);
  const dragHandle = useDragHandle();
  const { byName: providersByName } = useProviderDescriptors();
  const {
    events,
    status: streamStatus,
    sendInput,
    sendStop,
    sendPermission,
    sendSessionConfig,
    pendingPermissions,
  } = useAgentStream(chatSlug, { resource: "chats" });

  useEffect(() => {
    let cancelled = false;
    getChat(chatSlug)
      .then((next) => {
        if (cancelled) return;
        setChat(next);
        setError(null);
      })
      .catch((err) => {
        if (!cancelled) {
          setError(err instanceof Error ? err.message : String(err));
        }
      });
    return () => {
      cancelled = true;
    };
  }, [chatSlug]);

  useLayoutEffect(() => {
    streamRef.current?.scrollTo({ top: streamRef.current.scrollHeight });
  }, [events.length]);

  useEffect(() => {
    if (editingTitle) {
      titleInputRef.current?.focus();
      titleInputRef.current?.select();
    }
  }, [editingTitle]);

  useEffect(() => {
    if (!editingTitle) setDraftTitle(chat?.title ?? "");
  }, [chat?.title, editingTitle]);

  useEffect(() => {
    if (!chatSummary) return;
    setChat((current) => {
      if (!current || current.slug !== chatSummary.slug) return current;
      if (
        current.title === chatSummary.title &&
        current.updated_at === chatSummary.updated_at &&
        current.working_directory === chatSummary.working_directory &&
        current.promoted_to_work_slug === chatSummary.promoted_to_work_slug
      ) {
        return current;
      }
      return {
        ...current,
        title: chatSummary.title,
        updated_at: chatSummary.updated_at,
        message_count: chatSummary.message_count,
        grounding: chatSummary.grounding,
        working_directory: chatSummary.working_directory,
        promoted_to_work_slug: chatSummary.promoted_to_work_slug,
      };
    });
  }, [chatSummary]);

  useEffect(() => {
    if (!chat || !onUpdated || events.length === 0) return;
    const lastSeq = latestSeq(events);
    if (lastSeq <= lastSummarySeqRef.current) return;
    lastSummarySeqRef.current = lastSeq;
    const transcript = chatMessagesFromEvents(events);
    onUpdated(
      chatSummaryFromDetail({
        ...chat,
        transcript,
        message_count: transcript.length,
      }),
    );
  }, [chat, events, onUpdated]);

  const runtimeUnits = useMemo(() => groupEvents(events), [events]);
  const isActive = isAgentActive(events);
  const lastMetrics = useMemo(() => latestMetrics(events), [events]);
  const sessionTotals = useMemo(() => sessionMetrics(events), [events]);
  const activityPhase = useMemo(
    () => deriveActivityPhase(events, isActive),
    [events, isActive],
  );
  const modelMeta = chat
    ? lookupModelMeta(providersByName, chat.provider, chat.model)
    : null;
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

  const hintHandlers = (text: string) => ({
    onMouseEnter: () => setHint(text),
    onMouseLeave: () => setHint((current) => (current === text ? null : current)),
    onFocus: () => setHint(text),
    onBlur: () => setHint((current) => (current === text ? null : current)),
  });

  function send() {
    if (!chat) return;
    const body = draft.trim();
    if (!body) return;
    sendInput(body);
    setDraft("");
  }

  async function startAgent() {
    if (!chat || !onStartAgent) return;
    setStartingAgent(true);
    try {
      await onStartAgent(chat);
      setError(null);
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err));
    } finally {
      setStartingAgent(false);
    }
  }

  async function compactCurrentChat() {
    if (!chat || compacting || isActive) return;
    setCompacting(true);
    try {
      await compactChat(chat.slug);
      setError(null);
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err));
    } finally {
      setCompacting(false);
    }
  }

  function startRename() {
    if (!chat) return;
    setDraftTitle(chat.title);
    setRenameError(null);
    setEditingTitle(true);
  }

  function cancelRename() {
    setEditingTitle(false);
    setDraftTitle(chat?.title ?? "");
    setRenameError(null);
  }

  async function commitRename() {
    if (!chat) {
      cancelRename();
      return;
    }
    const next = draftTitle.trim();
    if (!next || next === chat.title) {
      cancelRename();
      return;
    }
    try {
      const updated = await patchChat(chat.slug, { title: next });
      setChat(updated);
      onUpdated?.(chatSummaryFromDetail(updated));
      setEditingTitle(false);
      setRenameError(null);
    } catch (err) {
      setRenameError(err instanceof Error ? err.message : String(err));
    }
  }

  const grounding = chat
    ? resolveGrounding(chat.grounding, projects, works)
    : { kind: "none" as const, label: "Loading", sub: "" };
  const showGrounding = grounding.kind !== "work";
  const composerDisabled = !chat || streamStatus !== "connected";
  const dotStatus = error
    ? "error"
    : isActive
      ? "thinking"
      : streamStatus === "connected"
        ? "idle"
        : streamStatus;
  const tileClass =
    "agent-tile chat-tile mode-tile" + (maximized ? " maximized" : "");

  useEffect(() => {
    if (!maximized) return;
    function onKey(e: KeyboardEvent) {
      if (e.key !== "Escape") return;
      if (!(e.shiftKey || e.metaKey || e.ctrlKey)) return;
      e.preventDefault();
      setMaximized(false);
    }
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [maximized]);

  return (
    <div className={tileClass} data-chat="true">
      <header
        className={dragHandle ? "tile-drag-header" : undefined}
        {...(dragHandle?.attributes ?? {})}
        {...(dragHandle?.listeners ?? {})}
      >
        <div className="tile-header-left">
          <span className="persona-pip chat-pip">
            <ChatIcon size={12} />
          </span>
          <span className="status-dot" data-status={dotStatus} />
          {editingTitle ? (
            <input
              ref={titleInputRef}
              className="tile-name-input"
              value={draftTitle}
              onChange={(e) => setDraftTitle(e.target.value)}
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
              aria-label="Rename chat"
            />
          ) : (
            <h2
              title="Double-click to rename"
              style={{ cursor: chat ? "text" : undefined }}
              onDoubleClick={(e) => {
                e.stopPropagation();
                startRename();
              }}
            >
              {chat?.title ?? chatSlug}
            </h2>
          )}
          {renameError && !editingTitle && (
            <span className="tile-rename-err">{renameError}</span>
          )}
        </div>
        <div className="tile-header-meta">
          <span className="agent-slug mono">{chat?.slug ?? chatSlug}</span>
          {chat && (
            <span
              className="provider-pill mono"
              data-provider={shortProvider(chat.provider)}
              {...hintHandlers(`Provider: ${providerLabelFor(chat.provider)} · Model: ${chat.model}`)}
            >
              {shortProvider(chat.provider)} · {shortModel(chat.model)}
            </span>
          )}
          {showGrounding && (
            <span
              className="chat-grounding-pill mono"
              {...hintHandlers(
                grounding.kind === "none"
                  ? "Open exploration"
                  : `Linked to ${grounding.label}`,
              )}
            >
              {grounding.label}
            </span>
          )}
          <span className="conn-status" data-conn-status={streamStatus}>
            {streamStatus}
          </span>
        </div>
        <div className="tile-header-right">
          <span
            className={"tile-hint" + (hint ? " visible" : "")}
            aria-hidden="true"
          >
            {hint}
          </span>
          <div className="tile-controls">
            {onStartAgent && (
              <button
                type="button"
                className="tile-ctl"
                aria-label="Start agent from chat"
                onClick={() => void startAgent()}
                disabled={!chat || startingAgent}
                {...hintHandlers("Start agent from chat")}
              >
                <SparkIcon />
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
            <button
              type="button"
              className="tile-ctl"
              aria-label="Close chat tile"
              onClick={onClose}
              {...hintHandlers("Close · stays in Chats")}
            >
              <CloseIcon />
            </button>
          </div>
        </div>
      </header>
      <div className="agent-tile-body chat-tile-body">
        {error && <div className="tile-banner">{error}</div>}
        <div className="transcript chat-tile-transcript" ref={streamRef}>
          {chat ? (
            <>
              <div className="chat-opening">
                {chat.slug} · talking to {chat.model}
                {grounding.kind !== "none" && <> · linked to {grounding.label}</>}
              </div>
              <TranscriptUnits
                units={runtimeUnits}
                agentSlug={chat.slug}
                compactionSummaryLoader={getChatCompactionSummary}
              />
            </>
          ) : (
            <div className="chat-opening">Loading {chatSlug}...</div>
          )}
        </div>
        {(lastMetrics || isActive) && (
          <TurnMetricsBar
            metrics={lastMetrics}
            session={sessionTotals}
            meta={modelMeta}
            activityPhase={activityPhase}
            context={contextSnapshot}
            compacting={compacting}
            onCompact={() => void compactCurrentChat()}
            compactTitle="Compact this chat context"
          />
        )}
        {pendingPermissions.length > 0 && (
          <PermissionApprovalDialog
            pendingPermissions={pendingPermissions}
            onDecide={sendPermission}
          />
        )}
        <form
          className="chat-tile-composer"
          onSubmit={(e) => {
            e.preventDefault();
            send();
          }}
        >
          <ChatContextGauge context={contextSnapshot} />
          <textarea
            rows={1}
            value={draft}
            onChange={(e) => setDraft(e.target.value)}
            onKeyDown={(e) => {
              if (e.key === "Enter" && !e.shiftKey) {
                e.preventDefault();
                send();
              }
              if (e.key === "Escape" && isActive) {
                e.preventDefault();
                sendStop();
              }
            }}
            placeholder={
              chat
                ? streamStatus === "connected"
                  ? "Message this chat - Enter sends, Shift+Enter newline"
                  : "Connecting to chat..."
                : "Loading chat..."
            }
            disabled={composerDisabled}
          />
          <LiveEffortSelect
            events={events}
            disabled={composerDisabled || isActive}
            onChange={sendSessionConfig}
          />
          <button
            type="submit"
            className="composer-send"
            disabled={composerDisabled || !draft.trim()}
          >
            Send
          </button>
        </form>
      </div>
    </div>
  );
}

export function ChatComposer({
  projects,
  works,
  presetGrounding,
  hideGrounding = false,
  linkProjects,
  linkWorks,
  allowNoLink = true,
  onClose,
  onStarted,
}: {
  projects: ProjectSummary[];
  works: WorkSummary[];
  presetGrounding?: ChatGrounding | null;
  hideGrounding?: boolean;
  linkProjects?: ProjectSummary[];
  linkWorks?: WorkSummary[];
  allowNoLink?: boolean;
  onClose: () => void;
  onStarted: (chat: ChatDetail) => void;
}) {
  const [providers, setProviders] = useState<ProviderDescriptor[]>([]);
  const [provider, setProvider] = useState<string>("");
  const [model, setModel] = useState<string>("");
  const [providerOptions, setProviderOptions] = useState<Record<string, string>>({});
  const [opencodeModelsLoading, setOpencodeModelsLoading] = useState(false);
  const [opencodeModelsError, setOpencodeModelsError] = useState<string | null>(null);
  const [grounding, setGrounding] = useState<ChatGrounding | null>(presetGrounding ?? null);
  const [workingDirectory, setWorkingDirectory] = useState<string | null>(null);
  const [message, setMessage] = useState("");
  const [error, setError] = useState<string | null>(null);
  const inputRef = useRef<HTMLTextAreaElement>(null);

  useEffect(() => {
    listProviders()
      .then((rows) => {
        setProviders(rows);
        const first = rows[0];
        if (first) {
          setProvider(first.name);
          setModel(first.primary_field.default);
          setProviderOptions(providerDefaults(first, first.primary_field.default));
        }
      })
      .catch((err) => setError(err instanceof Error ? err.message : String(err)));
    window.setTimeout(() => inputRef.current?.focus(), 50);
  }, []);

  const providerObj = providers.find((p) => p.name === provider);
  const permissionOption = providerObj ? providerPermissionOption(providerObj) : null;
  const effortOption = providerObj ? providerEffortOption(providerObj, model) : null;
  const groundingInfo = resolveGrounding(grounding, projects, works);
  const pickerProjects = linkProjects ?? projects;
  const pickerWorks = linkWorks ?? works;

  useEffect(() => {
    if (!providerObj || !model) return;
    setProviderOptions((prev) =>
      coerceProviderOptionsForModel(providerObj, model, prev),
    );
  }, [providerObj, model]);

  useEffect(() => {
    if (provider !== "opencode") return;
    let cancelled = false;
    setOpencodeModelsLoading(true);
    setOpencodeModelsError(null);
    listOpenCodeModels({ refresh: true })
      .then((rows) => {
        if (cancelled) return;
        setProviders((current) =>
          current.map((p) =>
            p.name === "opencode" ? withOpenCodeModelOptions(p, rows) : p,
          ),
        );
        setModel((current) => {
          if (!current) return "configured-default";
          if (current === "configured-default") return current;
          if (rows.some((row) => row.value === current)) return current;
          return "configured-default";
        });
      })
      .catch((err) => {
        if (!cancelled) {
          setOpencodeModelsError(err instanceof Error ? err.message : String(err));
        }
      })
      .finally(() => {
        if (!cancelled) setOpencodeModelsLoading(false);
      });
    return () => {
      cancelled = true;
    };
  }, [provider]);

  async function start() {
    const first_message = message.trim();
    if (!provider || !model || !first_message) return;
    const payload: CreateChatPayload = {
      provider,
      model,
      first_message,
      grounding,
      working_directory: workingDirectory,
      options: providerOptionsPayload(providerObj, model, providerOptions),
    };
    try {
      const created = await createChat(payload);
      onStarted(created);
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err));
    }
  }

  return (
    <div
      className="chat-bar-scrim"
      onClick={(e) => {
        if (e.target === e.currentTarget) onClose();
      }}
      onKeyDown={(e) => {
        if (e.key === "Escape") onClose();
        if (e.key === "Enter" && (e.metaKey || e.ctrlKey)) {
          e.preventDefault();
          void start();
        }
      }}
    >
      <div className="chat-bar" role="dialog" aria-label="New chat">
        <div className="cb-head">
          <span className="cb-tag"><ChatIcon size={12} /> new chat</span>
          <span className="cb-hint">a quick exploratory conversation</span>
          <span className="esc-tag">esc</span>
        </div>
        <textarea
          ref={inputRef}
          className="cb-input"
          rows={2}
          value={message}
          onChange={(e) => setMessage(e.target.value)}
          placeholder="What's on your mind? Think out loud..."
        />
        <div className="cb-controls">
          <div className="cb-selects">
            <select
              className="cb-select"
              value={provider}
              onChange={(e) => {
                const next = e.target.value;
                const desc = providers.find((p) => p.name === next);
                setProvider(next);
                const defaultModel = desc?.primary_field.default ?? "";
                setModel(defaultModel);
                setProviderOptions(desc ? providerDefaults(desc, defaultModel) : {});
              }}
            >
              {providers.map((p) => (
                <option key={p.name} value={p.name}>{p.label}</option>
              ))}
            </select>
            <span className="cb-model-wrap">
              {providerObj && (
                <ModelPicker
                  id={`new-chat-model-${providerObj.name}`}
                  className="compact"
                  value={model || providerObj.primary_field.default}
                  options={modelPickerOptions(providerObj)}
                  onChange={setModel}
                />
              )}
              {provider === "opencode" && opencodeModelsLoading && (
                <span className="cb-model-hint">refreshing models…</span>
              )}
              {provider === "opencode" &&
                !opencodeModelsLoading &&
                opencodeModelsError && (
                  <span className="cb-model-hint">using OpenCode default</span>
                )}
            </span>
            {permissionOption && (
              <select
                className="cb-select cb-permission-select"
                aria-label={permissionOption.field.label}
                value={
                  providerOptions[permissionOption.key] ??
                  permissionOption.field.default
                }
                onChange={(e) =>
                  setProviderOptions((prev) => ({
                    ...prev,
                    [permissionOption.key]: e.target.value,
                  }))
                }
              >
                {permissionOption.field.values.map((value) => (
                  <option key={value} value={value}>
                    {permissionOption.field.label}:{" "}
                    {optionLabel(permissionOption.field, value)}
                  </option>
                ))}
              </select>
            )}
            {effortOption && (
              <select
                className="cb-select cb-effort-select"
                aria-label={effortOption.field.label}
                value={
                  providerOptions[effortOption.key] ??
                  effortOption.field.default
                }
                onChange={(e) =>
                  setProviderOptions((prev) => ({
                    ...prev,
                    [effortOption.key]: e.target.value,
                  }))
                }
              >
                {effortOption.field.values.map((value) => (
                  <option key={value} value={value}>
                    {effortOption.field.label}:{" "}
                    {optionLabel(effortOption.field, value)}
                  </option>
                ))}
              </select>
            )}
            {!hideGrounding && (
              <GroundingPicker
                projects={pickerProjects}
                works={pickerWorks}
                value={grounding}
                label={groundingInfo.label}
                allowNoLink={allowNoLink}
                onChange={setGrounding}
              />
            )}
            <WorkingFolderPicker
              value={workingDirectory}
              onChange={setWorkingDirectory}
            />
          </div>
          <span className="spacer" />
          {error && <span className="form-error compact">{error}</span>}
          <button className="btn primary sm" disabled={!message.trim() || !provider || !model} onClick={() => void start()}>
            Start chat <span className="kbd" style={{ marginLeft: 4 }}>⌘↵</span>
          </button>
        </div>
      </div>
    </div>
  );
}

export function ChatRow({
  chat,
  projects,
  works,
  dense = false,
  focused = false,
  groundingPlacement = "column",
  onOpen,
  onRenamed,
  onDelete,
  hideGrounding = false,
}: {
  chat: ChatSummary;
  projects: ProjectSummary[];
  works: WorkSummary[];
  dense?: boolean;
  focused?: boolean;
  groundingPlacement?: "column" | "subtitle";
  onOpen?: () => void;
  onRenamed?: (chat: ChatSummary) => void;
  onDelete?: (chat: ChatSummary) => void;
  hideGrounding?: boolean;
}) {
  const [menuOpen, setMenuOpen] = useState(false);
  const [editing, setEditing] = useState(false);
  const [draftTitle, setDraftTitle] = useState(chat.title);
  const [renameError, setRenameError] = useState<string | null>(null);
  const [menuPosition, setMenuPosition] = useState<FloatingMenuPosition | null>(
    null,
  );
  const kebabRef = useRef<HTMLButtonElement>(null);
  const menuRef = useRef<HTMLDivElement>(null);
  const inputRef = useRef<HTMLInputElement>(null);
  const grounding = resolveGrounding(chat.grounding, projects, works);
  const promotedWork = chat.promoted_to_work_slug
    ? works.find((w) => w.slug === chat.promoted_to_work_slug)
    : null;
  const titleContext = hideGrounding
    ? null
    : chat.promoted_to_work_slug
      ? {
          icon: <SparkIcon size={9} />,
          label: promotedWork?.name ?? chat.promoted_to_work_slug,
          prefix: "seeded",
          ref: chat.promoted_to_work_slug,
        }
      : grounding.kind === "none"
        ? null
        : {
            icon:
              grounding.kind === "folder" ? (
                <FolderIcon size={10} />
              ) : (
                <span className="swatch" />
              ),
            label: grounding.label,
            prefix: "in",
            ref: grounding.sub,
          };
  const groundingAsSubtitle = groundingPlacement === "subtitle";
  const className =
    "v3-chat-row" +
    (dense ? " dense" : "") +
    (focused ? " focused" : "") +
    (groundingAsSubtitle ? " grounding-subtitle" : "") +
    (onRenamed || onDelete ? " has-actions" : "");

  useEffect(() => {
    if (!menuOpen) return;
    const handler = () => setMenuOpen(false);
    window.addEventListener("click", handler);
    window.addEventListener("resize", handler);
    window.addEventListener("scroll", handler, true);
    return () => {
      window.removeEventListener("click", handler);
      window.removeEventListener("resize", handler);
      window.removeEventListener("scroll", handler, true);
    };
  }, [menuOpen]);

  useEffect(() => {
    if (!menuOpen || !menuRef.current || !kebabRef.current) return;
    const anchor = kebabRef.current.getBoundingClientRect();
    const menu = menuRef.current.getBoundingClientRect();
    setMenuPosition(
      anchoredMenuPosition(anchor, {
        width: menu.width,
        height: menu.height,
      }),
    );
  }, [menuOpen]);

  useEffect(() => {
    if (editing) {
      inputRef.current?.focus();
      inputRef.current?.select();
    }
  }, [editing]);

  useEffect(() => {
    if (!editing) setDraftTitle(chat.title);
  }, [chat.title, editing]);

  function startRename() {
    if (!onRenamed) return;
    setDraftTitle(chat.title);
    setRenameError(null);
    setEditing(true);
  }

  function cancelRename() {
    setEditing(false);
    setDraftTitle(chat.title);
    setRenameError(null);
  }

  async function commitRename() {
    if (!onRenamed) {
      cancelRename();
      return;
    }
    const next = draftTitle.trim();
    if (!next || next === chat.title) {
      cancelRename();
      return;
    }
    try {
      const updated = await patchChat(chat.slug, { title: next });
      onRenamed(chatSummaryFromDetail(updated));
      setEditing(false);
      setRenameError(null);
    } catch (err) {
      setRenameError(err instanceof Error ? err.message : String(err));
    }
  }

  const content = (
    <>
      <span className="bubble"><ChatIcon size={12} /></span>
      <span className="title">
        {editing ? (
          <input
            ref={inputRef}
            className="rail-agent-name-input mono"
            value={draftTitle}
            onChange={(e) => setDraftTitle(e.target.value)}
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
            onClick={(e) => e.stopPropagation()}
            aria-label="Rename chat"
          />
        ) : (
          <span
            className="title-main"
            onDoubleClick={(e) => {
              if (!onRenamed) return;
              e.preventDefault();
              e.stopPropagation();
              startRename();
            }}
            title={onRenamed ? "Double-click to rename" : undefined}
          >
            {chat.title}
          </span>
        )}
        {titleContext && groundingAsSubtitle && (
          <span
            className="title-context"
            title={titleContext.ref ? `${titleContext.label} (${titleContext.ref})` : titleContext.label}
          >
            {titleContext.icon}
            {titleContext.prefix} {titleContext.label}
          </span>
        )}
      </span>
      {chat.promoted_to_work_slug && !groundingAsSubtitle && (
        <span className="promoted"><SparkIcon size={9} /> {chat.promoted_to_work_slug}</span>
      )}
      {!hideGrounding && !groundingAsSubtitle && (
        <span className={"ground" + (grounding.kind === "none" ? " none" : "")}>
          {grounding.kind === "folder" ? <FolderIcon size={10} /> : grounding.kind === "none" ? "~" : <span className="swatch" />}
          {grounding.label}
        </span>
      )}
      <span className="age">{formatAge(chat.updated_at)}</span>
    </>
  );
  const row = onOpen ? (
    <button
      type="button"
      className={className}
      onClick={editing ? undefined : onOpen}
      disabled={editing}
    >
      {content}
    </button>
  ) : (
    <a className={className} href={`/chats/${chat.slug}`}>
      {content}
    </a>
  );

  if (!onRenamed && !onDelete && !renameError) {
    return row;
  }

  return (
    <div className="chat-row-wrap">
      {row}
      {renameError && !editing && (
        <div className="rail-agent-rename-err">{renameError}</div>
      )}
      {!editing && (onRenamed || onDelete) && (
        <button
          ref={kebabRef}
          type="button"
          className="rail-agent-kebab chat-row-kebab"
          aria-label={`More actions for ${chat.title}`}
          title="More"
          onClick={(e) => {
            e.preventDefault();
            e.stopPropagation();
            if (menuOpen) {
              setMenuOpen(false);
              return;
            }
            setMenuPosition(
              anchoredMenuPosition(e.currentTarget.getBoundingClientRect()),
            );
            setMenuOpen(true);
          }}
        >
          ⋮
        </button>
      )}
      {menuOpen && (
        <div
          ref={menuRef}
          className="rail-agent-menu chat-row-menu floating"
          style={menuPosition ?? undefined}
          onClick={(e) => e.stopPropagation()}
        >
          {onRenamed && (
            <button
              className="menu-item"
              onClick={() => {
                setMenuOpen(false);
                startRename();
              }}
            >
              Rename
            </button>
          )}
          {onDelete && (
            <button
              className="menu-item danger"
              onClick={() => {
                setMenuOpen(false);
                onDelete(chat);
              }}
            >
              Delete chat...
            </button>
          )}
        </div>
      )}
    </div>
  );
}

export function DeleteChatDialog({
  chat,
  onClose,
  onDeleted,
}: {
  chat: ChatSummary;
  onClose: () => void;
  onDeleted: () => void;
}) {
  const [submitting, setSubmitting] = useState(false);
  const [error, setError] = useState<string | null>(null);

  async function submit() {
    setSubmitting(true);
    setError(null);
    try {
      await deleteChat(chat.slug);
      onDeleted();
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err));
      setSubmitting(false);
    }
  }

  return (
    <div className="scrim" onClick={() => !submitting && onClose()}>
      <div
        className="modal modal-sm"
        role="dialog"
        aria-modal="true"
        onClick={(e) => e.stopPropagation()}
      >
        <div className="modal-hd">
          <div>
            <h3>Delete chat {chat.slug}?</h3>
            <p className="sub">{chat.title}</p>
          </div>
          <button
            className="btn-icon"
            onClick={onClose}
            aria-label="Close"
            disabled={submitting}
          >
            x
          </button>
        </div>
        <div className="modal-bd">
          <p style={{ margin: 0, fontSize: 13, color: "var(--fg-2)" }}>
            This permanently removes the chat transcript, metadata, and saved
            compaction summaries.
          </p>
          {chat.promoted_to_work_slug && (
            <p className="hint" style={{ margin: 0 }}>
              The work already created from this chat remains available.
            </p>
          )}
          {error && <div className="form-error">{error}</div>}
        </div>
        <div className="modal-ft">
          <button className="btn" onClick={onClose} disabled={submitting}>
            Cancel
          </button>
          <span className="spacer" />
          <button className="btn danger" onClick={() => void submit()} disabled={submitting}>
            {submitting ? "Deleting..." : "Delete chat"}
          </button>
        </div>
      </div>
    </div>
  );
}

export function ContextDocModal({
  workSlug,
  folder,
  onClose,
}: {
  workSlug: string;
  folder: WorkChatContextFolder;
  onClose: () => void;
}) {
  const [doc, setDoc] = useState<{ path: string; content: string } | null>(null);
  useEffect(() => {
    getWorkChatContextDoc(workSlug, folder.name, folder.context_filename)
      .then(setDoc)
      .catch(() => setDoc({ path: folder.absolute_path, content: "Context document could not be loaded." }));
  }, [workSlug, folder]);

  return (
    <div className="scrim" onClick={(e) => e.target === e.currentTarget && onClose()}>
      <div className="modal context-doc-modal">
        <div className="modal-hd">
          <div>
            <h3><DocIcon size={13} /> {folder.name}/{folder.context_filename}</h3>
            <div className="sub">Shared context for this work, written when the chat was promoted.</div>
          </div>
          <button className="btn-icon" onClick={onClose}>×</button>
        </div>
        <div className="modal-bd">
          <div className="context-doc scroll">
            {(doc?.content ?? "").split("\n").map((line, i) => renderMdLine(line, i))}
          </div>
        </div>
        <div className="modal-ft">
          <span className="hint mono" style={{ marginRight: "auto" }}>{doc?.path ?? folder.absolute_path}</span>
          <button className="btn" onClick={onClose}>Close</button>
          <a className="btn primary" href={`/chats/${folder.chat_slug}`}><ChatIcon size={12} /> Open {folder.chat_slug}</a>
        </div>
      </div>
    </div>
  );
}

function PromoteChatModal({
  chat,
  projects,
  works,
  onClose,
  onPromoted,
}: {
  chat: ChatDetail;
  projects: ProjectSummary[];
  works: WorkSummary[];
  onClose: () => void;
  onPromoted: (workSlug: string) => void;
}) {
  const inherited = inheritedProjectSlug(chat, works);
  const [name, setName] = useState(chat.title);
  const [description, setDescription] = useState(summarizeChat(chat));
  const [projectSlug, setProjectSlug] = useState<string | null>(inherited);
  const [error, setError] = useState<string | null>(null);

  async function submit() {
    if (!name.trim()) return;
    try {
      const work = await promoteChat(chat.slug, {
        name: name.trim(),
        description: description.trim(),
        project_slug: projectSlug,
      });
      onPromoted(work.slug);
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err));
    }
  }

  return (
    <div className="scrim" onClick={(e) => e.target === e.currentTarget && onClose()}>
      <div className="modal promote-summary-modal">
        <div className="modal-hd">
          <div>
            <h3><SparkIcon size={13} /> Start work from this chat</h3>
            <div className="sub">Promote this conversation into a tracked work unit.</div>
          </div>
          <button className="btn-icon" onClick={onClose}>×</button>
        </div>
        <div className="modal-bd">
          <div className="promote-prov">
            <span className="pp-ico"><ChatIcon size={12} /></span>
            <span className="pp-body">
              <span className="pp-lbl">From chat · {chat.slug}</span>
              <span className="pp-title">{chat.title}</span>
            </span>
            <span className="pp-link mono">linked</span>
          </div>
          <label className="field">
            <span className="label">Name</span>
            <input className="input" value={name} onChange={(e) => setName(e.target.value)} autoFocus />
          </label>
          <label className="field">
            <span className="label">Brief <span className="hint">· edit freely</span></span>
            <textarea className="textarea" rows={6} value={description} onChange={(e) => setDescription(e.target.value)} />
          </label>
          <div className="field">
            <span className="label">Project</span>
            <div className="proj-pick-row">
              <button className={"proj-pick-chip" + (projectSlug == null ? " active" : "")} onClick={() => setProjectSlug(null)}>Loose</button>
              {projects.map((p) => (
                <button
                  key={p.slug}
                  className={"proj-pick-chip" + (projectSlug === p.slug ? " active" : "")}
                  style={{ ["--proj-h" as string]: String(p.color) }}
                  onClick={() => setProjectSlug(p.slug)}
                >
                  <span className="proj-pick-glyph mono">{p.glyph}</span>
                  {p.name}
                  {p.slug === inherited && <span className="inherit-tag">inherited</span>}
                </button>
              ))}
            </div>
          </div>
          {error && <div className="form-error">{error}</div>}
        </div>
        <div className="modal-ft">
          <span className="hint" style={{ marginRight: "auto" }}>The chat stays linked to this work.</span>
          <button className="btn" onClick={onClose}>Cancel</button>
          <button className="btn primary" disabled={!name.trim()} onClick={() => void submit()}>
            Create work
          </button>
        </div>
      </div>
    </div>
  );
}

function GroundingPicker({
  projects,
  works,
  value,
  label,
  allowNoLink,
  onChange,
}: {
  projects: ProjectSummary[];
  works: WorkSummary[];
  value: ChatGrounding | null;
  label: string;
  allowNoLink: boolean;
  onChange: (value: ChatGrounding | null) => void;
}) {
  return (
    <div className="cb-ground-pop">
      <button className="cb-ground-btn" data-on={!!value}>
        {value ? <span className="swatch" /> : <span className="mono">~</span>}
        <span className="cb-ground-label">{label}</span>
      </button>
      <div className="cb-ground-menu">
        <div className="ground-picker">
          <div className="ground-picker-label">Link to</div>
          <div className="ground-chips">
            {allowNoLink && (
              <button className={"ground-chip" + (!value ? " active" : "")} onClick={() => onChange(null)}><span className="g-glyph">~</span> None</button>
            )}
            {projects.map((p) => (
              <button
                key={p.slug}
                className={"ground-chip" + (value?.kind === "project" && value.ref === p.slug ? " active" : "")}
                style={{ ["--proj-color" as string]: `oklch(0.62 0.16 ${p.color})` }}
                onClick={() => onChange({ kind: "project", ref: p.slug })}
              >
                <span className="g-glyph mono">{p.glyph}</span> {p.name}
              </button>
            ))}
            {works.slice(0, 8).map((w) => (
              <button
                key={w.slug}
                className={"ground-chip work" + (value?.kind === "work" && value.ref === w.slug ? " active" : "")}
                style={workGroundStyle(w, projects)}
                onClick={() => onChange({ kind: "work", ref: w.slug })}
                title={`${w.name} (${w.slug})`}
              >
                <span className="g-glyph">▶</span>
                <span className="ground-chip-label">{w.name}</span>
              </button>
            ))}
          </div>
        </div>
      </div>
    </div>
  );
}

function WorkingFolderPicker({
  value,
  onChange,
}: {
  value: string | null;
  onChange: (value: string | null) => void;
}) {
  const [pickerOpen, setPickerOpen] = useState(false);
  const label = value ? displayFolderName(value) : "Working folder";
  return (
    <div className="cb-ground-pop cb-workdir-pop">
      <button className="cb-ground-btn" data-on={!!value} onClick={() => setPickerOpen(true)}>
        <FolderIcon size={11} />
        <span className="cb-ground-label">{label}</span>
      </button>
      <div className="cb-ground-menu">
        <div className="ground-picker">
          <div className="ground-picker-label">Working folder</div>
          <div className="ground-folder-row">
            <input
              className="input sm mono"
              value={value ?? ""}
              onChange={(e) => onChange(e.target.value || null)}
              placeholder="Default"
            />
            <button
              type="button"
              className="btn sm"
              onClick={() => setPickerOpen(true)}
            >
              <FolderIcon size={11} /> Browse
            </button>
          </div>
          {value && (
            <button
              type="button"
              className="btn sm ghost workdir-clear"
              onClick={() => onChange(null)}
            >
              Clear
            </button>
          )}
        </div>
      </div>
      {pickerOpen && (
        <FolderPickerDialog
          mode="folder"
          initialPath={value || null}
          onCancel={() => setPickerOpen(false)}
          onPick={(path) => {
            onChange(path);
            setPickerOpen(false);
          }}
        />
      )}
    </div>
  );
}

function displayFolderName(path: string): string {
  return path.split("/").filter(Boolean).pop() || path;
}

type GroundingInfo = {
  kind: "none" | "project" | "work" | "folder";
  label: string;
  sub: string;
  href?: string;
};

type WorkingFolderInfo = {
  label: string;
  sub: string;
};

function resolveGrounding(
  grounding: ChatGrounding | null,
  projects: ProjectSummary[],
  works: WorkSummary[],
): GroundingInfo {
  if (!grounding) return { kind: "none", label: "Unlinked", sub: "open exploration" };
  if (grounding.kind === "project") {
    const project = projects.find((p) => p.slug === grounding.ref);
    return {
      kind: "project",
      label: project?.name ?? grounding.ref,
      sub: project?.description ?? "project",
      href: `/projects/${grounding.ref}`,
    };
  }
  if (grounding.kind === "work") {
    const work = works.find((w) => w.slug === grounding.ref);
    return {
      kind: "work",
      label: work?.name ?? grounding.ref,
      sub: grounding.ref,
      href: `/works/${grounding.ref}`,
    };
  }
  return { kind: "folder", label: grounding.ref.split("/").filter(Boolean).pop() ?? grounding.ref, sub: grounding.ref };
}

function resolveWorkingFolder(chat: ChatSummary): WorkingFolderInfo {
  const path =
    chat.working_directory ??
    (chat.grounding?.kind === "folder" ? chat.grounding.ref : null);
  if (!path) return { label: "Default", sub: "uses the linked scope folder" };
  return { label: displayFolderName(path), sub: path };
}

function workGroundStyle(
  work: WorkSummary,
  projects: ProjectSummary[],
): Record<string, string> | undefined {
  const project = projects.find((p) => p.slug === work.project_slug);
  if (!project) return undefined;
  return { "--proj-color": `oklch(0.62 0.16 ${project.color})` };
}

function GroundingCard({ grounding }: { grounding: GroundingInfo }) {
  const body = (
    <>
      <span className="g-ico">{grounding.kind === "folder" ? <FolderIcon size={13} /> : grounding.kind === "none" ? "~" : <span className="swatch" />}</span>
      <span className="g-body">
        <span className="g-label">{grounding.label}</span>
        <span className="g-sub">{grounding.sub}</span>
      </span>
    </>
  );
  return grounding.href ? (
    <a className="chat-ground" data-kind={grounding.kind} href={grounding.href}>
      {body}
    </a>
  ) : (
    <div className="chat-ground" data-kind={grounding.kind}>
      {body}
    </div>
  );
}

function WorkingFolderCard({ folder }: { folder: WorkingFolderInfo }) {
  return (
    <div className="chat-ground" data-kind="folder">
      <span className="g-ico"><FolderIcon size={13} /></span>
      <span className="g-body">
        <span className="g-label">{folder.label}</span>
        <span className="g-sub">{folder.sub}</span>
      </span>
    </div>
  );
}

function LiveEffortSelect({
  events,
  disabled,
  onChange,
}: {
  events: AgentEvent[];
  disabled: boolean;
  onChange: (configId: string, value: string) => void;
}) {
  const config = useMemo(
    () => latestSessionConfigOptionByIds(events, EFFORT_SESSION_CONFIG_IDS),
    [events],
  );
  const value = typeof config?.currentValue === "string" ? config.currentValue : null;
  if (!config || !value || config.choices.length === 0) return null;
  const label = labelForSessionConfigValue(config, value);
  return (
    <label className="composer-effort-picker" title={`${config.name}: ${label}`}>
      <span className="composer-effort-prefix">Effort:</span>
      <select
        className="composer-effort-select"
        value={value}
        disabled={disabled}
        onChange={(e) => onChange(config.id, e.target.value)}
      >
        {config.choices.map((choice) => (
          <option key={String(choice.value)} value={String(choice.value)}>
            {choice.name ?? String(choice.value)}
          </option>
        ))}
      </select>
    </label>
  );
}

function ChatContextGauge({ context }: { context: ContextSnapshot | null }) {
  if (!context) return null;
  const pct = Math.max(0, Math.min(context.pct, 100));
  const tone = contextToneFor(context.pct);
  const style = { "--ctx-pct": `${pct}%` } as CSSProperties;
  const title = `Context: ${context.promptTokens.toLocaleString()} / ${context.contextWindow.toLocaleString()} (${context.pct.toFixed(1)}%)`;
  return (
    <div
      className="chat-context-gauge"
      data-ctx-tone={tone}
      style={style}
      title={title}
    >
      <span className="chat-context-gauge-track" aria-hidden>
        <span />
      </span>
      <span className="chat-context-gauge-label">
        ctx {context.pct.toFixed(0)}%
      </span>
    </div>
  );
}

type ChatRuntimeItem =
  | {
      kind: "message";
      key: number;
      role: "user" | "assistant";
      body: string;
      complete: boolean;
    }
  | { kind: "status"; key: number; label: string }
  | { kind: "tool"; key: number; name: string; body: string }
  | {
      kind: "compaction";
      key: number;
      summaryPath: string;
      oldSessionId: string;
      newSessionId: string;
    }
  | {
      kind: "provider_compaction";
      key: number;
      provider: string;
      reason: string;
    }
  | { kind: "error"; key: number; message: string };

function chatItemsFromEvents(events: AgentEvent[]): ChatRuntimeItem[] {
  const out: ChatRuntimeItem[] = [];
  let pendingAssistant: Extract<ChatRuntimeItem, { kind: "message" }> | null = null;

  for (const ev of events) {
    const seq = eventSeq(ev);
    if (ev.type === "user_input") {
      pendingAssistant = null;
      out.push({
        kind: "message",
        key: seq,
        role: "user",
        body: stringField(ev, "text"),
        complete: true,
      });
    } else if (ev.type === "message_delta") {
      const text = stringField(ev, "text");
      if (pendingAssistant) {
        pendingAssistant.body += text;
      } else {
        pendingAssistant = {
          kind: "message",
          key: seq,
          role: "assistant",
          body: text,
          complete: false,
        };
        out.push(pendingAssistant);
      }
    } else if (ev.type === "message_complete") {
      const text = stringField(ev, "text");
      if (pendingAssistant) {
        pendingAssistant.body = text;
        pendingAssistant.complete = true;
        pendingAssistant = null;
      } else {
        out.push({
          kind: "message",
          key: seq,
          role: "assistant",
          body: text,
          complete: true,
        });
      }
    } else if (ev.type === "tool_call") {
      pendingAssistant = null;
      out.push({
        kind: "tool",
        key: seq,
        name: stringField(ev, "name") || "tool",
        body: JSON.stringify(ev.arguments ?? {}, null, 2),
      });
    } else if (ev.type === "tool_result") {
      pendingAssistant = null;
      out.push({
        kind: "tool",
        key: seq,
        name: "tool result",
        body: stringField(ev, "content"),
      });
    } else if (ev.type === "user_stop") {
      pendingAssistant = null;
      out.push({ kind: "status", key: seq, label: "Stopped" });
    } else if (ev.type === "context_compacted") {
      pendingAssistant = null;
      out.push({
        kind: "compaction",
        key: seq,
        summaryPath: stringField(ev, "summary_path"),
        oldSessionId: stringField(ev, "old_session_id"),
        newSessionId: stringField(ev, "new_session_id"),
      });
    } else if (ev.type === "provider_context_compacted") {
      pendingAssistant = null;
      out.push({
        kind: "provider_compaction",
        key: seq,
        provider: stringField(ev, "provider") || "provider",
        reason: stringField(ev, "reason") || "auto",
      });
    } else if (ev.type === "compaction_failed") {
      pendingAssistant = null;
      out.push({
        kind: "error",
        key: seq,
        message: `Compaction failed: ${stringField(ev, "error") || "unknown error"}`,
      });
    } else if (ev.type === "error" || ev.type === "client_error") {
      pendingAssistant = null;
      out.push({
        kind: "error",
        key: seq,
        message: stringField(ev, "message") || "Runtime error",
      });
    }
  }

  return out;
}

function chatMessagesFromEvents(events: AgentEvent[]): ChatMessage[] {
  const now = new Date().toISOString();
  return chatItemsFromEvents(events)
    .filter((item): item is Extract<ChatRuntimeItem, { kind: "message" }> => {
      return item.kind === "message" && item.complete;
    })
    .map((item) => ({
      role: item.role,
      body: item.body,
      created_at: eventTimestamp(events, item.key) ?? now,
    }));
}

function eventSeq(event: AgentEvent): number {
  return typeof event.seq === "number" ? event.seq : -Date.now();
}

function latestSeq(events: AgentEvent[]): number {
  return events.reduce((max, event) => Math.max(max, eventSeq(event)), 0);
}

function eventTimestamp(events: AgentEvent[], seq: number): string | null {
  const event = events.find((ev) => eventSeq(ev) === seq);
  return typeof event?.ts === "string" ? event.ts : null;
}

function stringField(event: AgentEvent, field: string): string {
  const value = event[field];
  return typeof value === "string" ? value : "";
}

function renderMdLine(line: string, key: number) {
  if (line.startsWith("# ")) return <h2 className="cd-h1" key={key}>{line.slice(2)}</h2>;
  if (line.startsWith("## ")) return <h3 className="cd-h2" key={key}>{line.slice(3)}</h3>;
  if (line.startsWith("> ")) return <blockquote className="cd-quote" key={key}>{line.slice(2)}</blockquote>;
  if (line.startsWith("- ")) return <li className="cd-li" key={key}>{line.slice(2)}</li>;
  if (!line.trim()) return <div className="cd-gap" key={key} />;
  return <p className="cd-p" key={key}>{line}</p>;
}

function summarizeChat(chat: ChatDetail): string {
  const first = chat.transcript.find((m) => m.role === "user")?.body ?? "";
  const last = [...chat.transcript].reverse().find((m) => m.role === "assistant")?.body ?? "";
  let summary = `Carried over from chat ${chat.slug} - "${chat.title}".\n\n`;
  summary += trimPlain(first, 200);
  if (last) summary += `\n\nWhere we landed: ${trimPlain(last, 240)}`;
  return summary;
}

function trimPlain(text: string, limit: number): string {
  const plain = text.replace(/\*\*/g, "").replace(/`/g, "").replace(/\s+/g, " ").trim();
  return plain.length > limit ? plain.slice(0, limit).replace(/\s+\S*$/, "") + "..." : plain;
}

function inheritedProjectSlug(chat: ChatDetail, works: WorkSummary[]): string | null {
  if (chat.grounding?.kind === "project") return chat.grounding.ref;
  if (chat.grounding?.kind === "work") {
    return works.find((w) => w.slug === chat.grounding?.ref)?.project_slug ?? null;
  }
  return null;
}

function providerLabelFor(provider: string): string {
  if (provider === "claude-code") return "Claude Code";
  if (provider === "amp") return "Amp";
  if (provider === "codex") return "Codex";
  return provider;
}

function shortProvider(provider: string): string {
  if (provider === "claude-code") return "claude";
  if (provider === "claude-acp") return "claude";
  if (provider === "codex-acp") return "codex";
  return provider;
}

function shortModel(model: string): string {
  return model.replace(/^claude-/, "").replace(/^gpt-/, "");
}

export function chatSummaryFromDetail(chat: ChatDetail): ChatSummary {
  const { transcript: _transcript, ...summary } = chat;
  return summary;
}

function formatAge(iso: string): string {
  const ms = Date.now() - new Date(iso).getTime();
  if (ms < 0) return "now";
  const s = Math.floor(ms / 1000);
  if (s < 60) return `${s}s ago`;
  const m = Math.floor(s / 60);
  if (m < 60) return `${m}m ago`;
  const h = Math.floor(m / 60);
  if (h < 24) return `${h}h ago`;
  const d = Math.floor(h / 24);
  if (d < 7) return `${d}d ago`;
  const w = Math.floor(d / 7);
  return `${w}w ago`;
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
      <rect
        x="3"
        y="5"
        width="8"
        height="8"
        rx="1"
        stroke="currentColor"
        strokeWidth="1.4"
        fill="none"
      />
      <path
        d="M5 5V3h8v8h-2"
        stroke="currentColor"
        strokeWidth="1.4"
        fill="none"
        strokeLinecap="round"
      />
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
