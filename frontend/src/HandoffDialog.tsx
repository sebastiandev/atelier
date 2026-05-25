import { useEffect, useState } from "react";

import { type AgentSummary, type HandoffSummary, createHandoff } from "./api";

type Props = {
  workSlug: string;
  source: AgentSummary;
  onClose: () => void;
  /** Fires once the summarizer has produced the doc and the Handoff row
   *  is persisted. The parent (WorkView) then opens NewAgentDialog with
   *  the doc as initial-goal and ``forkFromAgent`` set to the source. */
  onHandoffReady: (handoff: HandoffSummary) => void;
};

/**
 * Two-step handoff trigger. Step 1 (this dialog) generates the
 * checkpoint doc — synchronous LLM call, takes a few seconds. Step 2
 * is the standard NewAgentDialog opened by the parent with the doc
 * pre-filled as the new agent's initial goal.
 *
 * Kept tiny on purpose: the heavy form work belongs in NewAgentDialog
 * where the user picks persona/model/folder. This dialog's only job is
 * "kick off summarisation, hand the result up".
 */
export function HandoffDialog({
  workSlug,
  source,
  onClose,
  onHandoffReady,
}: Props) {
  const [generating, setGenerating] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const canInterruptActiveCodexTurn =
    source.provider === "codex" &&
    (source.status === "thinking" || source.status === "live");

  useEffect(() => {
    function onKey(e: KeyboardEvent) {
      if (e.key === "Escape" && !generating) onClose();
    }
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [onClose, generating]);

  async function generate() {
    setGenerating(true);
    setError(null);
    try {
      const handoff = await createHandoff(workSlug, source.slug);
      onHandoffReady(handoff);
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err));
      setGenerating(false);
    }
  }

  return (
    <div className="scrim" onClick={generating ? undefined : onClose}>
      <div
        className="modal modal-sm"
        onClick={(e) => e.stopPropagation()}
        role="dialog"
        aria-modal="true"
      >
        <div className="modal-hd">
          <div>
            <h3>Hand off to a new agent</h3>
            <div className="sub">
              Summarises {source.name}'s recent work as a Markdown doc.
              The next step picks the new agent's persona and confirms
              the workdir.
            </div>
          </div>
          {!generating && (
            <button className="btn-icon" onClick={onClose} aria-label="Close">
              ×
            </button>
          )}
        </div>

        <div className="modal-bd">
          <div className="field">
            <span className="label">Source agent</span>
            <div className="rail-agent" data-persona={source.persona}>
              <span className="meta">
                <span className="name mono">{source.name}</span>
                <span className="role">{source.role}</span>
              </span>
            </div>
          </div>
          <div className="hint">
            Atelier hands {source.name}'s entire transcript to the summariser
            (capped to roughly 200K tokens — older events drop first if you
            hit the limit). The new agent inherits {source.name}'s uncommitted
            work via a forked worktree (detached HEAD; no auto-branch).{" "}
            {source.name} stays alive — close it manually once you're done.
          </div>
          {canInterruptActiveCodexTurn && (
            <div className="hint">
              Note: wait for the current Codex turn to finish before detaching
              this agent to the CLI. Detaching while it is still running
              interrupts that turn, and the CLI will resume from the
              interrupted state.
            </div>
          )}
          {error && <div className="form-error">{error}</div>}
        </div>

        <div className="modal-ft">
          <button className="btn" onClick={onClose} disabled={generating}>
            Cancel
          </button>
          <button
            className="btn primary"
            onClick={generate}
            disabled={generating}
          >
            {generating ? "Generating doc…" : "Generate handoff"}
          </button>
        </div>
      </div>
    </div>
  );
}
