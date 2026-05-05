import { useEffect, useState } from "react";

import {
  type Connection,
  type ConnectionConfig,
  type ConnectionDescriptor,
  type ConnectionField,
  type ConnectionType,
  type ContextEntry,
  connectionType,
  createConnection,
  patchConnection,
  verifyConnection,
} from "./api";
import { useConnectionDescriptors } from "./connectionDescriptors";

type Draft = Record<string, string>;
type VerifyState = "idle" | "verifying" | "ok" | "err";

type Props = {
  context: ContextEntry;
  connections: Connection[];
  onChange: (next: ContextEntry) => void;
  onRemove: () => void;
  /**
   * Called once a new connection has been created + verified by this row.
   * Parent should append/upsert it into its `connections` list.
   */
  onConnectionSaved: (connection: Connection) => void;
};

const ITEM_PLACEHOLDER: Record<ConnectionType, string> = {
  jira: "Ticket key or URL (e.g. ENG-3421)",
  sentry: "Issue ID, event ID, or Sentry URL",
  honeycomb: "Trigger name or query URL",
};

/**
 * Context row rendered inside a dialog. Two modes:
 *
 *  - "pick": dropdown of existing connections of this type + per-context
 *    value input (ticket key / event ID / etc).
 *  - "new": inline NewConnectionForm with Verify → Save semantics that
 *    mirror the standalone Connections screen. On save, flips back to
 *    pick mode with the new connection auto-selected and tells the
 *    parent so the dropdown picks it up.
 *
 * If there are zero connections of the chosen type when the row mounts,
 * we jump straight to "new" — the headline interaction from the design
 * handoff. Cancel from "new" with no fallback connections AND nothing
 * yet committed removes the whole row.
 */
