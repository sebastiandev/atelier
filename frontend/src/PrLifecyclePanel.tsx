import { useEffect, useMemo, useRef, useState } from "react";

import type { PrComment, PrLifecycle } from "./api";
import { BranchIcon, CheckIcon, ChevronRightIcon, CopyIcon, LoopIcon, ReturnIcon } from "./Icons";

type FeedbackItem = { comment_id: string; instruction: string };
type PrCommentThread = {
  action: PrComment;
  actionable: boolean;
  id: string;
  replies: PrComment[];
  root: PrComment;
};

export function PrLifecyclePanel({
  addressedComments = [],
  busy,
  comments,
  feedbackInstruction = "",
  onRefresh,
  onSendFeedback,
  pr,
  pushAt,
}: {
  addressedComments?: Array<Record<string, unknown>>;
  busy: boolean;
  comments: PrComment[];
  feedbackInstruction?: string;
  onRefresh?: (force?: boolean) => Promise<void>;
  onSendFeedback?: (comments: FeedbackItem[], instruction: string) => Promise<void>;
  pr: PrLifecycle;
  pushAt: string | null;
}) {
  const visible = useMemo(
    () => prCommentThreads(comments, pushAt),
    [comments, pushAt],
  );
  const actionable = visible.filter((thread) => thread.actionable);
  const [open, setOpen] = useState(true);
  const [selected, setSelected] = useState<Set<string>>(() => new Set());
  const [toggledThreads, setToggledThreads] = useState<Set<string>>(() => new Set());
  const [instructions, setInstructions] = useState<Record<string, string>>({});
  const [instruction, setInstruction] = useState("");
  const [copied, setCopied] = useState(false);
  const refreshRef = useRef(onRefresh);
  refreshRef.current = onRefresh;
  const selectedCount = actionable.filter((thread) => selected.has(thread.action.id)).length;
  const hasFreeInstruction = Boolean(instruction.trim());
  const allSelected = actionable.length > 0 && actionable.every((thread) => selected.has(thread.action.id));
  const checkState = typeof pr.checks === "string" ? pr.checks : pr.checks.state;
  const checks = typeof pr.checks === "string"
    ? pr.checks
    : pr.checks.total
      ? `${pr.checks.passed ?? 0}/${pr.checks.total}`
      : pr.checks.state;

  useEffect(() => {
    if (!refreshRef.current || !["draft", "open"].includes(pr.status)) return;
    void refreshRef.current(false);
    const timer = window.setInterval(() => void refreshRef.current?.(false), 60_000);
    return () => window.clearInterval(timer);
  }, [pr.status, pr.url]);

  async function sendFeedback() {
    if (!onSendFeedback) return;
    await onSendFeedback(
      visible
        .filter((thread) => thread.actionable && selected.has(thread.action.id))
        .map((thread) => ({
          comment_id: thread.action.id,
          instruction: instructions[thread.action.id]?.trim() ?? "",
        })),
      instruction.trim(),
    );
  }

  return (
    <section className="run-pr-panel">
      <div className="run-pr-summary">
        <BranchIcon size={14} />
        <span><strong>{pr.title || `PR #${pr.number ?? ""}`}</strong><small>PR #{pr.number ?? ""} · {pr.branch || "current branch"} → {pr.base}</small></span>
        <em className="tag">{pr.status}</em>
        <em className={`tag ${statusTone(checkState)}`}><CheckIcon size={9} /> checks {checks}</em>
        <em className={`tag ${statusTone(pr.review_state)}`}>review {pr.review_state.replaceAll("_", " ")}</em>
        <a className="btn primary sm" href={pr.url} target="_blank" rel="noreferrer">Open PR ↗</a>
        <button
          type="button"
          className="btn icon sm"
          aria-label="Copy pull request link"
          title="Copy pull request link"
          onClick={() => void navigator.clipboard.writeText(pr.url).then(() => {
            setCopied(true);
            window.setTimeout(() => setCopied(false), 1200);
          })}
        ><CopyIcon size={11} />{copied && <span className="sr-only">Copied</span>}</button>
      </div>

      {addressedComments.length > 0 && (
        <div className="run-pr-addressed">
          <strong><CheckIcon size={11} /> Addressed in this push</strong>
          {addressedComments.map((comment, index) => (
            <span key={String(comment.comment_id ?? index)}>
              <CheckIcon size={9} />
              <span>{String(comment.author || "reviewer")} · {String(comment.location || comment.body || "feedback")}</span>
              {Boolean(comment.instruction) && <small>your note · {String(comment.instruction)}</small>}
            </span>
          ))}
          {feedbackInstruction && <span><CheckIcon size={9} /><span>{feedbackInstruction}</span></span>}
        </div>
      )}

      <header className="run-pr-comments-head">
        <button type="button" onClick={() => setOpen((value) => !value)}>{open ? "▾" : "▸"} PR comments</button>
        <em className={actionable.length > 0 ? "tag warn" : "tag"}>{actionable.length} open</em>
        {visible.length > actionable.length && <em className="tag good">{visible.length - actionable.length} you replied</em>}
        <span>since this push{pr.last_synced_at && <> · synced {relativeTime(pr.last_synced_at)}</>}</span>
        {actionable.length > 0 && <label><input
          type="checkbox"
          checked={allSelected}
          onChange={(event) => setSelected(
            event.target.checked ? new Set(actionable.map((thread) => thread.action.id)) : new Set(),
          )}
        /> select all</label>}
        {onRefresh && <button className="btn sm" disabled={busy} onClick={() => void onRefresh(true)}><LoopIcon size={10} /> Check for comments</button>}
      </header>

      {open && (
        <div className="run-pr-comments-body">
          {visible.map((thread) => {
            const comment = thread.action;
            const checked = selected.has(comment.id);
            const expanded = thread.actionable !== toggledThreads.has(thread.id);
            return (
              <div className={`run-pr-comment${checked ? " selected" : ""}${thread.actionable ? "" : " acknowledged"}`} key={thread.id}>
                {thread.actionable ? <input
                  type="checkbox"
                  checked={checked}
                  disabled={!onSendFeedback}
                  onChange={(event) => setSelected((value) => {
                    const next = new Set(value);
                    if (event.target.checked) next.add(comment.id); else next.delete(comment.id);
                    return next;
                  })}
                /> : <span className="run-pr-thread-state"><CheckIcon size={10} /></span>}
                <span className="run-pr-comment-content">
                  <button type="button" className="run-pr-comment-summary" aria-expanded={expanded} onClick={() => setToggledThreads((value) => {
                    const next = new Set(value);
                    if (next.has(thread.id)) next.delete(thread.id); else next.add(thread.id);
                    return next;
                  })}>
                    <ChevronRightIcon className={expanded ? "open" : ""} size={14} />
                    <strong>{thread.root.author}</strong>
                    <small>{thread.root.location || "Pull request"}</small>
                    {!thread.actionable && <em>you replied</em>}
                    {thread.replies.length > 0 && <em>{thread.replies.length} {thread.replies.length === 1 ? "reply" : "replies"}</em>}
                  </button>
                  {expanded && <>
                    <p className="run-pr-comment-body">{thread.root.body}</p>
                    {thread.replies.length > 0 && <span className="run-pr-comment-replies">
                      {thread.replies.map((reply) => <span className={reply.is_viewer ? "viewer" : ""} key={reply.id}><strong>{reply.author}{reply.is_viewer ? " · you" : ""}</strong><p>{reply.body}</p></span>)}
                    </span>}
                  </>}
                </span>
                {checked && <input
                  value={instructions[comment.id] ?? ""}
                  placeholder="Optional instruction for the agent"
                  onChange={(event) => setInstructions((value) => ({ ...value, [comment.id]: event.target.value }))}
                />}
              </div>
            );
          })}
          {visible.length === 0 && <p className="dim">No new comments since this push.</p>}
          {onSendFeedback && <label className="run-pr-free-instruction"><span>Your instruction <small>no comment needed — refactors, missed scope, changed requirements</small></span><textarea rows={2} value={instruction} onChange={(event) => setInstruction(event.target.value)} placeholder="Additional work for the next implementation pass" /></label>}
          {onSendFeedback && <footer>
            <span>Unselected comments stay open.</span>
            <button className="btn primary sm" disabled={busy || (!selectedCount && !hasFreeInstruction)} onClick={() => void sendFeedback()}><ReturnIcon size={11} /> Send to Implement · {selectedCount}{hasFreeInstruction ? " + 1" : ""}</button>
          </footer>}
        </div>
      )}
    </section>
  );
}

