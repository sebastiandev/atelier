import { useEffect, useMemo, useRef, useState } from "react";

import {
  type Connection,
  type CreateProjectPayload,
  type ProjectDetail,
  createProject,
  deriveGlyph,
  listConnections,
} from "./api";

type Props = {
  onClose: () => void;
  onCreated: (project: ProjectDetail) => void;
};

// 7 OKLCH hues from the design handoff. Same chroma + lightness so they
// share visual weight; hue is the only knob.
const SWATCHES = [250, 165, 30, 0, 320, 200, 100];

export function NewProjectDialog({ onClose, onCreated }: Props) {
  const [name, setName] = useState("");
  const [glyphTouched, setGlyphTouched] = useState(false);
  const [glyph, setGlyph] = useState("");
  const [description, setDescription] = useState("");
  const [color, setColor] = useState<number>(SWATCHES[0]);
  const [connections, setConnections] = useState<Connection[]>([]);
  const [defaultJira, setDefaultJira] = useState<string>("");
  const [defaultSentry, setDefaultSentry] = useState<string>("");
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

  useEffect(() => {
    listConnections()
      .then(setConnections)
      .catch(() => setConnections([]));
  }, []);

  // Auto-derive glyph from name until the user types into the glyph field.
  const derivedGlyph = useMemo(() => deriveGlyph(name || "?"), [name]);
  const effectiveGlyph = glyphTouched ? glyph : derivedGlyph;

  const jiraConnections = connections.filter((c) => c.config.type === "jira");
  const sentryConnections = connections.filter((c) => c.config.type === "sentry");

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
      default_jira_conn: defaultJira || null,
      default_sentry_conn: defaultSentry || null,
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
        className="modal modal-sm"
        onClick={(e) => e.stopPropagation()}
        role="dialog"
        aria-modal="true"
        style={{ ["--proj-h" as string]: String(color) }}
      >
        <div className="modal-hd">
          <div>
            <h3>New project</h3>
            <div className="sub">
              Group related work and inherit default Jira / Sentry connections.
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
              placeholder="e.g. Acme Web"
              value={name}
              onChange={(e) => setName(e.target.value)}
            />
          </label>

          <div className="field" style={{ flexDirection: "row", alignItems: "center", gap: "0.75rem" }}>
            <span className="pc-glyph" aria-hidden="true">
              {effectiveGlyph.slice(0, 2)}
            </span>
            <label className="field" style={{ flex: 1 }}>
              <span className="label">
                Glyph <span className="hint">· auto from name</span>
              </span>
              <input
                className="input"
                value={effectiveGlyph}
                maxLength={2}
                onChange={(e) => {
                  setGlyphTouched(true);
                  setGlyph(e.target.value.toUpperCase());
                }}
              />
            </label>
          </div>

          <label className="field">
            <span className="label">
              Description <span className="hint">· optional</span>
            </span>
            <textarea
              className="textarea"
              rows={2}
              placeholder="One sentence on what this project covers."
              value={description}
              onChange={(e) => setDescription(e.target.value)}
            />
          </label>

          <div className="field">
            <span className="label">Color</span>
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

          {jiraConnections.length > 0 && (
            <label className="field">
              <span className="label">
                Default Jira connection <span className="hint">· optional</span>
              </span>
              <select
                className="input"
                value={defaultJira}
                onChange={(e) => setDefaultJira(e.target.value)}
              >
                <option value="">—</option>
                {jiraConnections.map((c) => (
                  <option key={c.slug} value={c.slug}>
                    {c.name}
                  </option>
                ))}
              </select>
            </label>
          )}

          {sentryConnections.length > 0 && (
            <label className="field">
              <span className="label">
                Default Sentry connection <span className="hint">· optional</span>
              </span>
              <select
                className="input"
                value={defaultSentry}
                onChange={(e) => setDefaultSentry(e.target.value)}
              >
                <option value="">—</option>
                {sentryConnections.map((c) => (
                  <option key={c.slug} value={c.slug}>
                    {c.name}
                  </option>
                ))}
              </select>
            </label>
          )}

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
            {submitting ? "Creating…" : "Create project"}
          </button>
        </div>
      </div>
    </div>
  );
}
