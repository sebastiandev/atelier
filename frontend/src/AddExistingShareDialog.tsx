import { useEffect, useState } from "react";

import { createProjectShare, type SharedFolderSummary } from "./api";
import { FolderPickerDialog } from "./FolderPickerDialog";

type Props = {
  projectSlug: string;
  projectName: string;
  onClose: () => void;
  onCreated: (share: SharedFolderSummary) => void;
};

/**
 * "+ Add existing folder" — point Atelier at a folder that already
 * exists on disk.
 *
 * Nondestructive: we don't move or copy anything. Atelier just creates
 * a symlink at the share's canonical path pointing to the user's
 * folder, so every agent in the project sees it via mount path.
 */
export function AddExistingShareDialog({
  projectSlug,
  projectName,
  onClose,
  onCreated,
}: Props) {
  const [folderPath, setFolderPath] = useState("");
  const [mountPath, setMountPath] = useState("");
  const [mountTouched, setMountTouched] = useState(false);
  const [label, setLabel] = useState("");
  const [labelTouched, setLabelTouched] = useState(false);
  const [pickerOpen, setPickerOpen] = useState(false);
  const [submitting, setSubmitting] = useState(false);
  const [error, setError] = useState<string | null>(null);

  // Default mount path + label to the basename of the picked folder so
  // the common case ("/Users/seba/src/atelier/_bmad-output" → mount at
  // _bmad-output) needs zero typing after browse.
  useEffect(() => {
    if (!folderPath) return;
    const basename = folderPath.replace(/\/+$/, "").split("/").pop() || "";
    if (!mountTouched && basename) setMountPath(basename);
    if (!labelTouched && basename) setLabel(basename);
  }, [folderPath, mountTouched, labelTouched]);

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
      const share = await createProjectShare(projectSlug, {
        mode: "existing",
        name: label.trim() || mountPath.trim(),
        mount_path: mountPath.trim(),
        location: folderPath.trim(),
      });
      onCreated(share);
    } catch (e) {
      setError((e as Error).message || "Failed to add share");
      setSubmitting(false);
    }
  };

  const canSubmit =
    !!folderPath.trim() && !!mountPath.trim() && !submitting;

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
            <h3>Add existing folder</h3>
            <p className="sub">in {projectName}</p>
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
          <label className="field">
            <span className="label">Folder path</span>
            <div className="folder-input-row">
              <input
                className="input mono"
                placeholder="/Users/you/src/your-repo/_bmad-output"
                value={folderPath}
                onChange={(e) => setFolderPath(e.target.value)}
                autoFocus
              />
              <button
                type="button"
                className="btn-icon folder-input-pick"
                onClick={() => setPickerOpen(true)}
                aria-label="Browse"
                title="Browse"
              >
                📁
              </button>
            </div>
            <span className="hint">
              The folder on disk you want to share across this project's
              agents. We won't move or copy anything — just symlink to it.
            </span>
          </label>
          <label className="field">
            <span className="label">Mount path</span>
            <input
              className="input mono"
              placeholder="_bmad-output"
              value={mountPath}
              onChange={(e) => {
                setMountTouched(true);
                setMountPath(e.target.value);
              }}
            />
            <span className="hint">
              Where this share appears inside each agent's worktree.
              Defaults to the folder's name; change it if your tool
              expects a different path.
            </span>
          </label>
          <label className="field">
            <span className="label">Name</span>
            <input
              className="input"
              placeholder="Defaults to the folder's name"
              value={label}
              onChange={(e) => {
                setLabelTouched(true);
                setLabel(e.target.value);
              }}
            />
            <span className="hint">Display label only — edit any time.</span>
          </label>
          {error && <div className="form-error">{error}</div>}
        </div>
        <div className="modal-ft">
          <button className="btn" onClick={onClose} disabled={submitting}>
            Cancel
          </button>
          <span className="spacer" />
          <button
            className="btn primary"
            disabled={!canSubmit}
            onClick={submit}
          >
            {submitting ? "Adding…" : "Add"}
          </button>
        </div>
      </div>
      {pickerOpen && (
        <FolderPickerDialog
          initialPath={folderPath.trim() || null}
          onCancel={() => setPickerOpen(false)}
          onPick={(p) => {
            setFolderPath(p);
            setPickerOpen(false);
          }}
        />
      )}
    </div>
  );
}