function createdAfter(createdAt: string, pushedAt: string | null): boolean {
  if (!pushedAt) return true;
  const created = Date.parse(createdAt);
  const pushed = Date.parse(pushedAt);
  return Number.isFinite(created) && Number.isFinite(pushed) && created > pushed;
}

function prCommentThreads(
  comments: PrComment[],
  pushedAt: string | null,
): PrCommentThread[] {
  const grouped = new Map<string, PrComment[]>();
  for (const comment of comments) {
    const id = comment.kind === "review" && comment.reply_target_id
      ? comment.reply_target_id
      : comment.id;
    grouped.set(id, [...(grouped.get(id) ?? []), comment]);
  }
  return [...grouped.entries()].flatMap(([id, rows]) => {
    const ordered = rows.sort((left, right) => left.created_at.localeCompare(right.created_at));
    const latest = ordered.at(-1)!;
    if (
      latest.addressed_in_pass != null
      || !createdAfter(latest.created_at, pushedAt)
    ) return [];
    return [{ id, root: ordered[0], replies: ordered.slice(1), action: latest, actionable: !latest.is_viewer }];
  });
}

function relativeTime(value: string): string {
  const seconds = Math.max(0, Math.round((Date.now() - Date.parse(value)) / 1000));
  if (seconds < 60) return "just now";
  if (seconds < 3600) return `${Math.floor(seconds / 60)}m ago`;
  if (seconds < 86400) return `${Math.floor(seconds / 3600)}h ago`;
  return `${Math.floor(seconds / 86400)}d ago`;
}

function statusTone(value: string): "danger" | "good" | "warn" | "" {
  if (["failed", "changes_requested"].includes(value)) return "danger";
  if (["passed", "approved"].includes(value)) return "good";
  if (["pending"].includes(value)) return "warn";
  return "";
}
