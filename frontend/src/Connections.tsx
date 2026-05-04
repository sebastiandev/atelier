import { useEffect, useState } from "react";

import {
  type Connection,
  type ConnectionType,
  type NewConnectionPayload,
  type PatchConnectionPayload,
  createConnection,
  deleteConnection,
  listConnections,
  patchConnection,
  verifyConnection,
} from "./api";
import {
  CONNECTION_FIELDS,
  CONNECTION_TYPES,
  type ConnectionSchema,
} from "./connectionFields";
import { ThemeToggle } from "./ThemeToggle";
import { TweaksToggle } from "./TweaksPanel";

type Draft = Record<string, string>;
type VerifyState = "idle" | "verifying" | "ok" | "err";

export function Connections() {
  const [connections, setConnections] = useState<Connection[]>([]);
  const [loadError, setLoadError] = useState<string | null>(null);

  // Per-type new-form state. At most one section is open at a time, so a
  // single openType + draft pair is enough. `createdSlug` tracks the row
  // committed by Verify so the Save click doesn't double-POST.
  const [openType, setOpenType] = useState<ConnectionType | null>(null);
  const [newDraft, setNewDraft] = useState<Draft>({});
  const [newVerify, setNewVerify] = useState<VerifyState>("idle");
  const [newError, setNewError] = useState<string | null>(null);
  const [createdSlug, setCreatedSlug] = useState<string | null>(null);

  // Edit existing connection.
  const [editSlug, setEditSlug] = useState<string | null>(null);
  const [editDraft, setEditDraft] = useState<Draft>({});
  const [editVerify, setEditVerify] = useState<VerifyState>("idle");
  const [editError, setEditError] = useState<string | null>(null);
  const [revealed, setRevealed] = useState<Record<string, boolean>>({});

  async function refresh() {
    try {
      setConnections(await listConnections());
      setLoadError(null);
    } catch (err) {
      setLoadError(err instanceof Error ? err.message : String(err));
    }
  }

  useEffect(() => {
    refresh();
  }, []);

  function startNew(type: ConnectionType) {
    setOpenType(type);
    setNewDraft({});
    setNewVerify("idle");
    setNewError(null);
    setCreatedSlug(null);
    setEditSlug(null);
  }

  function cancelNew() {
    setOpenType(null);
    setNewDraft({});
    setNewVerify("idle");
    setNewError(null);
    setCreatedSlug(null);
  }

  function startEdit(connection: Connection) {
    setEditSlug(connection.slug);
    setEditDraft(toDraft(connection));
    setEditVerify(connection.verified ? "ok" : "idle");
    setEditError(null);
    setOpenType(null);
  }

  function cancelEdit() {
    setEditSlug(null);
    setEditDraft({});
    setEditVerify("idle");
    setEditError(null);
  }

  async function verifyNew() {
    if (!openType) return;
    setNewVerify("verifying");
    setNewError(null);
    try {
      // The server's verify reads the keychain, so the row has to exist
      // before we can verify. First Verify click commits via POST;
      // subsequent Verify clicks PATCH any draft edits onto the same
      // slug. This way "Verify → tweak → Verify → Save" doesn't pile up
      // duplicate rows.
      let slug = createdSlug;
      if (slug === null) {
        const created = await createConnection(toCreatePayload(openType, newDraft));
        slug = created.slug;
        setCreatedSlug(slug);
        setConnections((prev) => [
          ...prev.filter((c) => c.slug !== slug),
          created,
        ]);
      } else {
        const updated = await patchConnection(slug, toPatchPayload(newDraft));
        setConnections((prev) => prev.map((c) => (c.slug === slug ? updated : c)));
      }
      const result = await verifyConnection(slug);
      setConnections((prev) =>
        prev.map((c) => (c.slug === slug ? { ...c, verified: result.verified } : c)),
      );
      setNewVerify(result.verified ? "ok" : "err");
      setNewError(result.verified ? null : result.error ?? "verification failed");
    } catch (err) {
      setNewVerify("err");
      setNewError(err instanceof Error ? err.message : String(err));
    }
  }

  function saveNew() {
    // Verify already committed the row; Save just closes the form. The
    // primary button only enables when newVerify === "ok" so the form
    // never closes with an uncommitted/unverified row.
    cancelNew();
  }

  async function saveEdit() {
    if (!editSlug) return;
    try {
      const updated = await patchConnection(editSlug, toPatchPayload(editDraft));
      setConnections((prev) => prev.map((c) => (c.slug === editSlug ? updated : c)));
      cancelEdit();
    } catch (err) {
      setEditError(err instanceof Error ? err.message : String(err));
    }
  }

  async function reverify(slug: string) {
    setEditVerify("verifying");
    setEditError(null);
    try {
      // If the user typed a new token, save it first so the keychain has
      // the latest before verify. Empty token → leave existing.
      if (editDraft.token) {
        await patchConnection(slug, { token: editDraft.token });
      }
      const result = await verifyConnection(slug);
      setEditVerify(result.verified ? "ok" : "err");
      setEditError(result.error);
      setConnections((prev) =>
        prev.map((c) => (c.slug === slug ? { ...c, verified: result.verified } : c)),
      );
    } catch (err) {
      setEditVerify("err");
      setEditError(err instanceof Error ? err.message : String(err));
    }
  }

  async function disconnect(slug: string) {
    try {
      await deleteConnection(slug);
      setConnections((prev) => prev.filter((c) => c.slug !== slug));
      if (editSlug === slug) cancelEdit();
    } catch (err) {
      setEditError(err instanceof Error ? err.message : String(err));
    }
  }

  return (
    <div className="home">
      <header className="topbar">
        <a className="brand brand-link" href="/">
          <span className="brand-mark" /> Atelier
        </a>
        <span className="crumbs">
          <span className="sep">/</span>
          <a className="crumb-link" href="/">
            Workspace
          </a>
          <span className="sep">/</span>
          <span className="now">Connections</span>
        </span>
        <div className="spacer" />
        <TweaksToggle />
        <ThemeToggle />
      </header>

      <div className="home-hd">
        <div>
          <h1>Connections</h1>
          <p className="tagline">
            Source creds, saved once. Reused whenever an agent needs to pull a ticket,
            error, or trace.
          </p>
        </div>
      </div>

      {loadError && <div className="form-error">{loadError}</div>}

      <div className="conn-screen">
        {CONNECTION_TYPES.map((type) => {
          const schema = CONNECTION_FIELDS[type];
          const list = connections.filter((c) => c.type === type);
          return (
            <section key={type} className="conn-group" data-source={type}>
              <div className="conn-group-hd">
                <div className="conn-group-title">
                  <span className="ctx-type" data-source={type}>
                    <span className="mono">{schema.glyph}</span>
                    {schema.label}
                  </span>
                  <span className="count mono">{list.length}</span>
                </div>
                <button className="btn sm" onClick={() => startNew(type)}>
                  + New {schema.label}
                </button>
              </div>

              {list.length === 0 && openType !== type && (
                <div className="conn-empty">
                  No {schema.label} connection yet. Add one to let agents pull{" "}
                  {schema.label.toLowerCase()} context.
                </div>
              )}

              <div className="conn-list">
                {list.map((c) => {
                  const isEditing = editSlug === c.slug;
                  return (
                    <div
                      key={c.slug}
                      className={"conn-card" + (isEditing ? " editing" : "")}
                      data-source={type}
                    >
                      <button
                        type="button"
                        className="conn-card-hd"
                        onClick={() => (isEditing ? cancelEdit() : startEdit(c))}
                      >
                        <div className="conn-card-name">
                          <span className="conn-card-title">{c.name}</span>
                          {c.verified && <span className="verify-pill ok">✓ Verified</span>}
                        </div>
                        <div className="conn-card-meta mono">
                          {c.url && <span>{prettyHost(c.url)}</span>}
                          {c.org && <span>· {c.org}</span>}
                          {c.email && <span>· {c.email}</span>}
                          {c.env && <span>· env={c.env}</span>}
                          {c.team && <span>· team={c.team}</span>}
                        </div>
                        <div className="conn-card-aside">
                          {c.last_used && (
                            <span className="hint">Last used {formatRelative(c.last_used)}</span>
                          )}
                          <span aria-hidden>{isEditing ? "▾" : "▸"}</span>
                        </div>
                      </button>
                      {isEditing && (
                        <div className="conn-card-bd">
                          <ConnFieldGrid
                            schema={schema}
                            draft={editDraft}
                            onChange={(d) => {
                              setEditDraft(d);
                              setEditVerify("idle");
                            }}
                            revealed={!!revealed[c.slug]}
                            onToggleReveal={() =>
                              setRevealed((r) => ({ ...r, [c.slug]: !r[c.slug] }))
                            }
                          />
                          {editError && <div className="form-error">{editError}</div>}
                          <div className="conn-card-ft">
                            <button
                              type="button"
                              className="btn sm danger ghost"
                              onClick={() => disconnect(c.slug)}
                            >
                              Disconnect
                            </button>
                            <span className="spacer" />
                            <VerifyPill state={editVerify} />
                            <button
                              type="button"
                              className="btn sm"
                              disabled={editVerify === "verifying"}
                              onClick={() => reverify(c.slug)}
                            >
                              Re-verify
                            </button>
                            <button
                              type="button"
                              className="btn sm primary"
                              onClick={saveEdit}
                            >
                              Save
                            </button>
                          </div>
                        </div>
                      )}
                    </div>
                  );
                })}
              </div>

              {openType === type && (
                <div className="conn-card editing fresh" data-source={type}>
                  <div className="conn-card-hd-static">
                    <span className="conn-card-title">New {schema.label} connection</span>
                    <span className="hint">{schema.docs}</span>
                  </div>
                  <div className="conn-card-bd">
                    <ConnFieldGrid
                      schema={schema}
                      draft={newDraft}
                      onChange={(d) => {
                        setNewDraft(d);
                        setNewVerify("idle");
                      }}
                      revealed
                    />
                    {newError && <div className="form-error">{newError}</div>}
                    <div className="conn-card-ft">
                      <button type="button" className="btn sm ghost" onClick={cancelNew}>
                        Cancel
                      </button>
                      <span className="spacer" />
                      <VerifyPill state={newVerify} />
                      <button
                        type="button"
                        className="btn sm"
                        disabled={
                          newVerify === "verifying" ||
                          newVerify === "ok" ||
                          !canSubmit(schema, newDraft)
                        }
                        onClick={verifyNew}
                      >
                        {newVerify === "ok" ? "Connected" : "Verify"}
                      </button>
                      <button
                        type="button"
                        className="btn sm primary"
                        disabled={newVerify !== "ok"}
                        onClick={saveNew}
                      >
                        Save connection
                      </button>
                    </div>
                  </div>
                </div>
              )}
            </section>
          );
        })}
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

function ConnFieldGrid({
  schema,
  draft,
  onChange,
  revealed,
  onToggleReveal,
}: {
  schema: ConnectionSchema;
  draft: Draft;
  onChange: (next: Draft) => void;
  revealed: boolean;
  onToggleReveal?: () => void;
}) {
  return (
    <div className="conn-fields">
      {schema.fields.map((f) => (
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
          ) : f.secret ? (
            <span className="conn-secret-wrap">
              <input
                className="input sm"
                type={revealed ? "text" : "password"}
                placeholder={f.placeholder}
                value={draft[f.id] ?? ""}
                onChange={(e) => onChange({ ...draft, [f.id]: e.target.value })}
              />
              {onToggleReveal && (
                <button
                  type="button"
                  className="conn-reveal"
                  onClick={onToggleReveal}
                  title={revealed ? "Hide" : "Show"}
                >
                  {revealed ? "🙈" : "👁"}
                </button>
              )}
            </span>
          ) : (
            <input
              className="input sm"
              placeholder={f.placeholder}
              value={draft[f.id] ?? ""}
              onChange={(e) => onChange({ ...draft, [f.id]: e.target.value })}
            />
          )}
        </label>
      ))}
    </div>
  );
}

