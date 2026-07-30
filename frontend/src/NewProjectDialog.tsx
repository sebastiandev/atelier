import { useEffect, useMemo, useRef, useState } from "react";

import {
  type CreateProjectPayload,
  type ProjectDetail,
  createProject,
  deriveGlyph,
} from "./api";
import { FolderPickerDialog } from "./FolderPickerDialog";
import { FolderIcon } from "./Icons";

type Props = {
  onClose: () => void;
  onCreated: (project: ProjectDetail) => void;
};

const SWATCHES = [20, 75, 150, 200, 250, 290, 340];

export function NewProjectDialog({ onClose, onCreated }: Props) {
  const [name, setName] = useState("");
  const [description, setDescription] = useState("");
  const [color, setColor] = useState<number>(250);
  const [defaultFolder, setDefaultFolder] = useState("");
  const [pickerOpen, setPickerOpen] = useState(false);
  const [submitting, setSubmitting] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const nameRef = useRef<HTMLInputElement>(null);

  useEffect(() => {
    nameRef.current?.focus();
    function onKey(e: KeyboardEvent) {
      if (e.key === "Escape") onClose();
    }
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [onClose]);

  const effectiveGlyph = useMemo(() => deriveGlyph(name || "?"), [name]);

  const canSubmit = name.trim().length > 0 && effectiveGlyph.length > 0 && !submitting;

  async function submit() {
    if (!canSubmit) return;
    setSubmitting(true);
    setError(null);
    const payload: CreateProjectPayload = {
      name: name.trim(),
      description: description.trim(),
      glyph: effectiveGlyph.slice(0, 2).toUpperCase(),
      color,
      default_folder: defaultFolder.trim() || null,
    };
    try {
      const created = await createProject(payload);
      onCreated(created);
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err));
      setSubmitting(false);
    }
  }

  return (
    <div className="scrim" onClick={onClose}>
      <div
        className="modal new-project-modal"
        onClick={(e) => e.stopPropagation()}
        role="dialog"
        aria-modal="true"
        style={{ ["--proj-h" as string]: String(color) }}
      >
        <div className="modal-hd">
          <div>
            <h3>New project</h3>
          </div>
          <button className="btn ghost icon sm" type="button" onClick={onClose} aria-label="Close">
            ×
          </button>
        </div>

        <div className="modal-bd">
          <label className="nw-field">
            <span className="nw-lbl">Name</span>
            <input
              ref={nameRef}
              className="nw-input"
              placeholder="e.g. Acme Web"
              value={name}
              onChange={(e) => setName(e.target.value)}
            />
          </label>

          <label className="nw-field">
            <span className="nw-lbl">Description <small>optional</small></span>
            <input
              className="nw-input"
              placeholder="One sentence on what this project covers."
              value={description}
              onChange={(e) => setDescription(e.target.value)}
            />
          </label>

          <div className="nw-field">
            <span className="nw-lbl">Color</span>
            <div className="swatch-row">
              {SWATCHES.map((h) => (
                <button
                  key={h}
                  type="button"
                  aria-label={`Hue ${h}`}
                  className={"swatch" + (color === h ? " selected" : "")}
                  style={{ ["--swatch-h" as string]: String(h) }}
                  onClick={() => setColor(h)}
                />
              ))}
            </div>
          </div>

          <label className="nw-field">
            <span className="nw-lbl">Default folder <small>optional · used for new work</small></span>
            <span className="np-folder-row">
              <input
                className="nw-input"
                placeholder="/Users/you/code/acme"
                value={defaultFolder}
                onChange={(e) => setDefaultFolder(e.target.value)}
              />
              <button
                className="btn icon"
                type="button"
                onClick={() => setPickerOpen(true)}
                aria-label="Choose default folder"
                title="Choose default folder"
              >
                <FolderIcon size={13} />
              </button>
            </span>
          </label>

          {error && <div className="form-error">{error}</div>}
        </div>

        <div className="modal-ft">
          <button className="btn" type="button" onClick={onClose} disabled={submitting}>
            Cancel
          </button>
          <button className="btn primary" type="button" disabled={!canSubmit} onClick={submit}>
            {submitting ? "Creating…" : "Create project"}
          </button>
        </div>
      </div>

      {pickerOpen && (
        <FolderPickerDialog
          initialPath={defaultFolder.trim() || null}
          onCancel={() => setPickerOpen(false)}
          onPick={(path) => {
            setDefaultFolder(path);
            setPickerOpen(false);
          }}
        />
      )}
    </div>
  );
}
