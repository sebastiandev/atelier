import { useCallback, useEffect, useMemo, useRef, useState } from "react";

import {
  type AgentSummary,
  type PrArtifactView,
  PERSONA_GLYPH,
  getPrArtifactView,
  sendPrArtifactFeedback,
} from "./api";
import { CheckIcon, ChevronRightIcon, LoopIcon } from "./Icons";
import { prStatusTone } from "./LoopRunView";
import { MarkdownText } from "./MarkdownText";
import { prCommentThreads, relativeTime } from "./PrLifecyclePanel";

/**
 * A tracked PR opened in the story canvas (Screen Specs §12 "PR view").
 *
 * Replaces the agent grid; the rail and the story doc stay put. Threads are
 * GitHub-style cards, selectable, each selected one revealing a per-comment
 * instruction. The footer routes the selection to the agent that opened the
 * PR — never a choice: it is the opener, or a relaunch of the opener when it
 * was removed — as Implement (a task on its branch) or Discuss (a question).
 */
export function StoryPrView({
  workSlug,
  artifactSlug,
  agents,
  readOnly,
  onBack,
  onAgentsChanged,
  onFocusAgent,
}: {
  workSlug: string;
  artifactSlug: string;
  agents: AgentSummary[];
  readOnly: boolean;
  onBack: () => void;
  /** A relaunched opener is a new agent row; the parent refreshes its list. */
  onAgentsChanged: () => Promise<void>;
  onFocusAgent: (slug: string) => void;
}) {
  const [view, setView] = useState<PrArtifactView | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [syncing, setSyncing] = useState(false);
  const [sending, setSending] = useState(false);
  const [selected, setSelected] = useState<Set<string>>(() => new Set());
  const [instructions, setInstructions] = useState<Record<string, string>>({});
  const [note, setNote] = useState("");
  const [mode, setMode] = useState<"implement" | "discuss">("implement");
  const [sentTo, setSentTo] = useState<{ slug: string; relaunched: boolean } | null>(null);
  // Threads the user folded or unfolded by hand; anything else follows the
  // default: long or resolved threads start folded, the rest open.
  const [folded, setFolded] = useState<Record<string, boolean>>({});
  const mounted = useRef(true);
  // StrictMode runs mount/cleanup/mount: set the flag on every mount, not
  // just at ref creation, or the second mount ignores all state updates.
  useEffect(() => {
    mounted.current = true;
    return () => { mounted.current = false; };
  }, []);

  const load = useCallback(async (force = false) => {
    setSyncing(true);
    try {
      const next = await getPrArtifactView(workSlug, artifactSlug, { force });
      if (mounted.current) { setView(next); setError(null); }
    } catch (err) {
      if (mounted.current) setError(err instanceof Error ? err.message : String(err));
    } finally {
      if (mounted.current) setSyncing(false);
    }
  }, [workSlug, artifactSlug]);

  useEffect(() => {
    setView(null);
    setSelected(new Set());
    setInstructions({});
    setSentTo(null);
    setFolded({});
    void load();
    const timer = window.setInterval(() => void load(), 60_000);
    return () => window.clearInterval(timer);
  }, [load]);

  const threads = useMemo(() => prCommentThreads(view?.comments ?? []), [view?.comments]);
  const feedbackByThread = useMemo(
    () => threadFeedbackStates(threads, view?.feedback ?? []),
    [threads, view?.feedback],
  );
  const lastFeedbackAt = view?.feedback.at(-1)?.sent_at ?? null;
  const newThreads = threads.filter(
    (t) => t.state === "open" && t.actionable && (!lastFeedbackAt || t.root.created_at > lastFeedbackAt),
  );
  // Selectable = open thread, nobody's own reply, and not currently being
  // worked (a pending implement batch) or already addressed by a push.
  const selectable = threads.filter((t) => {
    const fb = feedbackByThread.get(t.id);
    return t.actionable && fb?.state !== "pending" && fb?.state !== "addressed";
  });
  const selectedCount = selectable.filter((t) => selected.has(t.action.id)).length;
  const withInstruction = selectable.filter(
    (t) => selected.has(t.action.id) && (instructions[t.action.id] ?? "").trim(),
  ).length;
  const isFolded = (id: string, body: string, resolved: boolean) =>
    folded[id] ?? (resolved || body.length > LONG_COMMENT);
  const anyOpen = threads.some((t) => !isFolded(t.id, t.root.body, t.state !== "open" || !t.actionable));
  const opener = view?.opened_by ?? null;
  const openerAgent = opener?.slug ? agents.find((a) => a.slug === opener.slug) ?? null : null;
  const pr = view?.pr ?? null;
  const terminal = view ? view.status === "merged" || view.status === "closed" : false;
  const canSend = !readOnly && !terminal && !sending && selectedCount > 0 && Boolean(opener);

  async function send() {
    if (!view || !canSend) return;
    setSending(true);
    setError(null);
    try {
      const result = await sendPrArtifactFeedback(workSlug, view.slug, {
        mode,
        note: note.trim(),
        comments: selectable
          .filter((t) => selected.has(t.action.id))
          .map((t) => ({ comment_id: t.action.id, instruction: (instructions[t.action.id] ?? "").trim() })),
      });
      if (!mounted.current) return;
      setView(result.view);
      setSelected(new Set());
      setInstructions({});
      setNote("");
      setSentTo({ slug: result.target_slug, relaunched: result.relaunched });
      if (result.relaunched) await onAgentsChanged();
      // The work now happens in the agent's tile: go there.
      onFocusAgent(result.target_slug);
    } catch (err) {
      if (mounted.current) setError(err instanceof Error ? err.message : String(err));
    } finally {
      if (mounted.current) setSending(false);
    }
  }

  return (
    <section className="story-pr-view">
      <header className="story-pr-head">
        <div className="story-pr-inner">
        <div className="pm-pane-kicker">
          <span>tracked pr</span>
          {opener && (
            <button
              type="button"
              className="story-pr-agent-strip"
              data-persona={opener.persona}
              onClick={onBack}
              title="Back to the agents"
            >
              <span className="persona-pip">{PERSONA_GLYPH[opener.persona]}</span>
              <span className="story-pr-agent-name">{opener.name} · {opener.slug ?? "removed"}</span>
              {openerAgent && <span className="status-dot" data-status={openerAgent.status} />}
              <span className="story-pr-agent-status">
                {openerAgent ? agentStatusLine(openerAgent.status) : opener.present ? "" : "removed · will relaunch"}
              </span>
            </button>
          )}
        </div>
        <div className="pm-kicker story-pr-crumbs">
          <button type="button" onClick={onBack}>‹ agents</button>
          <span>/</span>
          <span>{pr?.number ? `#${pr.number}` : view?.slug ?? artifactSlug}</span>
          {view && <em className={`tag ${prStatusTone(view.status)}`}>{view.status}</em>}
          {newThreads.length > 0 && (
            <em className="tag warn">{newThreads.length} new comment{newThreads.length === 1 ? "" : "s"}</em>
          )}
          <span className="story-pr-spacer" />
          <button type="button" className="btn sm" disabled={syncing} onClick={() => void load(true)}>
            <LoopIcon size={10} /> {syncing ? "Checking…" : "Check for comments"}
          </button>
          {view && (
            <a className="btn ghost sm" href={view.url} target="_blank" rel="noreferrer">open on GitHub ↗</a>
          )}
        </div>
        <h2 className="story-pr-title">{pr?.title || view?.title || "Pull request"}</h2>
        <div className="pm-meta-row story-pr-meta">
          {view && (
            <a className="mono dim story-pr-url" href={view.url} target="_blank" rel="noreferrer" title="Open on GitHub">
              {view.url.replace(/^https?:\/\//, "")}
            </a>
          )}
          {pr?.branch && <span className="mono dim">{pr.branch} → {pr.base}</span>}
          {pr && (pr.additions || pr.deletions || pr.changed_files) ? (
            <span className="mono dim">
              +{pr.additions ?? 0} −{pr.deletions ?? 0} · {pr.changed_files ?? 0} files
            </span>
          ) : null}
          {opener && (
            <button
              type="button"
              className={"story-pr-opener" + (opener.present && opener.slug ? "" : " gone")}
              data-persona={opener.persona}
              disabled={!opener.present || !opener.slug}
              onClick={() => opener.slug && onFocusAgent(opener.slug)}
              title={
                openerAgent
                  ? `${agentStatusLine(openerAgent.status)} — back to the agents`
                  : "This agent was removed — sending will relaunch it"
              }
            >
              <span className="persona-pip">{PERSONA_GLYPH[opener.persona]}</span>
              <span className="mono">
                opened by {opener.name}{opener.slug ? ` · ${opener.slug}` : ""}
                {!opener.present && " · removed"}
              </span>
              {openerAgent && <span className="status-dot" data-status={openerAgent.status} />}
            </button>
          )}
        </div>
        </div>
      </header>

      <div className="story-pr-body themed-scrollbar">
        <div className="story-pr-inner">
        {error && <div className="pm-error">{error}</div>}
        {!view && !error && <div className="pm-loading">Loading pull request…</div>}
        {view && (
          <>
            <div className="story-pr-card">
              <span className="story-pr-card-kicker">Description</span>
              {pr?.body ? (
                <div className="story-pr-description md-body">
                  <MarkdownText text={stripHtmlComments(pr.body)} />
                </div>
              ) : (
                <p className="dim">No description on the pull request.</p>
              )}
            </div>
            <div className="story-pr-threads-hd">
              <span className="pm-section-hd">
                <span>Comments</span>
                <span>{view.comments.length} · {threads.length} thread{threads.length === 1 ? "" : "s"}</span>
              </span>
              {newThreads.length > 0 && !terminal && (
                <button
                  type="button"
                  className="story-pr-link"
                  onClick={() => setSelected(new Set(newThreads.map((t) => t.action.id)))}
                >
                  select all new
                </button>
              )}
              {threads.length > 0 && (
                <button
                  type="button"
                  className="story-pr-link dim"
                  onClick={() => setFolded(Object.fromEntries(threads.map((t) => [t.id, anyOpen])))}
                >
                  {anyOpen ? "fold all" : "unfold all"}
                </button>
              )}
              <span className="mono dim">
                {pr?.last_synced_at ? `synced ${relativeTime(pr.last_synced_at)}` : "not synced yet"}
              </span>
            </div>
            <div className="story-pr-threads">
              {threads.map((thread) => {
                const checked = selected.has(thread.action.id);
                const fb = feedbackByThread.get(thread.id);
                const canSelect = selectable.includes(thread);
                const resolved = thread.state !== "open" || !thread.actionable || fb?.state === "addressed";
                const collapsed = isFolded(thread.id, thread.root.body, resolved);
                const tag = thread.state === "dismissed"
                  ? "dismissed"
                  : fb?.state === "pending"
                    ? "sent · implementing"
                    : fb?.state === "addressed"
                      ? "addressed"
                      : fb?.state === "reopened"
                        ? "reopened"
                        : fb?.state === "discussed"
                          ? "discussed"
                          : !thread.actionable && thread.state === "open"
                            ? "replied"
                            : thread.root.author.endsWith("[bot]") || thread.root.author.endsWith("-bot")
                              ? "bot"
                              : null;
                const tagTone = tag === "addressed" || tag === "replied"
                  ? " good"
                  : tag === "sent · implementing" || tag === "discussed"
                    ? " accent"
                    : tag === "reopened"
                      ? " warn"
                      : "";
                return (
                  <div
                    key={thread.id}
                    className={"story-pr-thread" + (checked ? " selected" : "") + (resolved ? " resolved" : "")}
                  >
                    <div className="story-pr-thread-hd">
                      {canSelect && !terminal ? (
                        <input
                          type="checkbox"
                          checked={checked}
                          disabled={readOnly}
                          onChange={(e) => setSelected((cur) => {
                            const next = new Set(cur);
                            if (e.target.checked) next.add(thread.action.id); else next.delete(thread.action.id);
                            return next;
                          })}
                        />
                      ) : (
                        <span className="story-pr-thread-state"><CheckIcon size={10} /></span>
                      )}
                      <span className="story-pr-avatar">{initials(thread.root.author)}</span>
                      <span className="mono">@{thread.root.author}</span>
                      <span className="mono dim">{thread.root.location || "general"}</span>
                      <span className="mono dim">{relativeTime(thread.root.created_at)}</span>
                      {tag && <em className={"tag" + tagTone} title={fb?.detail}>{tag}</em>}
                      <button
                        type="button"
                        className={"story-pr-fold" + (collapsed ? " folded" : "")}
                        onClick={() => setFolded((cur) => ({ ...cur, [thread.id]: !collapsed }))}
                        aria-expanded={!collapsed}
                        aria-label={collapsed ? "Unfold comment" : "Fold comment"}
                        title={collapsed ? "Unfold" : "Fold"}
                      >
                        <ChevronRightIcon size={12} />
                        {collapsed && thread.replies.length > 0 && <span className="mono">{thread.replies.length}</span>}
                      </button>
                    </div>
                    {collapsed ? (
                      <button
                        type="button"
                        className="story-pr-thread-preview"
                        onClick={() => setFolded((cur) => ({ ...cur, [thread.id]: false }))}
                      >
                        {previewLine(thread.root.body)}
                      </button>
                    ) : (
                    <div className="story-pr-thread-body md-body"><MarkdownText text={stripHtmlComments(thread.root.body)} /></div>
                    )}
                    {!collapsed && thread.replies.length > 0 && (
                      <div className="story-pr-replies">
                        {thread.replies.map((reply) => (
                          <div key={reply.id} className={"story-pr-reply" + (reply.atelier_reply ? " agent" : "")}>
                            <span className="story-pr-avatar">{reply.atelier_reply ? "AG" : initials(reply.author)}</span>
                            <div>
                              <div className="story-pr-reply-meta">
                                <span className="mono">{reply.atelier_reply ? (opener?.name ?? "agent") : `@${reply.author}`}</span>
                                {reply.atelier_reply && <em className="tag accent">agent reply</em>}
                                <span className="mono dim">{relativeTime(reply.created_at)}</span>
                              </div>
                              <div className="md-body story-pr-reply-body"><MarkdownText text={stripHtmlComments(reply.body)} /></div>
                            </div>
                          </div>
                        ))}
                      </div>
                    )}
                    {checked && (
                      <input
                        className="story-pr-instruction"
                        value={instructions[thread.action.id] ?? ""}
                        placeholder="optional instruction for the agent — how to tackle this one…"
                        onChange={(e) => setInstructions((cur) => ({ ...cur, [thread.action.id]: e.target.value }))}
                      />
                    )}
                  </div>
                );
              })}
              {threads.length === 0 && <p className="dim">No comments on this pull request yet.</p>}
            </div>
            {view && !terminal && (selectedCount > 0 || sending) && (
        <footer className="story-pr-footer">
          <div className="story-pr-inner">
          <div className="story-pr-footer-row">
            <span className="mono accent">{selectedCount} comment{selectedCount === 1 ? "" : "s"} selected</span>
            {withInstruction > 0 && <span className="mono dim">{withInstruction} with an instruction</span>}
            {selectedCount > 0 && (
              <button type="button" className="story-pr-link dim" onClick={() => setSelected(new Set())}>clear</button>
            )}
            {sentTo && (
              <span className="mono dim story-pr-sent">
                <CheckIcon size={10} /> sent to {sentTo.slug}{sentTo.relaunched ? " (relaunched)" : ""}
              </span>
            )}
          </div>
          <div className="story-pr-footer-row">
            <span className="mono dim">send to</span>
            {opener ? (
              <span className="story-pr-opener static" data-persona={opener.persona}>
                <span className="persona-pip">{PERSONA_GLYPH[opener.persona]}</span>
                {opener.name}{opener.slug ? ` · ${opener.slug}` : ""}
                <em className="tag accent">{opener.present && opener.slug ? "opened this PR" : "will relaunch"}</em>
              </span>
            ) : (
              <span className="mono dim">no agent recorded for this PR</span>
            )}
            <span className="story-pr-mode">
              <button type="button" className={"btn sm" + (mode === "implement" ? " on" : " ghost")} onClick={() => setMode("implement")}>Implement</button>
              <button type="button" className={"btn sm" + (mode === "discuss" ? " on" : " ghost")} onClick={() => setMode("discuss")}>Discuss</button>
            </span>
            <input
              className="story-pr-note"
              value={note}
              placeholder="note for the whole batch — optional…"
              onChange={(e) => setNote(e.target.value)}
            />
            <button type="button" className="btn sm primary" disabled={!canSend} onClick={() => void send()}>
              {sending ? "Sending…" : "Send to agent"}
            </button>
          </div>
          <span className="mono dim story-pr-hint">
            Implement lands as a task in that agent's tile on its own branch · Discuss only asks · a reply is posted to each selected comment when the work pushes
          </span>
          </div>
        </footer>
            )}
          </>
        )}
        </div>
      </div>


    </section>
  );
}

/** GitHub bots leave HTML comments (`<!-- meta -->`) in bodies; they are
 *  not content. */
type ThreadFeedback = {
  state: "pending" | "addressed" | "reopened" | "discussed";
  detail: string;
};

/**
 * What the last feedback batch means for each thread.
 *
 * - `pending`   — sent as implement, the push has not landed: not selectable.
 * - `addressed` — Atelier replied on the thread after the push and nobody
 *                 has answered since: resolved, not selectable.
 * - `reopened`  — a reviewer wrote after the addressed reply: selectable again.
 * - `discussed` — sent as discuss (answered in chat): selectable again.
 */
function threadFeedbackStates(
  threads: ReturnType<typeof prCommentThreads>,
  feedback: PrArtifactView["feedback"],
): Map<string, ThreadFeedback> {
  const out = new Map<string, ThreadFeedback>();
  for (const thread of threads) {
    const ids = new Set([thread.root, ...thread.replies].map((c) => c.id));
    const batch = [...feedback].reverse().find((b) => b.comments.some((c) => ids.has(c.comment_id)));
    if (!batch) continue;
    const lastReviewerAt = [thread.root, ...thread.replies]
      .filter((c) => !c.atelier_reply && !c.is_viewer)
      .map((c) => c.created_at)
      .sort()
      .at(-1) ?? "";
    if (batch.mode === "discuss") {
      out.set(thread.id, { state: "discussed", detail: `discussed with ${batch.sent_to} · ${relativeTime(batch.sent_at)}` });
    } else if (!batch.replied_at) {
      out.set(thread.id, { state: "pending", detail: `sent to ${batch.sent_to} · ${relativeTime(batch.sent_at)} · waiting for the push` });
    } else if (lastReviewerAt > batch.replied_at) {
      out.set(thread.id, { state: "reopened", detail: "a reviewer answered after the fix landed" });
    } else {
      out.set(thread.id, { state: "addressed", detail: `addressed by ${batch.sent_to} · ${relativeTime(batch.replied_at)}` });
    }
  }
  return out;
}

/** Bodies past this start folded — CI reports and bot checklists run long. */
const LONG_COMMENT = 700;

function previewLine(body: string): string {
  const flat = stripHtmlComments(body)
    .replace(/```[\s\S]*?```/g, " [code] ")
    .replace(/[#*_>`|-]+/g, " ")
    .replace(/\s+/g, " ")
    .trim();
  return flat.length > 140 ? `${flat.slice(0, 140)}…` : flat || "(empty)";
}

function agentStatusLine(status: AgentSummary["status"]): string {
  if (status === "thinking") return "working";
  if (status === "live") return "live";
  if (status === "idle") return "idle";
  if (status === "error") return "error";
  if (status === "detached") return "detached to CLI";
  return "stopped";
}

function stripHtmlComments(text: string): string {
  return text.replace(/<!--[\s\S]*?-->/g, "").trim();
}

function initials(author: string): string {
  const clean = author.replace(/\[bot\]$/, "").replace(/[^a-z0-9]/gi, "");
  return (clean.slice(0, 2) || "??").toUpperCase();
}
