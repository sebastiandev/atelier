import {
  type KeyboardEvent as ReactKeyboardEvent,
  useEffect,
  useMemo,
  useRef,
  useState,
} from "react";

import { FolderPickerDialog } from "./FolderPickerDialog";

import {
  type Connection,
  type ConnectionType,
  type ContextEntry,
  type CreateAgentPayload,
  type Persona,
  type ProviderDescriptor,
  type ProviderField,
  PERSONAS,
  PERSONA_GLYPH,
  listConnections,
  listGitBranches,
  listProviders,
} from "./api";
import { useConnectionDescriptors } from "./connectionDescriptors";
import { ContextRow } from "./ContextRow";
import { SimpleContextRow, type SimpleContextType } from "./SimpleContextRow";
import {
  NO_FOLDERS,
  deriveFolderCandidates,
  useFolderRecentsStore,
} from "./state/folderRecents";

type Props = {
  workSlug: string;
  workName: string;
  onClose: () => void;
  onCreate: (payload: CreateAgentPayload) => Promise<void>;
  /** Set when the dialog is opened from the handoff flow (or any other
   *  flow that wants the new agent to inherit a sibling's working state).
   *  Adds a "Workdir" picker above the folder field; when "Continue from
   *  {source}" is selected, the create payload includes
   *  ``fork_from_agent`` so the supervisor calls ``ensure_forked``. */
  forkFromAgent?: { slug: string; name: string; folder: string };
  /** Pre-fills the optional-goal textarea. Used by the handoff flow to
   *  drop the freshly-generated handoff doc into the new agent's first
   *  context. The user can edit before submitting. */
  initialGoal?: string;
  /** Context rows to pre-seed before the dialog opens. Used by chat
   *  handoff so the chat summary/context.md enters the ordinary agent
   *  context pipeline. */
  initialContexts?: ContextEntry[];
};

const SIMPLE_TYPES: { id: SimpleContextType; label: string }[] = [
  { id: "text", label: "Text" },
  { id: "url", label: "URL" },
  { id: "file", label: "File" },
];

const CUSTOM_PERSONA_PLACEHOLDER: Persona = "developer";

