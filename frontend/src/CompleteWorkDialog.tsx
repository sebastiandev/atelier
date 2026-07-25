import { useEffect, useState } from "react";

import {
  type CompletionWorkspace,
  type CompleteWorkPreview,
  type WorkDetail,
  completeWork,
  getWorkCompletion,
} from "./api";

type Props = {
  work: WorkDetail;
  onClose: () => void;
  onCompleted: (agentCount: number) => void;
};

/** Preview completion impact and archive a Work without risking local changes. */
export function CompleteWorkDialog({ work, onClose, onCompleted }: Props) {
  const [preview, setPreview] = useState<CompleteWorkPreview | null>(null);
  const [loading, setLoading] = useState(true);
  const [removeWorkspaces, setRemoveWorkspaces] = useState(false);
  const [submitting, setSubmitting] = useState(false);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    let cancelled = false;
    setLoading(true);
    setError(null);
    getWorkCompletion(work.slug)
      .then((result) => {
        if (!cancelled) setPreview(result);
      })
      .catch((reason) => {
        if (!cancelled) setError(errorMessage(reason));
      })
      .finally(() => {
        if (!cancelled) setLoading(false);
      });
    return () => {
      cancelled = true;
    };
  }, [work.slug]);

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
      const res = await completeWork(work.slug, {
        remove_workspaces: removeWorkspaces,
      });
      onCompleted(res.agent_count);
    } catch (e) {
      setError(errorMessage(e));
      setSubmitting(false);
    }
  };

  const plural = (n: number, w: string) => `${n} ${w}${n === 1 ? "" : "s"}`;
  const workspaces = preview?.workspaces ?? [];
  const canRemoveWorkspaces =
    workspaces.length > 0 && workspaces.every((workspace) => workspace.removable);
  const blockedByRuns = (preview?.active_run_count ?? 0) > 0;

  return (
    <div className="scrim" onClick={() => !submitting && onClose()}>
      <div
        className="modal modal-lg complete-work-modal"
        role="dialog"
        aria-modal="true"
        aria-labelledby="complete-work-title"
        onClick={(e) => e.stopPropagation()}
      >
        <div className="modal-hd">
          <div>
            <h3 id="complete-work-title">Complete {work.slug}?</h3>
            <p className="sub">{work.name}</p>
          </div>
          <button
            className="btn icon"
            onClick={onClose}
            aria-label="Close"
            disabled={submitting}
          >
            ×
          </button>
        </div>
        <div className="modal-bd">
          <p className="complete-work-copy">
            This archives the Work and stops its provider runtimes. Agent records,
            transcripts, and managed workspaces are preserved by default.
          </p>
          {loading && <div className="complete-work-loading">Inspecting workspaces...</div>}
          {preview && (
            <>
              <div className="complete-work-impact">
                <span>{plural(preview.agent_count, "agent record")} preserved</span>
                <span>{plural(workspaces.length, "managed workspace")}</span>
              </div>
              {blockedByRuns && (
                <div className="form-error" role="alert">
                  {plural(preview.active_run_count, "run")} still active. Finish or cancel
                  {preview.active_run_count === 1 ? " it" : " them"} before completing this Work.
                </div>
              )}
              <section className="complete-work-workspaces" aria-label="Managed workspaces">
                <div className="complete-work-section-title">Workspaces</div>
                {workspaces.length === 0 ? (
                  <div className="complete-work-empty">No managed workspaces.</div>
                ) : (
                  workspaces.map((workspace) => (
                    <WorkspaceState key={`${workspace.owner}:${workspace.path}`} workspace={workspace} />
                  ))
                )}
              </section>
              <label className={`complete-work-remove${canRemoveWorkspaces ? "" : " disabled"}`}>
                <input
                  type="checkbox"
                  checked={removeWorkspaces}
                  disabled={!canRemoveWorkspaces || submitting}
                  onChange={(event) => setRemoveWorkspaces(event.target.checked)}
                />
                <span>
                  <strong>Remove all managed workspaces after archiving</strong>
                  <small>
                    {canRemoveWorkspaces
                      ? "Optional. Every working tree is clean; review each branch or detached commit before removing."
                      : workspaces.length === 0
                        ? "There are no managed workspaces to remove."
                        : "Unavailable because at least one workspace has changes or cannot be inspected."}
                  </small>
                </span>
              </label>
            </>
          )}
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
            disabled={submitting || loading || !preview || blockedByRuns}
          >
            {submitting ? "Completing..." : "Complete work"}
          </button>
        </div>
      </div>
    </div>
  );
}

function WorkspaceState({ workspace }: { workspace: CompletionWorkspace }) {
  const changed = workspace.changed_files.length;
  const untracked = workspace.untracked_files.length;
  const state = workspace.error
    ? "Inspection failed"
    : !workspace.is_git_repo
      ? "Not a Git worktree"
      : workspace.removable
        ? "Clean"
        : "Changes present";
  return (
    <article className="complete-work-workspace">
      <header>
        <strong>{workspace.owner}</strong>
        <span className={workspace.removable ? "clean" : "protected"}>{state}</span>
      </header>
      <code title={workspace.path}>{workspace.path}</code>
      <div className="complete-work-workspace-meta">
        {workspace.is_git_repo && (
          <span>{workspace.branch ? `branch ${workspace.branch}` : "detached HEAD"}</span>
        )}
        {workspace.head && <span title={workspace.head}>commit {workspace.head.slice(0, 8)}</span>}
        {changed > 0 && <span>{pluralCount(changed, "changed file")}</span>}
        {untracked > 0 && <span>{pluralCount(untracked, "untracked file")}</span>}
      </div>
      {workspace.error && <p>{workspace.error}</p>}
      {(changed > 0 || untracked > 0) && (
        <ul>
          {workspace.changed_files.map((file) => <li key={`changed:${file}`}>{file} <em>changed</em></li>)}
          {workspace.untracked_files.map((file) => <li key={`untracked:${file}`}>{file} <em>untracked</em></li>)}
        </ul>
      )}
    </article>
  );
}

function pluralCount(count: number, label: string) {
  return `${count} ${label}${count === 1 ? "" : "s"}`;
}

function errorMessage(reason: unknown) {
  return reason instanceof Error && reason.message
    ? reason.message
    : "Failed to complete work";
}
