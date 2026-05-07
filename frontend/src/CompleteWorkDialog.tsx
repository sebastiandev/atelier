import { useEffect, useState } from "react";

import { type WorkDetail, completeWork } from "./api";

type Props = {
  work: WorkDetail;
  agentCount: number;
  onClose: () => void;
  // Called after the work is successfully marked complete. The caller
  // typically navigates back to the workspace (active works list) since
  // a completed work falls out of the default filter there.
  onCompleted: (agentCount: number) => void;
};

/**
 * Confirmation dialog for marking a work as complete.
 *
 * Spells out the side effects (stop agents + remove worktrees) so the
 * user knows what's irreversible vs preserved. Transcripts and the work
 * folder under ``~/Atelier/works/<slug>/`` survive completion — only the
 * per-agent git worktrees (scratch space) are removed.
 */
export function CompleteWorkDialog({ work, agentCount, onClose, onCompleted }: Props) {
  const [submitting, setSubmitting] = useState(false);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      if (e.key === "Escape" && !submitting) onClose();
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [onClose, submitting]);

  const submit = async () => {
    setSubmitting(true);
    setError(null);
    try {
      const res = await completeWork(work.slug);
      onCompleted(res.agent_count);
    } catch (e) {
      setError((e as Error).message || "Failed to complete work");
      setSubmitting(false);
    }
  };

  const plural = (n: number, w: string) => `${n} ${w}${n === 1 ? "" : "s"}`;

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
            <h3>Mark {work.slug} as complete?</h3>
            <p className="sub">{work.name}</p>
          </div>
          <button
            className="btn-icon"
            onClick={onClose}
            aria-label="Close"
            disabled={submitting}
          >
            ×
          </button>
        </div>
        <div className="modal-bd">
          <p style={{ margin: 0, fontSize: 13, color: "var(--fg-2)" }}>
            This will:
          </p>
          <ul style={{ margin: 0, paddingLeft: "1.2rem", fontSize: 13, lineHeight: 1.55 }}>
            <li>Stop {plural(agentCount, "agent")} on this work</li>
            <li>Remove {plural(agentCount, "git worktree")} (per-agent scratch space)</li>
            <li>Move the work to your Completed list (still viewable)</li>
          </ul>
          <p className="hint" style={{ margin: 0 }}>
            Transcripts and the work folder are preserved.
          </p>
          {error && <div className="form-error">{error}</div>}
        </div>
        <div className="modal-ft">
          <button className="btn" onClick={onClose} disabled={submitting}>
            Cancel
          </button>
          <span className="spacer" />
          <button className="btn primary" onClick={submit} disabled={submitting}>
            {submitting ? "Completing…" : "Complete work"}
          </button>
        </div>
      </div>
    </div>
  );
}
