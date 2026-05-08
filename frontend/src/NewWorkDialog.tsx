import { useEffect, useMemo, useRef, useState } from "react";

import {
  type Connection,
  type ConnectionType,
  type ContextEntry,
  type CreateWorkPayload,
  type ProjectSummary,
  listConnections,
} from "./api";
import { useConnectionDescriptors } from "./connectionDescriptors";
import { ContextRow } from "./ContextRow";

type Props = {
  onClose: () => void;
  onCreate: (payload: CreateWorkPayload) => Promise<void>;
  projects?: ProjectSummary[];
  // When opened from a project-scoped context, seed the picker. ``null``
  // is "Loose"; ``undefined`` leaves the picker free.
  presetProjectSlug?: string | null;
  // True when the project context is mandatory (e.g. opened from a
  // project detail screen). Disables the picker so the user can't
  // accidentally retarget the work elsewhere.
  lockProjectSlug?: boolean;
};

export function NewWorkDialog({
  onClose,
  onCreate,
  projects = [],
  presetProjectSlug,
  lockProjectSlug = false,
}: Props) {
  const [name, setName] = useState("");
  const [desc, setDesc] = useState("");
  const [contexts, setContexts] = useState<ContextEntry[]>([]);
  const [connections, setConnections] = useState<Connection[]>([]);
  const [projectSlug, setProjectSlug] = useState<string | null>(
    presetProjectSlug ?? null,
  );
  const [submitting, setSubmitting] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const nameRef = useRef<HTMLInputElement>(null);
  const { descriptors } = useConnectionDescriptors();
  const selectedProject = useMemo(
    () => projects.find((p) => p.slug === projectSlug) ?? null,
    [projects, projectSlug],
  );
  // Filter to types whose backend fetcher actually works — picking a
  // non-fetchable type would 422 at agent creation time. ``descriptors``
  // is null while loading; render no buttons until it arrives.
  const fetchableTypes = (descriptors ?? []).filter((d) => d.context_fetchable);

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

  // Seed context rows from the selected project's default Jira / Sentry
  // connection slugs. Track the last-seeded rows in a ref so a project
  // change drops the previous prefill before applying the new one — this
  // keeps the user's manually-added rows intact while letting them flip
  // projects without orphan defaults piling up. Trade-off: a manually-
  // edited prefill row is treated as still-seeded (we filter by type +
  // conn_id) and gets removed on switch; that's fine — switching projects
  // means the user wanted those defaults gone anyway.
  const lastSeededRef = useRef<ContextEntry[]>([]);
  const projectJira = selectedProject?.default_jira_conn ?? null;
  const projectSentry = selectedProject?.default_sentry_conn ?? null;
  useEffect(() => {
    const seeded: ContextEntry[] = [];
    if (projectJira) {
      seeded.push({ type: "jira", value: "", conn_id: projectJira });
    }
    if (projectSentry) {
      seeded.push({ type: "sentry", value: "", conn_id: projectSentry });
    }
    setContexts((prev) => {
      const previous = lastSeededRef.current;
      const without = prev.filter(
        (c) =>
          !previous.some(
            (p) => p.type === c.type && p.conn_id === c.conn_id,
          ),
      );
      return [...without, ...seeded];
    });
    lastSeededRef.current = seeded;
  }, [projectJira, projectSentry]);

  function addContext(type: ConnectionType) {
    setContexts((prev) => [...prev, { type, value: "", conn_id: null }]);
  }

  function patchContext(index: number, next: ContextEntry) {
    setContexts((prev) => prev.map((c, i) => (i === index ? next : c)));
  }

  function removeContext(index: number) {
    setContexts((prev) => prev.filter((_, i) => i !== index));
  }

  function upsertConnection(connection: Connection) {
    setConnections((prev) => {
      const without = prev.filter((c) => c.slug !== connection.slug);
      return [...without, connection];
    });
  }

  const canSubmit = name.trim() && !submitting;

  async function submit() {
    if (!canSubmit) return;
    const payload: CreateWorkPayload = {
      name: name.trim(),
      description: desc.trim(),
      contexts: contexts.filter((c) => c.value.trim() || c.conn_id),
      project_slug: projectSlug,
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
      <div
        className="modal"
        onClick={(e) => e.stopPropagation()}
        role="dialog"
        aria-modal="true"
        style={
          selectedProject
            ? { ["--proj-h" as string]: String(selectedProject.color) }
            : undefined
        }
      >
        <div className="modal-hd">
          <div>
            <h3>
              New work
              {selectedProject ? <span className="hint"> in {selectedProject.name}</span> : null}
            </h3>
            <div className="sub">
              Define the goal and any constraints. You'll spawn agents in the next view.
            </div>
          </div>
          <button className="btn-icon" onClick={onClose} aria-label="Close">
            ×
          </button>
        </div>

        <div className="modal-bd">
          {projects.length > 0 && (
            <div className="field">
              <span className="label">
                Project
                {lockProjectSlug && (
                  <span className="hint"> · locked to current scope</span>
                )}
              </span>
              <div className="proj-pick-row">
                <button
                  type="button"
                  className={"proj-pick-chip" + (projectSlug == null ? " active" : "")}
                  onClick={() => !lockProjectSlug && setProjectSlug(null)}
                  disabled={lockProjectSlug && projectSlug != null}
                >
                  Loose
                </button>
                {projects.map((p) => (
                  <button
                    key={p.slug}
                    type="button"
                    className={"proj-pick-chip" + (projectSlug === p.slug ? " active" : "")}
                    style={{ ["--proj-h" as string]: String(p.color) }}
                    onClick={() => !lockProjectSlug && setProjectSlug(p.slug)}
                    disabled={lockProjectSlug && projectSlug !== p.slug}
                  >
                    <span className="proj-pick-glyph mono">{p.glyph}</span>
                    {p.name}
                  </button>
                ))}
              </div>
              {selectedProject &&
                (selectedProject.default_jira_conn || selectedProject.default_sentry_conn) && (
                  <span className="hint">
                    {selectedProject.default_jira_conn && "Jira prefilled"}
                    {selectedProject.default_jira_conn && selectedProject.default_sentry_conn && " · "}
                    {selectedProject.default_sentry_conn && "Sentry prefilled"}{" "}
                    from project defaults
                  </span>
                )}
            </div>
          )}

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
            <span className="label">
              Brief description <span className="hint">· optional</span>
            </span>
            <textarea
              className="textarea"
              rows={3}
              placeholder="What does done look like? Any constraints?"
              value={desc}
              onChange={(e) => setDesc(e.target.value)}
            />
          </label>

          <div className="field">
            <span className="label">Context</span>
            {contexts.map((c, i) => (
              <ContextRow
                key={i}
                context={c}
                connections={connections}
                onChange={(next) => patchContext(i, next)}
                onRemove={() => removeContext(i)}
                onConnectionSaved={upsertConnection}
              />
            ))}
            <div className="add-context-row">
              <span className="hint">+ Add context</span>
              {fetchableTypes.map((d) => (
                <button
                  key={d.type}
                  type="button"
                  className="btn sm"
                  data-source={d.type}
                  onClick={() => addContext(d.type)}
                >
                  {d.label}
                </button>
              ))}
            </div>
          </div>

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
