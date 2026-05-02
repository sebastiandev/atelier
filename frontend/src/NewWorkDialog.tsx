import { useEffect, useRef, useState } from "react";

import type { CreateWorkPayload } from "./api";

type Props = {
  onClose: () => void;
  onCreate: (payload: CreateWorkPayload) => Promise<void>;
};

export function NewWorkDialog({ onClose, onCreate }: Props) {
  const [name, setName] = useState("");
  const [desc, setDesc] = useState("");
  const [folder, setFolder] = useState("");
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

  const canSubmit = name.trim() && desc.trim() && !submitting;

  async function submit() {
    if (!canSubmit) return;
    const trimmedName = name.trim();
    const slug = trimmedName.toLowerCase().replace(/\s+/g, "-");
    const payload: CreateWorkPayload = {
      name: trimmedName,
      description: desc.trim(),
      folder: folder.trim() || `~/work/${slug}`,
    };
    setSubmitting(true);
    setError(null);
    try {
      await onCreate(payload);
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err));
      setSubmitting(false);
    }
  }

  return (
    <div className="scrim" onClick={onClose}>
      <div className="modal" onClick={(e) => e.stopPropagation()} role="dialog" aria-modal="true">
        <div className="modal-hd">
          <div>
            <h3>New work</h3>
            <div className="sub">
              Define the goal and any constraints. You'll spawn agents in the next view.
            </div>
          </div>
          <button className="btn-icon" onClick={onClose} aria-label="Close">
            ×
          </button>
        </div>

        <div className="modal-bd">
          <label className="field">
            <span className="label">Name</span>
            <input
              ref={nameRef}
              className="input"
              placeholder="e.g. Fix checkout 500 spike"
              value={name}
              onChange={(e) => setName(e.target.value)}
            />
          </label>

          <label className="field">
            <span className="label">Brief description</span>
            <textarea
              className="textarea"
              rows={3}
              placeholder="What does done look like? Any constraints?"
              value={desc}
              onChange={(e) => setDesc(e.target.value)}
            />
          </label>

          <label className="field">
            <span className="label">
              Working folder <span className="hint">· optional</span>
            </span>
            <input
              className="input"
              placeholder={`~/work/${(name || "new-work").toLowerCase().replace(/\s+/g, "-")}`}
              value={folder}
              onChange={(e) => setFolder(e.target.value)}
            />
            <span className="hint">
              If it's a git repo, agents will spawn worktrees here automatically.
            </span>
          </label>

          {error && <div className="form-error">{error}</div>}
        </div>

        <div className="modal-ft">
          <span className="hint" style={{ marginRight: "auto" }}>
            You can edit everything later.
          </span>
          <button className="btn" onClick={onClose} disabled={submitting}>
            Cancel
          </button>
          <button className="btn primary" disabled={!canSubmit} onClick={submit}>
            {submitting ? "Creating…" : "Create work"}
          </button>
        </div>
      </div>
    </div>
  );
}
