import { useState } from "react";

import { type AgentSummary, deleteAgent } from "./api";

type Props = {
  agent: AgentSummary;
  onClose: () => void;
  // Called after the agent has been removed server-side. The caller
  // refreshes the agents list and clears any focused/closed state.
  onDeleted: () => void;
};

/**
 * Confirmation dialog for permanently deleting an agent.
 *
 * Spells out exactly what disappears (DB row, transcript, contexts,
 * per-agent worktree) so the user knows it's irreversible. The work
 * itself, sibling agents, and any artifacts the agent reported stay
 * intact — those are work-scoped, not agent-scoped.
 *
 * No Esc-to-close: that key is reserved for "stop the agent's current
 * turn" elsewhere in the UI; binding it here would create a confusing
 * double meaning. Click the backdrop or Cancel to dismiss.
 */
export function DeleteAgentDialog({ agent, onClose, onDeleted }: Props) {
  const [submitting, setSubmitting] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const submit = async () => {
    setSubmitting(true);
    setError(null);
    try {
      await deleteAgent(agent.slug);
      onDeleted();
    } catch (e) {
      setError((e as Error).message || "Failed to delete agent");
      setSubmitting(false);
    }
  };

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
            <h3>Delete agent {agent.slug}?</h3>
            <p className="sub">
              {agent.name} · {agent.role}
            </p>
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
            This permanently removes:
          </p>
          <ul style={{ margin: 0, paddingLeft: "1.2rem", fontSize: 13, lineHeight: 1.55 }}>
            <li>The agent's transcript, contexts, and metadata</li>
            <li>Its per-agent git worktree (if one was provisioned)</li>
            <li>The agent record itself</li>
          </ul>
          <p className="hint" style={{ margin: 0 }}>
            Your source folder ({agent.folder}) is left untouched, and so
            are sibling agents, the parent work, and any artifacts the
            agent already reported. This cannot be undone.
          </p>
          {error && <div className="form-error">{error}</div>}
        </div>
        <div className="modal-ft">
          <button className="btn" onClick={onClose} disabled={submitting}>
            Cancel
          </button>
          <span className="spacer" />
          <button className="btn danger" onClick={submit} disabled={submitting}>
            {submitting ? "Deleting…" : "Delete agent"}
          </button>
        </div>
      </div>
    </div>
  );
}
