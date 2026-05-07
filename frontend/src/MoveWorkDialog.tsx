import { useEffect, useState } from "react";

import {
  type ProjectSummary,
  type WorkDetail,
  moveWorkToProject,
} from "./api";

type Props = {
  work: WorkDetail;
  projects: ProjectSummary[];
  onClose: () => void;
  // Called after a successful move with the updated work payload. The
  // caller typically refreshes its `work` state so the breadcrumb/chips
  // re-render against the new project.
  onMoved: (updated: WorkDetail) => void;
};

/**
 * Picker dialog for re-parenting a work to a different project (or to
 * Loose). Mirrors the project-picker pattern from NewWorkDialog so the
 * same chip styling carries over. The current project is preselected so
 * "Move" stays disabled until the user actually picks a new target.
 */
export function MoveWorkDialog({ work, projects, onClose, onMoved }: Props) {
  const [target, setTarget] = useState<string | null>(work.project_slug);
  const [submitting, setSubmitting] = useState(false);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      if (e.key === "Escape" && !submitting) onClose();
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [onClose, submitting]);

  const noChange = target === work.project_slug;

  const submit = async () => {
    if (noChange) return;
    setSubmitting(true);
    setError(null);
    try {
      const updated = await moveWorkToProject(work.slug, target);
      onMoved(updated);
    } catch (e) {
      setError((e as Error).message || "Failed to move work");
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
            <h3>Move {work.slug} to project</h3>
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
          <div className="proj-pick-row">
            <button
              type="button"
              className={"proj-pick-chip" + (target == null ? " active" : "")}
              onClick={() => setTarget(null)}
              disabled={submitting}
            >
              Loose
            </button>
            {projects.map((p) => (
              <button
                key={p.slug}
                type="button"
                className={
                  "proj-pick-chip" + (target === p.slug ? " active" : "")
                }
                style={{ ["--proj-h" as string]: String(p.color) }}
                onClick={() => setTarget(p.slug)}
                disabled={submitting}
              >
                <span className="proj-pick-glyph mono">{p.glyph}</span>
                {p.name}
              </button>
            ))}
          </div>
          {error && <div className="form-error">{error}</div>}
        </div>
        <div className="modal-ft">
          <button className="btn" onClick={onClose} disabled={submitting}>
            Cancel
          </button>
          <span className="spacer" />
          <button
            className="btn primary"
            onClick={submit}
            disabled={submitting || noChange}
            title={noChange ? "Pick a different project to move" : undefined}
          >
            {submitting ? "Moving…" : "Move"}
          </button>
        </div>
      </div>
    </div>
  );
}