export function ContextRow({
  context,
  connections,
  onChange,
  onRemove,
  onConnectionSaved,
}: Props) {
  const type = context.type as ConnectionType;
  const { byType } = useConnectionDescriptors();
  const descriptor = byType?.[type];
  const conns = connections.filter((c) => connectionType(c) === type);

  const [mode, setMode] = useState<"pick" | "new">(conns.length === 0 ? "new" : "pick");
  const [draft, setDraft] = useState<Draft>({});
  const [verifyState, setVerifyState] = useState<VerifyState>("idle");
  const [verifyError, setVerifyError] = useState<string | null>(null);
  // Set by Verify: the connection just created/updated. Parent isn't told
  // about it until Save.
  const [pending, setPending] = useState<Connection | null>(null);

  // If the type's connection list goes from "has some" to "none" while
  // we're in pick mode, jump back to new (e.g. user deleted from
  // somewhere else — unlikely inside a dialog, but cheap to handle).
  useEffect(() => {
    if (mode === "pick" && conns.length === 0) setMode("new");
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [conns.length]);

  // Auto-commit the first connection when entering pick mode without
  // one selected. The dropdown *displays* conns[0] in that case, but
  // until the user explicitly picks (or the parent already supplies a
  // conn_id) the context object's conn_id stays null — and the backend
  // rejects connection-backed contexts with no conn_id at agent start.
  useEffect(() => {
    if (mode === "pick" && !context.conn_id && conns.length > 0) {
      onChange({ ...context, conn_id: conns[0].slug });
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [mode, conns.length, context.conn_id]);

  function updateDraft(next: Draft) {
    setDraft(next);
    setVerifyState("idle");
    setVerifyError(null);
  }

  async function verify() {
    if (!descriptor) return;
    setVerifyState("verifying");
    setVerifyError(null);
    const config = draftToConfig(type, descriptor, draft);
    const name = (draft.name ?? "").trim();
    const token = (draft.token ?? "").trim();
    try {
      const created = pending
        ? await patchConnection(pending.slug, { name, token, config })
        : await createConnection({ name, token, config });
      const result = await verifyConnection(created.slug);
      setPending({ ...created, verified: result.verified });
      setVerifyState(result.verified ? "ok" : "err");
      setVerifyError(result.verified ? null : result.error ?? "verification failed");
    } catch (err) {
      setVerifyState("err");
      setVerifyError(err instanceof Error ? err.message : String(err));
    }
  }

  function saveNew() {
    if (!pending) return;
    onConnectionSaved(pending);
    onChange({ ...context, conn_id: pending.slug });
    setMode("pick");
    setDraft({});
    setVerifyState("idle");
    setVerifyError(null);
    setPending(null);
  }

  function cancelNew() {
    if (conns.length === 0 && pending === null) {
      // No fallback selection and we never committed — drop the row.
      onRemove();
      return;
    }
    if (pending) {
      // Surface the half-committed row anyway; user can finish later
      // from the Connections page. Reflect into parent so the picker
      // shows it.
      onConnectionSaved(pending);
    }
    setMode("pick");
    setDraft({});
    setVerifyState("idle");
    setVerifyError(null);
    setPending(null);
  }

  const selectedConnId =
    context.conn_id ?? (conns.length > 0 ? conns[0].slug : null);

  // While descriptors load (one-time fetch on first mount in the tab),
  // render a tiny placeholder rather than crash on the missing schema.
  if (!descriptor) {
    return <div className="context-card" data-source={type}><span className="hint">Loading…</span></div>;
  }

  return (
    <div className="context-card" data-source={type}>
      <div className="context-card-hd">
        <div className="ctx-type" data-source={type}>
          <span className="mono">{descriptor.glyph}</span>
          {descriptor.label}
        </div>
        <button
          type="button"
          className="rm"
          onClick={onRemove}
          aria-label="Remove context"
          title="Remove"
        >
          ×
        </button>
      </div>

      {mode === "new" ? (
        <NewConnectionInline
          descriptor={descriptor}
          draft={draft}
          onChange={updateDraft}
          onVerify={verify}
          onSave={saveNew}
          onCancel={cancelNew}
          verifyState={verifyState}
          verifyError={verifyError}
          showCancelHint={conns.length === 0 && pending === null}
        />
      ) : (
        <div className="context-card-bd">
          <div className="conn-pick-row">
            <span className="conn-pick-lbl">via</span>
            <select
              className="input sm"
              value={selectedConnId ?? ""}
              onChange={(e) => {
                if (e.target.value === "__new__") {
                  setMode("new");
                  return;
                }
                onChange({ ...context, conn_id: e.target.value });
              }}
            >
              {conns.map((c) => (
                <option key={c.slug} value={c.slug}>
                  {c.name}
                  {c.verified ? " · ✓" : ""}
                </option>
              ))}
              <option value="__new__">+ New {descriptor.label} connection…</option>
            </select>
          </div>
          <input
            className="input sm"
            placeholder={ITEM_PLACEHOLDER[type]}
            value={context.value}
            onChange={(e) => onChange({ ...context, value: e.target.value })}
          />
        </div>
      )}
    </div>
  );
}

const NAME_FIELD: ConnectionField = {
  id: "name",
  label: "Connection name",
  placeholder: "Acme",
  required: true,
  secret: false,
  options: null,
};

const TOKEN_FIELD: ConnectionField = {
  id: "token",
  label: "API token",
  placeholder: null,
  required: true,
  secret: true,
  options: null,
};

function renderableFields(descriptor: ConnectionDescriptor): ConnectionField[] {
  return [NAME_FIELD, ...descriptor.config_fields, TOKEN_FIELD];
}

function draftToConfig(
  type: ConnectionType,
  descriptor: ConnectionDescriptor,
  draft: Draft,
): ConnectionConfig {
  const config: Record<string, unknown> = { type };
  for (const f of descriptor.config_fields) {
    const value = draft[f.id]?.trim();
    if (value !== undefined && value !== "") {
      config[f.id] = value;
    } else if (!f.required) {
      config[f.id] = null;
    }
  }
  return config as ConnectionConfig;
}

function NewConnectionInline({
  descriptor,
  draft,
  onChange,
  onVerify,
  onSave,
  onCancel,
  verifyState,
  verifyError,
  showCancelHint,
}: {
  descriptor: ConnectionDescriptor;
  draft: Draft;
  onChange: (next: Draft) => void;
  onVerify: () => void;
  onSave: () => void;
  onCancel: () => void;
  verifyState: VerifyState;
  verifyError: string | null;
  showCancelHint: boolean;
}) {
  const fields = renderableFields(descriptor);
  const canVerify = fields.every(
    (f) => !f.required || (draft[f.id] ?? "").trim() !== "",
  );
  return (
    <div className="conn-new">
      <div className="conn-new-hd">
        <div className="conn-new-title">New {descriptor.label} connection</div>
        <div className="hint">{descriptor.docs}</div>
      </div>
      <div className="conn-fields">
        {fields.map((f) => (
          <label key={f.id} className="conn-field">
            <span className="conn-field-lbl">
              {f.label}
              {f.required && <span className="conn-field-req"> *</span>}
            </span>
            {f.options ? (
              <select
                className="input sm"
                value={draft[f.id] ?? f.options[0]}
                onChange={(e) => onChange({ ...draft, [f.id]: e.target.value })}
              >
                {f.options.map((o) => (
                  <option key={o} value={o}>
                    {o}
                  </option>
                ))}
              </select>
            ) : (
              <input
                className="input sm"
                type={f.secret ? "password" : "text"}
                placeholder={f.placeholder ?? undefined}
                value={draft[f.id] ?? ""}
                onChange={(e) => onChange({ ...draft, [f.id]: e.target.value })}
              />
            )}
          </label>
        ))}
      </div>
      {verifyError && <div className="form-error">{verifyError}</div>}
      <div className="conn-new-ft">
        {showCancelHint && <span className="hint">Cancel removes the row.</span>}
        <span className="spacer" />
        <VerifyPill state={verifyState} />
        <button type="button" className="btn sm ghost" onClick={onCancel}>
          Cancel
        </button>
        <button
          type="button"
          className="btn sm"
          disabled={verifyState === "verifying" || !canVerify}
          onClick={onVerify}
        >
          {verifyState === "ok" ? "Re-verify" : "Verify"}
        </button>
        <button
          type="button"
          className="btn sm primary"
          disabled={verifyState !== "ok"}
          onClick={onSave}
        >
          Save
        </button>
      </div>
    </div>
  );
}

function VerifyPill({ state }: { state: VerifyState }) {
  if (state === "verifying") {
    return (
      <span className="verify-pill">
        <span className="spinner" /> Verifying…
      </span>
    );
  }
  if (state === "ok") return <span className="verify-pill ok">✓ Verified</span>;
  if (state === "err") return <span className="verify-pill err">✗ Couldn't verify</span>;
  return null;
}