export function NewAgentDialog({
  workSlug,
  workName,
  onClose,
  onCreate,
  forkFromAgent,
  initialGoal,
  initialContexts,
}: Props) {
  const [providers, setProviders] = useState<ProviderDescriptor[] | null>(null);
  const [providersError, setProvidersError] = useState<string | null>(null);

  const [persona, setPersona] = useState<Persona | null>(null);
  const [customMode, setCustomMode] = useState(false);
  const [customRole, setCustomRole] = useState("");
  const [name, setName] = useState("");

  const [providerName, setProviderName] = useState<string | null>(null);
  const [model, setModel] = useState<string | null>(null);
  const [options, setOptions] = useState<Record<string, string>>({});

  // "fork" only available when forkFromAgent is supplied; "fresh" is the
  // baseline and the only choice in the regular new-agent flow.
  const [workdirMode, setWorkdirMode] = useState<"fresh" | "fork">(
    forkFromAgent ? "fork" : "fresh",
  );

  // Folder + recents (per-work first, then global). Default to the first
  // candidate so the common "same folder as last time" case is one click.
  // When forking, default to the source agent's folder — the worktree
  // manager needs the same source repo to provision the forked checkout.
  const folderRecentsByWork = useFolderRecentsStore(
    (s) => s.byWork[workSlug] ?? NO_FOLDERS,
  );
  const folderRecentsGlobal = useFolderRecentsStore((s) => s.global);
  const rememberFolder = useFolderRecentsStore((s) => s.remember);
  const folderCandidates = useMemo(
    () => deriveFolderCandidates(folderRecentsByWork, folderRecentsGlobal),
    [folderRecentsByWork, folderRecentsGlobal],
  );
  const [folder, setFolder] = useState(
    () => forkFromAgent?.folder ?? folderCandidates[0] ?? "",
  );
  // Optional branch name for the agent's worktree. Blank = detached
  // HEAD from master (default), and the agent is told via system prompt to
  // ``git switch -c <name>`` before checking out elsewhere.
  const [branchName, setBranchName] = useState("");
  const [branchPickerOpen, setBranchPickerOpen] = useState(false);
  const [branchOptions, setBranchOptions] = useState<string[] | null>(null);
  const [branchesLoading, setBranchesLoading] = useState(false);

  const [goal, setGoal] = useState(initialGoal ?? "");
  const [contexts, setContexts] = useState<ContextEntry[]>(
    () => initialContexts ?? [],
  );
  const [connections, setConnections] = useState<Connection[]>([]);
  const { descriptors: connectionDescriptors } = useConnectionDescriptors();
  // Only types whose backend fetcher is wired show up as add-context
  // buttons — picking a non-fetchable type would 422 at agent creation.
  const fetchableTypes = (connectionDescriptors ?? []).filter(
    (d) => d.context_fetchable,
  );

  const [submitting, setSubmitting] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [pickerOpen, setPickerOpen] = useState(false);
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
          const defaultModel = p[0].primary_field.default;
          setProviderName(p[0].name);
          setModel(defaultModel);
          setOptions(defaultsFor(p[0], defaultModel));
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
      const defaultModel = p.primary_field.default;
      setModel(defaultModel);
      setOptions(defaultsFor(p, defaultModel));
    }
  }

  useEffect(() => {
    if (!provider || !model) return;
    setOptions((prev) => coerceOptionsForModel(provider, model, prev));
  }, [provider, model]);

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
    !!folder.trim() &&
    (persona !== null || (customMode && customRole.trim())) &&
    !submitting;

  async function openBranchPicker() {
    setBranchPickerOpen(true);
    if (branchOptions !== null) return; // already cached
    setBranchesLoading(true);
    try {
      const branches = await listGitBranches(folder.trim());
      setBranchOptions(branches);
    } catch {
      // Soft-fail: render an empty picker with the "no branches" hint.
      setBranchOptions([]);
    } finally {
      setBranchesLoading(false);
    }
  }

  async function submit() {
    if (!canSubmit || !provider || !model) return;
    const personaId: Persona = persona ?? CUSTOM_PERSONA_PLACEHOLDER;
    const role =
      persona !== null
        ? PERSONAS.find((p) => p.id === persona)?.role ?? "agent"
        : customRole.trim();
    const trimmedFolder = folder.trim();
    const payload: CreateAgentPayload = {
      name: name.trim(),
      persona: personaId,
      role,
      provider: provider.name,
      model,
      folder: trimmedFolder,
    };
    if (Object.keys(options).length > 0) {
      payload.options = options;
    }
    if (forkFromAgent && workdirMode === "fork") {
      payload.fork_from_agent = forkFromAgent.slug;
    }
    const trimmedBranch = branchName.trim();
    if (trimmedBranch) {
      payload.branch_name = trimmedBranch;
    }
    // Prepend the optional initial-goal textarea as a synthesized text
    // context so the agent's first-message points at it the same way
    // any user-added text context would. Same wire shape as if the user
    // had clicked "+ Text" and typed.
    const collected: ContextEntry[] = [];
    const trimmedGoal = goal.trim();
    if (trimmedGoal) {
      collected.push({ type: "text", value: trimmedGoal, conn_id: null });
    }
    collected.push(...contexts.filter((c) => c.value.trim() || c.conn_id));
    if (collected.length > 0) {
      payload.contexts = collected;
    }
    setSubmitting(true);
    setError(null);
    try {
      await onCreate(payload);
      // Only remember the folder after the create succeeds — a folder
      // that 422's at the backend (typo, missing parent) shouldn't end
      // up in the recents list and re-suggest itself next time.
      rememberFolder(workSlug, trimmedFolder);
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

          {forkFromAgent && (
            <div className="field">
              <span className="label">Workdir</span>
              <div className="workdir-pick">
                <label className="workdir-pick-opt">
                  <input
                    type="radio"
                    name="workdir-mode"
                    value="fork"
                    checked={workdirMode === "fork"}
                    onChange={() => setWorkdirMode("fork")}
                  />
                  <span>
                    <strong>Continue from {forkFromAgent.name}</strong>
                    <span className="hint">
                      {" "}
                      · forks {forkFromAgent.name}'s worktree at its current
                      HEAD with all uncommitted changes carried over (detached
                      HEAD, no auto-branch)
                    </span>
                  </span>
                </label>
                <label className="workdir-pick-opt">
                  <input
                    type="radio"
                    name="workdir-mode"
                    value="fresh"
                    checked={workdirMode === "fresh"}
                    onChange={() => setWorkdirMode("fresh")}
                  />
                  <span>
                    <strong>Fresh worktree</strong>
                    <span className="hint">
                      {" "}
                      · clean checkout from master; loses{" "}
                      {forkFromAgent.name}'s uncommitted work
                    </span>
                  </span>
                </label>
              </div>
            </div>
          )}

          <div className="field-row">
            <label className="field field-grow">
              <span className="label">Working folder</span>
              <div className="folder-input-row">
                <input
                  className="input"
                  list={`folder-recents-${workSlug}`}
                  placeholder="/Users/you/code/some-repo"
                  value={folder}
                  onChange={(e) => {
                    setFolder(e.target.value);
                    // Folder changed → cached branch list is stale.
                    setBranchOptions(null);
                  }}
                />
                <button
                  type="button"
                  className="btn-icon folder-input-pick"
                  onClick={() => setPickerOpen(true)}
                  aria-label="Browse for folder"
                  title="Browse"
                >
                  <FolderIcon />
                </button>
              </div>
              {folderCandidates.length > 0 && (
                <datalist id={`folder-recents-${workSlug}`}>
                  {folderCandidates.map((f) => (
                    <option key={f} value={f} />
                  ))}
                </datalist>
              )}
              <span className="hint">
                Created on start if missing. Git repos get their own
                worktree.
              </span>
            </label>

            <label className="field field-branch">
              <span className="label">
                Branch <span className="hint">(optional)</span>
              </span>
              <div className="folder-input-row branch-input-wrap">
                <input
                  className="input"
                  placeholder="detached HEAD"
                  value={branchName}
                  onChange={(e) => setBranchName(e.target.value)}
                  disabled={workdirMode === "fork"}
                />
                <button
                  type="button"
                  className="btn-icon folder-input-pick"
                  onClick={() => openBranchPicker()}
                  disabled={workdirMode === "fork" || !folder.trim()}
                  aria-label="Pick existing branch"
                  title={
                    folder.trim()
                      ? "Pick an existing branch"
                      : "Set a folder first"
                  }
                >
                  <BranchIcon />
                </button>
                {branchPickerOpen && (
                  <BranchPicker
                    loading={branchesLoading}
                    branches={branchOptions}
                    onPick={(b) => {
                      setBranchName(b);
                      setBranchPickerOpen(false);
                    }}
                    onClose={() => setBranchPickerOpen(false)}
                  />
                )}
              </div>
              <span className="hint">
                Blank = detached HEAD from master; agent names the branch later via{" "}
                <code>git switch -c</code>.
              </span>
            </label>
          </div>

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
                    {provider.primary_field.values.map((v, i) => (
                      <option key={v} value={v}>
                        {provider.primary_field.value_labels?.[i] ?? v}
                      </option>
                    ))}
                  </select>
                </label>
              )}

              {provider &&
                (Object.keys(provider.options).length > 0 ||
                  Object.keys(provider.text_options ?? {}).length > 0) && (
                  <details className="advanced-section">
                    <summary>Advanced</summary>
                    <div className="advanced-body">
                      {provider.advanced_intro && (
                        <p className="advanced-intro">{provider.advanced_intro}</p>
                      )}
                      {Object.entries(provider.options).map(([key, field]) => {
                        const effectiveField = optionFieldForModel(
                          provider,
                          model,
                          key,
                          field,
                        );
                        return (
                          <label key={key} className="field">
                            <span className="label">{effectiveField.label}</span>
                            <select
                              className="input"
                              value={options[key] ?? effectiveField.default}
                              onChange={(e) =>
                                setOptions((prev) => ({
                                  ...prev,
                                  [key]: e.target.value,
                                }))
                              }
                            >
                              {effectiveField.values.map((v) => (
                                <option key={v} value={v}>
                                  {optionLabel(field, v)}
                                </option>
                              ))}
                            </select>
                          </label>
                        );
                      })}
                      {Object.entries(provider.text_options ?? {}).map(([key, field]) => {
                        if (
                          field.visible_when &&
                          (options[field.visible_when[0]] ??
                            provider.options[field.visible_when[0]]?.default) !==
                            field.visible_when[1]
                        ) {
                          return null;
                        }
                        return (
                          <label key={key} className="field">
                            <span className="label">{field.label}</span>
                            <textarea
                              className="textarea sm"
                              rows={3}
                              placeholder={field.placeholder ?? ""}
                              value={options[key] ?? field.default}
                              onChange={(e) =>
                                setOptions((prev) => ({
                                  ...prev,
                                  [key]: e.target.value,
                                }))
                              }
                            />
                            {field.hint && <span className="hint">{field.hint}</span>}
                          </label>
                        );
                      })}
                    </div>
                  </details>
                )}
            </>
          )}

          <div className="field">
            <span className="label">
              Initial goal <span className="hint">(optional)</span>
            </span>
            <textarea
              className="textarea sm"
              rows={3}
              placeholder="What should this agent work on first? e.g. Investigate why /search returns 500 on queries with non-ASCII characters."
              value={goal}
              onChange={(e) => setGoal(e.target.value)}
            />
          </div>

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
              {fetchableTypes.map((d) => (
                <button
                  key={d.type}
                  type="button"
                  className="btn sm"
                  data-source={d.type}
                  onClick={() => addConnectionContext(d.type)}
                >
                  {d.label}
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
      {pickerOpen && (
        <FolderPickerDialog
          initialPath={folder.trim() || null}
          onCancel={() => setPickerOpen(false)}
          onPick={(picked) => {
            setFolder(picked);
            setPickerOpen(false);
          }}
        />
      )}
    </div>
  );
}

function FolderIcon() {
  return (
    <svg
      width="14"
      height="14"
      viewBox="0 0 12 12"
      fill="none"
      stroke="currentColor"
      strokeWidth="1.2"
      strokeLinecap="round"
      strokeLinejoin="round"
      aria-hidden
    >
      <path d="M1.5 3.5h3l1 1h5v5a1 1 0 0 1-1 1h-7a1 1 0 0 1-1-1v-6Z" />
    </svg>
  );
}

function BranchIcon() {
  // Two stems joining one trunk — reads as "branches" at 14px.
  return (
    <svg
      width="14"
      height="14"
      viewBox="0 0 12 12"
      fill="none"
      stroke="currentColor"
      strokeWidth="1.2"
      strokeLinecap="round"
      strokeLinejoin="round"
      aria-hidden
    >
      <circle cx="3" cy="2.5" r="1" />
      <circle cx="3" cy="9.5" r="1" />
      <circle cx="9" cy="6" r="1" />
      <path d="M3 3.5v5" />
      <path d="M3 6h2a3 3 0 0 0 3-3v0" />
    </svg>
  );
}

function BranchPicker({
  loading,
  branches,
  onPick,
  onClose,
}: {
  loading: boolean;
  branches: string[] | null;
  onPick: (branch: string) => void;
  onClose: () => void;
}) {
  const ref = useRef<HTMLDivElement>(null);
  const inputRef = useRef<HTMLInputElement>(null);
  const [query, setQuery] = useState("");

  // Click-outside dismiss — capture-phase listener so the click that
  // closes us doesn't also fire on a button it landed on.
  useEffect(() => {
    function onDoc(e: MouseEvent) {
      if (!ref.current) return;
      if (!ref.current.contains(e.target as Node)) onClose();
    }
    document.addEventListener("mousedown", onDoc);
    return () => document.removeEventListener("mousedown", onDoc);
  }, [onClose]);

  // Auto-focus the search field so the user can start typing
  // immediately after clicking the picker button.
  useEffect(() => {
    inputRef.current?.focus();
  }, []);

  const filtered = useMemo(() => {
    if (!branches) return null;
    const q = query.trim().toLowerCase();
    if (!q) return branches;
    return branches.filter((b) => b.toLowerCase().includes(q));
  }, [branches, query]);

  function onKeyDown(e: ReactKeyboardEvent<HTMLInputElement>) {
    if (e.key === "Escape") {
      e.preventDefault();
      onClose();
    } else if (e.key === "Enter") {
      // Enter picks if there's a unique match — saves a click in the
      // common "type a few chars to disambiguate" flow.
      if (filtered && filtered.length === 1) {
        e.preventDefault();
        onPick(filtered[0]);
      }
    }
  }

  return (
    <div className="branch-picker" ref={ref} role="listbox">
      <div className="branch-picker-search">
        <input
          ref={inputRef}
          className="input"
          placeholder="Filter branches…"
          value={query}
          onChange={(e) => setQuery(e.target.value)}
          onKeyDown={onKeyDown}
        />
      </div>
      <div className="branch-picker-list">
        {loading && <div className="branch-picker-empty">Loading…</div>}
        {!loading && branches !== null && branches.length === 0 && (
          <div className="branch-picker-empty">
            No branches found (not a git repo, or empty repo).
          </div>
        )}
        {!loading &&
          filtered !== null &&
          branches !== null &&
          branches.length > 0 &&
          filtered.length === 0 && (
            <div className="branch-picker-empty">
              No matches for &ldquo;{query}&rdquo;.
            </div>
          )}
        {!loading &&
          filtered !== null &&
          filtered.length > 0 &&
          filtered.map((b) => (
            <button
              key={b}
              type="button"
              className="branch-picker-opt"
              onClick={() => onPick(b)}
              role="option"
            >
              {b}
            </button>
          ))}
      </div>
    </div>
  );
}

function defaultsFor(
  provider: ProviderDescriptor,
  model: string | null = provider.primary_field.default,
): Record<string, string> {
  const out: Record<string, string> = {};
  for (const [key, field] of Object.entries(provider.options)) {
    out[key] = optionFieldForModel(provider, model, key, field).default;
  }
  for (const [key, field] of Object.entries(provider.text_options ?? {})) {
    out[key] = field.default;
  }
  return out;
}

function coerceOptionsForModel(
  provider: ProviderDescriptor,
  model: string,
  current: Record<string, string>,
): Record<string, string> {
  let changed = false;
  const next = { ...current };
  for (const [key, field] of Object.entries(provider.options)) {
    const effectiveField = optionFieldForModel(provider, model, key, field);
    const value = next[key] ?? effectiveField.default;
    if (!effectiveField.values.includes(value)) {
      next[key] = effectiveField.default;
      changed = true;
    } else if (next[key] === undefined) {
      next[key] = value;
      changed = true;
    }
  }
  return changed ? next : current;
}

function optionFieldForModel(
  provider: ProviderDescriptor,
  model: string | null,
  key: string,
  field: ProviderField,
): ProviderField {
  if (key !== "thinking_effort" || !model) return field;
  const meta = provider.model_meta?.[model];
  const values = meta?.effort_values?.filter(Boolean);
  if (!values || values.length === 0) return field;
  const defaultValue =
    meta?.effort_default && values.includes(meta.effort_default)
      ? meta.effort_default
      : values[0];
  return {
    ...field,
    values,
    default: defaultValue,
  };
}

function optionLabel(field: ProviderField, value: string): string {
  const idx = field.values.indexOf(value);
  return idx >= 0 ? field.value_labels?.[idx] ?? value : value;
}
