import { useEffect, useState } from "react";

import { type WorkDetail, deleteWork } from "./api";

type Props = {
  work: WorkDetail;
  agentCount: number;
  chatCount: number;
  onClose: () => void;
  onDeleted: () => void;
};

export function DeleteWorkDialog({
  work,
  agentCount,
  chatCount,
  onClose,
  onDeleted,
}: Props) {
  const [submitting, setSubmitting] = useState(false);
  const [confirm, setConfirm] = useState("");
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      if (e.key === "Escape" && !submitting) onClose();
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [onClose, submitting]);

  const submit = async () => {
    if (confirm !== work.slug) return;
    setSubmitting(true);
    setError(null);
    try {
      await deleteWork(work.slug);
      onDeleted();
    } catch (e) {
      setError((e as Error).message || "Failed to delete work");
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
            <h3>Delete {work.slug} permanently?</h3>
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
            This will permanently remove:
          </p>
          <ul style={{ margin: 0, paddingLeft: "1.2rem", fontSize: 13, lineHeight: 1.55 }}>
            <li>The work folder, brief, handoffs, and artifacts</li>
            <li>{plural(agentCount, "agent")} and their transcripts</li>
            <li>{plural(chatCount, "work chat")} linked to this work</li>
            <li>{plural(agentCount, "git worktree")} used as scratch space</li>
          </ul>
          <label className="field">
            <span>Type {work.slug} to confirm</span>
            <input
              value={confirm}
              onChange={(e) => setConfirm(e.target.value)}
              autoFocus
              disabled={submitting}
              spellCheck={false}
            />
          </label>
          {error && <div className="form-error">{error}</div>}
        </div>
        <div className="modal-ft">
          <button className="btn" onClick={onClose} disabled={submitting}>
            Cancel
          </button>
          <span className="spacer" />
          <button
            className="btn danger"
            onClick={submit}
            disabled={submitting || confirm !== work.slug}
          >
            {submitting ? "Deleting…" : "Delete permanently"}
          </button>
        </div>
      </div>
    </div>
  );
}
