import { useEffect, useMemo, useRef, useState } from "react";

import {
  type CreateAgentPayload,
  type Persona,
  type ProviderDescriptor,
  PERSONAS,
  PERSONA_GLYPH,
  listProviders,
} from "./api";

type Props = {
  workSlug: string;
  workName: string;
  onClose: () => void;
  onCreate: (payload: CreateAgentPayload) => Promise<void>;
};

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

  const [submitting, setSubmitting] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const nameRef = useRef<HTMLInputElement>(null);

  useEffect(() => {
    listProviders()
      .then((p) => {
        setProviders(p);
        if (p.length > 0) {
          setProviderName(p[0].name);
          setModel(p[0].primary_field.default);
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
    if (p) setModel(p.primary_field.default);
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
            </>
          )}

          <div className="hint">
            Context attachments and worktree base-branch picker land in Sprint 3.
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
