import { useState } from "react";

import type { AcceptPlanArtifactPayload, PlanArtifact } from "./api";

/**
 * Accept an agent-mode story. There is no run to accept, so the summary is
 * the user's: prefilled with the story's tracked pull requests so "what
 * landed" is one edit away. Optionally closes the story's tiles to the rail.
 */
export function MarkStoryDoneDialog({
  artifact,
  prLines,
  agentCount,
  onClose,
  onAccept,
}: {
  artifact: PlanArtifact;
  /** One line per tracked PR: "#218 merged · feat(auth): password fallback". */
  prLines: string[];
  agentCount: number;
  onClose: () => void;
  onAccept: (payload: AcceptPlanArtifactPayload, closeAgents: boolean) => Promise<void>;
}) {
  const [summary, setSummary] = useState(() =>
    prLines.length > 0 ? `Landed via:\n${prLines.map((l) => `- ${l}`).join("\n")}` : "",
  );
  const [changes, setChanges] = useState("");
  const [closeAgents, setCloseAgents] = useState(agentCount > 0);
  const [submitting, setSubmitting] = useState(false);
  const [error, setError] = useState<string | null>(null);

  async function submit() {
    setSubmitting(true);
    setError(null);
    try {
      await onAccept({ summary: summary.trim(), changes: changes.trim() }, closeAgents);
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err));
      setSubmitting(false);
    }
  }

  return (
    <div className="scrim" onClick={() => !submitting && onClose()}>
      <div
        className="modal mark-done-modal"
        role="dialog"
        aria-labelledby="mark-done-title"
        onClick={(e) => e.stopPropagation()}
      >
        <div className="modal-hd">
          <div>
            <h3 id="mark-done-title">Mark {artifact.id} done</h3>
            <p className="sub">{artifact.title}</p>
          </div>
        </div>
        <div className="modal-bd">
          <label className="field">
            <span className="label">Summary</span>
            <textarea
              className="textarea sm"
              rows={5}
              value={summary}
              onChange={(e) => setSummary(e.target.value)}
              placeholder="What landed, and where."
            />
          </label>
          <label className="field">
            <span className="label">
              Changes <span className="hint">(optional)</span>
            </span>
            <textarea
              className="textarea sm"
              rows={2}
              value={changes}
              onChange={(e) => setChanges(e.target.value)}
              placeholder="Files or areas touched."
            />
          </label>
          {agentCount > 0 && (
            <label className="mark-done-close">
              <input
                type="checkbox"
                checked={closeAgents}
                onChange={(e) => setCloseAgents(e.target.checked)}
              />
              Close the {agentCount} agent tile{agentCount === 1 ? "" : "s"} to the rail (runs keep going)
            </label>
          )}
          <p className="hint">
            The story reads <em>accepted</em> until its source changes. The summary is saved next to the plan.
          </p>
          {error && <div className="form-error">{error}</div>}
        </div>
        <div className="modal-ft">
          <button className="btn" onClick={onClose} disabled={submitting}>Cancel</button>
          <button className="btn primary" onClick={() => void submit()} disabled={submitting}>
            {submitting ? "Saving…" : "Mark done"}
          </button>
        </div>
      </div>
    </div>
  );
}
