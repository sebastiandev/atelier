import { useEffect, useRef, useState } from "react";

import {
  type Connection,
  type PatchProjectPayload,
  type ProjectDetail,
  deleteProject as deleteProjectApi,
  listConnections,
  patchProject,
} from "./api";

type Props = {
  project: ProjectDetail;
  onClose: () => void;
  /** Receives the updated project. Caller refreshes any cached views. */
  onSaved: (project: ProjectDetail) => void;
  /** Caller is responsible for navigating away (the project no longer
   *  exists on screen). The backend has already deleted it server-side
   *  by the time this fires. */
  onDeleted: () => void;
};

// Same palette as NewProjectDialog — keep the two dialogs visually aligned.
const SWATCHES = [250, 165, 30, 0, 320, 200, 100];

export function EditProjectDialog({
  project,
  onClose,
  onSaved,
  onDeleted,
}: Props) {
  const [name, setName] = useState(project.name);
  const [description, setDescription] = useState(project.description);
  const [glyph, setGlyph] = useState(project.glyph);
  const [color, setColor] = useState<number>(project.color);
  const [connections, setConnections] = useState<Connection[]>([]);
  const [defaultJira, setDefaultJira] = useState<string>(
    project.default_jira_conn ?? "",
  );
  const [defaultSentry, setDefaultSentry] = useState<string>(
    project.default_sentry_conn ?? "",
  );
  const [submitting, setSubmitting] = useState(false);
  const [confirmingDelete, setConfirmingDelete] = useState(false);
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

  const jiraConnections = connections.filter((c) => c.config.type === "jira");
  const sentryConnections = connections.filter(
    (c) => c.config.type === "sentry",
  );

  // Build a payload of only the fields the user actually changed. The
  // backend treats absent fields as "don't change", so this keeps PATCH
  // requests minimal and avoids touching defaults the user didn't open.
  function buildPatch(): PatchProjectPayload {
    const patch: PatchProjectPayload = {};
    const trimmedName = name.trim();
    if (trimmedName !== project.name) patch.name = trimmedName;
    if (description !== project.description) patch.description = description;
    const upperGlyph = glyph.slice(0, 2).toUpperCase();
    if (upperGlyph !== project.glyph) patch.glyph = upperGlyph;
    if (color !== project.color) patch.color = color;
    if (defaultJira && defaultJira !== (project.default_jira_conn ?? "")) {
      patch.default_jira_conn = defaultJira;
    }
    if (defaultSentry && defaultSentry !== (project.default_sentry_conn ?? "")) {
      patch.default_sentry_conn = defaultSentry;
    }
    return patch;
  }

  const canSubmit =
    name.trim().length > 0 && glyph.length > 0 && !submitting;

  async function submit() {
    if (!canSubmit) return;
    setSubmitting(true);
    setError(null);
    const patch = buildPatch();
    if (Object.keys(patch).length === 0) {
      // Nothing changed — close without a roundtrip.
      onClose();
      return;
    }
    try {
      const updated = await patchProject(project.slug, patch);
      onSaved(updated);
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err));
      setSubmitting(false);
    }
  }

  async function confirmDelete() {
    setSubmitting(true);
    setError(null);
    try {
      await deleteProjectApi(project.slug);
      onDeleted();
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err));
      setSubmitting(false);
      setConfirmingDelete(false);
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
            <h3>Edit project</h3>
            <div className="sub">
              {confirmingDelete
                ? "Attached works are demoted to Loose; transcripts are preserved."
                : `${project.slug} · changes apply on save.`}
            </div>
          </div>
          <button className="btn-icon" onClick={onClose} aria-label="Close">
            ×
          </button>
        </div>

        {confirmingDelete ? (
          <div className="modal-bd">
            <p style={{ margin: 0 }}>
              Delete <strong>{project.name}</strong>?
            </p>
            <p className="hint" style={{ margin: 0 }}>
              Works currently in this project become Loose. The project
              folder under{" "}
              <span className="mono">~/Atelier/projects/{project.slug}/</span>{" "}
              is removed best-effort.
            </p>
            {error && <div className="form-error">{error}</div>}
          </div>
        ) : (
          <div className="modal-bd">
            <label className="field">
              <span className="label">Name</span>
              <input
                ref={nameRef}
                className="input"
                value={name}
                onChange={(e) => setName(e.target.value)}
              />
            </label>

            <div
              className="field"
              style={{ flexDirection: "row", alignItems: "center", gap: "0.75rem" }}
            >
              <span className="pc-glyph" aria-hidden="true">
                {glyph.slice(0, 2)}
              </span>
              <label className="field" style={{ flex: 1 }}>
                <span className="label">Glyph</span>
                <input
                  className="input"
                  value={glyph}
                  maxLength={2}
                  onChange={(e) => setGlyph(e.target.value.toUpperCase())}
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
                  <option value="">— keep current —</option>
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
                  <option value="">— keep current —</option>
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
        )}

        <div className="modal-ft">
          {!confirmingDelete && (
            <button
              className="btn-ghost-sm"
              style={{ marginRight: "auto", color: "var(--bad, #d05050)" }}
              onClick={() => {
                setError(null);
                setConfirmingDelete(true);
              }}
              disabled={submitting}
            >
              Delete project…
            </button>
          )}
          {confirmingDelete ? (
            <>
              <button
                className="btn"
                onClick={() => setConfirmingDelete(false)}
                disabled={submitting}
              >
                Cancel
              </button>
              <button
                className="btn primary"
                style={{ background: "var(--bad, #d05050)", borderColor: "transparent" }}
                disabled={submitting}
                onClick={confirmDelete}
              >
                {submitting ? "Deleting…" : "Delete"}
              </button>
            </>
          ) : (
            <>
              <button className="btn" onClick={onClose} disabled={submitting}>
                Cancel
              </button>
              <button
                className="btn primary"
                disabled={!canSubmit}
                onClick={submit}
              >
                {submitting ? "Saving…" : "Save changes"}
              </button>
            </>
          )}
        </div>
      </div>
    </div>
  );
}
