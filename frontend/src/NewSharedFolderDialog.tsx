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
 * "+ New shared folder" — create a fresh empty share.
 *
 * Defaults to the canonical location under the project dir; a "Custom
 * location" toggle reveals a path picker for users who want the data
 * in iCloud / Dropbox / etc. The label defaults to the mount path so
 * the user can ship without typing twice.
 */
export function NewSharedFolderDialog({
  projectSlug,
  projectName,
  onClose,
  onCreated,
}: Props) {
  const [mountPath, setMountPath] = useState("");
  const [label, setLabel] = useState("");
  const [labelTouched, setLabelTouched] = useState(false);
  const [customLocation, setCustomLocation] = useState(false);
  const [location, setLocation] = useState("");
  const [pickerOpen, setPickerOpen] = useState(false);
  const [submitting, setSubmitting] = useState(false);
  const [error, setError] = useState<string | null>(null);

  // Mirror the mount path into the label until the user starts typing
  // their own label — same UX pattern as Add-existing.
  useEffect(() => {
    if (!labelTouched) setLabel(mountPath);
  }, [mountPath, labelTouched]);

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
        mode: "new",
        name: label.trim() || mountPath.trim(),
        mount_path: mountPath.trim(),
        location: customLocation ? location.trim() : null,
      });
      onCreated(share);
    } catch (e) {
      setError((e as Error).message || "Failed to create share");
      setSubmitting(false);
    }
  };

  const canSubmit =
    !!mountPath.trim() &&
    !submitting &&
    (!customLocation || !!location.trim());

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
            <h3>New shared folder</h3>
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
            <span className="label">Mount path</span>
            <input
              className="input mono"
              placeholder="_bmad-output"
              value={mountPath}
              onChange={(e) => setMountPath(e.target.value)}
              autoFocus
            />
            <span className="hint">
              Where this share appears inside each agent's worktree.
              Match the path your tool expects (e.g. <code>_bmad-output</code>{" "}
              for BMAD).
            </span>
          </label>
          <label className="field">
            <span className="label">Name</span>
            <input
              className="input"
              placeholder="Defaults to the mount path"
              value={label}
              onChange={(e) => {
                setLabelTouched(true);
                setLabel(e.target.value);
              }}
            />
            <span className="hint">Display label only — edit any time.</span>
          </label>
          <div className="field">
            <span className="label">Location</span>
            <label className="share-loc-opt">
              <input
                type="radio"
                checked={!customLocation}
                onChange={() => setCustomLocation(false)}
              />
              <span>
                <strong>Atelier (default)</strong>
                <span className="hint">
                  Stored under{" "}
                  <code>~/Atelier/projects/{projectSlug}/shared/</code>.
                </span>
              </span>
            </label>
            <label className="share-loc-opt">
              <input
                type="radio"
                checked={customLocation}
                onChange={() => setCustomLocation(true)}
              />
              <span>
                <strong>Custom location</strong>
                <span className="hint">
                  Anywhere on disk. Useful if your share lives in iCloud
                  / Dropbox / Obsidian / another sync target.
                </span>
              </span>
            </label>
            {customLocation && (
              <div className="folder-input-row" style={{ marginTop: "0.4rem" }}>
                <input
                  className="input mono"
                  placeholder="/Users/you/iCloud/atelier-notes"
                  value={location}
                  onChange={(e) => setLocation(e.target.value)}
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
            )}
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
            disabled={!canSubmit}
            onClick={submit}
          >
            {submitting ? "Creating…" : "Create"}
          </button>
        </div>
      </div>
      {pickerOpen && (
        <FolderPickerDialog
          initialPath={location.trim() || null}
          onCancel={() => setPickerOpen(false)}
          onPick={(p) => {
            setLocation(p);
            setPickerOpen(false);
          }}
        />
      )}
    </div>
  );
}