function canSubmit(schema: ConnectionSchema, draft: Draft): boolean {
  return schema.fields.every((f) => !f.required || (draft[f.id] ?? "").trim() !== "");
}

function toDraft(connection: Connection): Draft {
  const out: Draft = { name: connection.name };
  if (connection.url) out.url = connection.url;
  if (connection.org) out.org = connection.org;
  if (connection.region) out.region = connection.region;
  if (connection.env) out.env = connection.env;
  if (connection.team) out.team = connection.team;
  if (connection.email) out.email = connection.email;
  // token stays empty — server never returns it; user types a value to
  // rotate.
  return out;
}

function toCreatePayload(type: ConnectionType, draft: Draft): NewConnectionPayload {
  return {
    type,
    name: (draft.name ?? "").trim(),
    token: (draft.token ?? "").trim(),
    url: draft.url?.trim() || undefined,
    org: draft.org?.trim() || undefined,
    region: draft.region?.trim() || undefined,
    env: draft.env?.trim() || undefined,
    team: draft.team?.trim() || undefined,
    email: draft.email?.trim() || undefined,
  };
}

function toPatchPayload(draft: Draft): PatchConnectionPayload {
  const payload: PatchConnectionPayload = {};
  if (draft.name) payload.name = draft.name.trim();
  if (draft.token) payload.token = draft.token.trim();
  if (draft.url !== undefined) payload.url = draft.url.trim() || undefined;
  if (draft.org !== undefined) payload.org = draft.org.trim() || undefined;
  if (draft.region !== undefined) payload.region = draft.region.trim() || undefined;
  if (draft.env !== undefined) payload.env = draft.env.trim() || undefined;
  if (draft.team !== undefined) payload.team = draft.team.trim() || undefined;
  if (draft.email !== undefined) payload.email = draft.email.trim() || undefined;
  return payload;
}

function prettyHost(url: string): string {
  try {
    return new URL(url).host;
  } catch {
    return url;
  }
}

function formatRelative(iso: string): string {
  const d = new Date(iso);
  if (isNaN(d.getTime())) return iso;
  const diff = Date.now() - d.getTime();
  const min = Math.round(diff / 60_000);
  if (min < 1) return "just now";
  if (min < 60) return `${min}m ago`;
  const hr = Math.round(min / 60);
  if (hr < 24) return `${hr}h ago`;
  return d.toLocaleDateString(undefined, { month: "short", day: "numeric" });
}
