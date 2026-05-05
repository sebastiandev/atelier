import { useEffect, useMemo, useRef, useState } from "react";

import {
  type Connection,
  type ConnectionType,
  type ContextEntry,
  type CreateAgentPayload,
  type Persona,
  type ProviderDescriptor,
  PERSONAS,
  PERSONA_GLYPH,
  listConnections,
  listProviders,
} from "./api";
import { CONNECTION_FIELDS, CONNECTION_TYPES } from "./connectionFields";
import { ContextRow } from "./ContextRow";
import { SimpleContextRow, type SimpleContextType } from "./SimpleContextRow";

type Props = {
  workSlug: string;
  workName: string;
  onClose: () => void;
  onCreate: (payload: CreateAgentPayload) => Promise<void>;
};

const SIMPLE_TYPES: { id: SimpleContextType; label: string }[] = [
  { id: "text", label: "Text" },
  { id: "url", label: "URL" },
  { id: "file", label: "File" },
];

const CUSTOM_PERSONA_PLACEHOLDER: Persona = "developer";

export function NewAgentDialog({ workSlug, workName, onClose, onCreate }: Props) {
  const [providers, setProviders] = useState<ProviderDescriptor[] | null>(null);
  const [providersError, setProvidersError] = useState<string | null>(null);

  const [persona, setPersona] = useState<Persona | null>(null);
  const [customMode, setCustomMode] = useState(false);
  const [customRole, setCustomRole] = useState("");
  const [name, setName] = useState("");

  const [providerName, setProviderName] = useState<string | null>(null);
  const [model, setModel] = useState<string | null>(null);
  const [options, setOptions] = useState<Record<string, string>>({});

  const [contexts, setContexts] = useState<ContextEntry[]>([]);
  const [connections, setConnections] = useState<Connection[]>([]);

  const [submitting, setSubmitting] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const nameRef = useRef<HTMLInputElement>(null);

  useEffect(() => {
    listConnections()
      .then(setConnections)
      .catch(() => setConnections([]));
  }, []);

  useEffect(() => {
    listProviders()
      .then((p) => {
        setProviders(p);
        if (p.length > 0) {
          setProviderName(p[0].name);
          setModel(p[0].primary_field.default);
          setOptions(defaultsFor(p[0]));
        }
      })
      .catch((e) => setProvidersError(e instanceof Error ? e.message : String(e)));
  }, []);

  useEffect(() => {
    function onKey(e: KeyboardEvent) {
      if (e.key === "Escape") onClose();
    }
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [onClose]);

  useEffect(() => {
    nameRef.current?.focus();
  }, []);

  const provider = useMemo(
    () => providers?.find((p) => p.name === providerName) ?? null,
    [providers, providerName],
  );

  function changeProvider(next: string) {
    setProviderName(next);
    const p = providers?.find((x) => x.name === next);
    if (p) {
      setModel(p.primary_field.default);
      setOptions(defaultsFor(p));
    }
  }

  function pickPersona(id: Persona) {
    setPersona(id);
    setCustomMode(false);
    setCustomRole("");
    if (!name.trim()) {
      const meta = PERSONAS.find((p) => p.id === id);
      if (meta) setName(meta.name);
    }
  }

  function pickCustom() {
    setPersona(null);
    setCustomMode(true);
  }

  function addConnectionContext(type: ConnectionType) {
    setContexts((prev) => [...prev, { type, value: "", conn_id: null }]);
  }

  function addSimpleContext(type: SimpleContextType) {
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

  const canSubmit =
    !!provider &&
    !!model &&
    !!name.trim() &&
    (persona !== null || (customMode && customRole.trim())) &&
    !submitting;

  async function submit() {
    if (!canSubmit || !provider || !model) return;
    const personaId: Persona = persona ?? CUSTOM_PERSONA_PLACEHOLDER;
    const role =
      persona !== null
        ? PERSONAS.find((p) => p.id === persona)?.role ?? "agent"
        : customRole.trim();
    const payload: CreateAgentPayload = {
      name: name.trim(),
      persona: personaId,
      role,
      provider: provider.name,
      model,
    };
    if (Object.keys(options).length > 0) {
      payload.options = options;
    }
    const trimmedContexts = contexts.filter((c) => c.value.trim() || c.conn_id);
    if (trimmedContexts.length > 0) {
      payload.contexts = trimmedContexts;
    }
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
        className="modal modal-lg"
        onClick={(e) => e.stopPropagation()}
        role="dialog"
        aria-modal="true"
      >
        <div className="modal-hd">
          <div>
            <h3>Launch new agent</h3>
            <div className="sub">
              In <span className="mono">{workSlug}</span> · {workName}
            </div>
          </div>
          <button className="btn-icon" onClick={onClose} aria-label="Close">
            ×
          </button>
        </div>

        <div className="modal-bd">
          {providersError && <div className="form-error">{providersError}</div>}

          <div className="field">
            <span className="label">Pick a profile</span>
            <div className="persona-grid">
              {PERSONAS.map((p) => (
                <button
                  key={p.id}
                  type="button"
                  className={"persona-card" + (persona === p.id ? " active" : "")}
                  data-persona={p.id}
                  onClick={() => pickPersona(p.id)}
                >
                  <span className="pp-pip">{PERSONA_GLYPH[p.id]}</span>
                  <span className="pp-meta">
                    <span className="pp-name">{p.name}</span>
                    <span className="pp-role">{p.role}</span>
                  </span>
                </button>
              ))}
              <button
                type="button"
                className={"persona-card" + (customMode ? " active" : "")}
                style={{ borderStyle: "dashed" }}
                onClick={pickCustom}
              >
                <span className="pp-pip" style={{ background: "transparent" }}>
                  +
                </span>
                <span className="pp-meta">
                  <span className="pp-name">Custom role</span>
                  <span className="pp-role">Define a goal</span>
                </span>
              </button>
            </div>
          </div>

          {customMode && (
            <label className="field">
              <span className="label">Goal / role</span>
              <textarea
                className="textarea"
                rows={2}
                placeholder="e.g. Audit the auth flow for OWASP top 10. Stop at recommendations."
                value={customRole}
                onChange={(e) => setCustomRole(e.target.value)}
              />
            </label>
          )}

          <label className="field">
            <span className="label">Name</span>
            <input
              ref={nameRef}
              className="input"
              placeholder="e.g. Architect-01"
              value={name}
              onChange={(e) => setName(e.target.value)}
            />
          </label>

          {providers && (
            <>
              <div className="field">
                <span className="label">Provider</span>
                <div className="provider-row">
                  {providers.map((p) => (
                    <button
                      key={p.name}
                      type="button"
                      className={
                        "provider-card" + (providerName === p.name ? " active" : "")
                      }
                      onClick={() => changeProvider(p.name)}
                    >
                      <span className="pname">{p.label}</span>
                      <span className="pmodel">
                        {p.primary_field.values.length} {p.primary_field.label.toLowerCase()}
                        {p.primary_field.values.length === 1 ? "" : "s"}
                      </span>
                    </button>
                  ))}
                </div>
              </div>

              {provider && (
                <label className="field">
                  <span className="label">{provider.primary_field.label}</span>
                  <select
                    className="input"
                    value={model ?? provider.primary_field.default}
                    onChange={(e) => setModel(e.target.value)}
                  >
                    {provider.primary_field.values.map((v) => (
                      <option key={v} value={v}>
                        {v}
                      </option>
                    ))}
                  </select>
                </label>
              )}

              {provider && Object.keys(provider.options).length > 0 && (
                <details className="advanced-section">
                  <summary>Advanced</summary>
                  <div className="advanced-body">
                    {Object.entries(provider.options).map(([key, field]) => (
                      <label key={key} className="field">
                        <span className="label">{field.label}</span>
                        <select
                          className="input"
                          value={options[key] ?? field.default}
                          onChange={(e) =>
                            setOptions((prev) => ({ ...prev, [key]: e.target.value }))
                          }
                        >
                          {field.values.map((v) => (
                            <option key={v} value={v}>
                              {v}
                            </option>
                          ))}
                        </select>
                      </label>
                    ))}
                  </div>
                </details>
              )}
            </>
          )}

          <div className="field">
            <span className="label">Context</span>
            <span className="hint">
              Pointers the agent can read on demand. Connection-backed sources
              get full content in a later sprint.
            </span>
            {contexts.map((c, i) =>
              SIMPLE_TYPES.some((s) => s.id === c.type) ? (
                <SimpleContextRow
                  key={i}
                  context={c}
                  onChange={(next) => patchContext(i, next)}
                  onRemove={() => removeContext(i)}
                />
              ) : (
                <ContextRow
                  key={i}
                  context={c}
                  connections={connections}
                  onChange={(next) => patchContext(i, next)}
                  onRemove={() => removeContext(i)}
                  onConnectionSaved={upsertConnection}
                />
              ),
            )}
            <div className="add-context-row">
              <span className="hint">+ Add context</span>
              {SIMPLE_TYPES.map((s) => (
                <button
                  key={s.id}
                  type="button"
                  className="btn sm"
                  data-source={s.id}
                  onClick={() => addSimpleContext(s.id)}
                >
                  {s.label}
                </button>
              ))}
              {CONNECTION_TYPES.map((type) => (
                <button
                  key={type}
                  type="button"
                  className="btn sm"
                  data-source={type}
                  onClick={() => addConnectionContext(type)}
                >
                  {CONNECTION_FIELDS[type].label}
                </button>
              ))}
            </div>
          </div>

          {error && <div className="form-error">{error}</div>}
        </div>

        <div className="modal-ft">
          <button className="btn" onClick={onClose} disabled={submitting}>
            Cancel
          </button>
          <button className="btn primary" disabled={!canSubmit} onClick={submit}>
            {submitting ? "Launching…" : "Launch agent"}
          </button>
        </div>
      </div>
    </div>
  );
}

function defaultsFor(provider: ProviderDescriptor): Record<string, string> {
  const out: Record<string, string> = {};
  for (const [key, field] of Object.entries(provider.options)) {
    out[key] = field.default;
  }
  return out;
}
