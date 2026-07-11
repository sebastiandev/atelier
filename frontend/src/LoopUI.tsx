import { useEffect, useState } from "react";

import {
  type LoopContextKind,
  type LoopContextReference,
  type LoopDefinition,
  type LoopOutcome,
  type LoopStepDefinition,
  type LoopStepKind,
  type PlanArtifactDetail,
  deleteLoopDefinition,
  listLoopDefinitions,
  revealLoopDefinition,
  saveLoopDefinition,
} from "./api";
import {
  AgentIcon,
  BranchIcon,
  CheckIcon,
  ChevronRightIcon,
  CopyIcon,
  DocIcon,
  EditIcon,
  EyeIcon,
  FlaskIcon,
  FolderIcon,
  LoopIcon,
  PlayIcon,
  ReturnIcon,
  SearchIcon,
  ShieldIcon,
  SlidersIcon,
  TrashIcon,
  UserCheckIcon,
} from "./Icons";
import { MarkdownText } from "./MarkdownText";
import { modelPickerOptions } from "./providerDescriptors";
import { useProviderDescriptors } from "./providerDescriptors";

type EditorSeed = {
  definition: LoopDefinition;
  expectedRevision: string | null;
};

type LoopSelectorDialogProps = {
  workSlug: string;
  target: PlanArtifactDetail;
  onClose: () => void;
  onStart: (definition: LoopDefinition) => Promise<void>;
};

const CONTEXT_KINDS: Array<{
  kind: LoopContextKind;
  label: string;
  hint: string;
}> = [
  { kind: "target", label: "Target artifact", hint: "Planning artifact or objective" },
  { kind: "plan_index", label: "Plan index", hint: "Metadata-only plan file index" },
  { kind: "artifact_dependencies", label: "Dependencies", hint: "Accepted dependency summaries" },
  { kind: "workspace_diff", label: "Workspace diff", hint: "Current run-workspace diff" },
  { kind: "changed_files", label: "Changed files", hint: "Current changed-file references" },
  { kind: "previous_report", label: "Previous report", hint: "Structured output from a stage" },
  { kind: "files", label: "Files / globs", hint: "Repository-relative paths" },
  { kind: "folder", label: "Folder", hint: "Repository-relative folder" },
  { kind: "shared_context", label: "Shared context", hint: "Atelier shared-context reference" },
];

const OUTCOMES: Array<{ key: LoopOutcome; label: string }> = [
  { key: "pass", label: "pass" },
  { key: "changes_requested", label: "changes requested" },
  { key: "blocked_user", label: "blocked · user" },
  { key: "failed", label: "failed" },
];

export function LoopSelectorDialog({
  workSlug,
  target,
  onClose,
  onStart,
}: LoopSelectorDialogProps) {
  const [definitions, setDefinitions] = useState<LoopDefinition[] | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [query, setQuery] = useState("");
  const [selectedId, setSelectedId] = useState("atelier-reviewed");
  const [editor, setEditor] = useState<EditorSeed | null>(null);
  const [libraryOpen, setLibraryOpen] = useState(false);
  const [starting, setStarting] = useState(false);

  const refresh = () =>
    listLoopDefinitions(workSlug)
      .then((rows) => {
        setDefinitions(rows);
        setError(null);
        if (!rows.some((row) => row.id === selectedId && row.valid)) {
          setSelectedId(defaultLoop(rows)?.id ?? "");
        }
      })
      .catch((err) => setError(err instanceof Error ? err.message : String(err)));

  useEffect(() => {
    void refresh();
  }, [workSlug]);

  useEffect(() => {
    const onKey = (event: KeyboardEvent) => {
      if (event.key === "Escape" && !editor && !libraryOpen) onClose();
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [editor, libraryOpen, onClose]);

  const launchable = definitions?.filter((row) => row.valid) ?? [];
  const needle = query.trim().toLowerCase();
  const visible = needle
    ? launchable.filter((definition) =>
        [
          definition.name,
          definition.description,
          definition.scope,
          definition.stages.map((stage) => stage.name).join(" "),
        ]
          .join(" ")
          .toLowerCase()
          .includes(needle),
      )
    : launchable;
  const selected =
    launchable.find((definition) => definition.id === selectedId) ??
    defaultLoop(launchable);

  function editDefinition(definition: LoopDefinition, duplicate = false) {
    setEditor(editorSeed(definition, duplicate));
  }

  async function start(definition: LoopDefinition) {
    setStarting(true);
    setError(null);
    try {
      await onStart(definition);
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err));
      setStarting(false);
    }
  }

  if (editor) {
    return (
      <LoopEditorScreen
        workSlug={workSlug}
        seed={editor}
        onClose={() => setEditor(null)}
        onSaved={(saved) => {
          setEditor(null);
          setSelectedId(saved.id);
          void refresh();
        }}
      />
    );
  }

  if (libraryOpen) {
    return (
      <LoopLibraryScreen
        workSlug={workSlug}
        rootPath=".atelier/loops/"
        onClose={() => {
          setLibraryOpen(false);
          void refresh();
        }}
      />
    );
  }

  return (
    <div className="scrim loop-selector-scrim" onClick={onClose}>
      <div
        className="modal loop-modal"
        role="dialog"
        aria-modal="true"
        aria-labelledby="loop-selector-title"
        onClick={(event) => event.stopPropagation()}
      >
        <div className="modal-hd loop-modal-hd">
          <div>
            <h3 id="loop-selector-title">Work on {target.artifact.id.toUpperCase()} with a loop</h3>
            <div className="sub">Select the multi-stage loop this run will follow</div>
          </div>
          <button className="btn ghost icon sm" onClick={onClose} aria-label="Close">×</button>
        </div>
        <div className="modal-bd">
          <TargetRow target={target} />
          <div className="loop-selector-body">
            <div className="loop-section-label">
              <span>Choose a loop</span>
              <span>{visible.length} of {launchable.length}</span>
            </div>
            <label className={"loop-search" + (query ? " active" : "")}>
              <SearchIcon size={13} />
              <input
                value={query}
                onChange={(event) => setQuery(event.target.value)}
                placeholder="Filter loops by name, description, or stage…"
                autoFocus
              />
              {query && <button type="button" onClick={() => setQuery("")} aria-label="Clear search">×</button>}
            </label>
            {error && <div className="form-error">{error}</div>}
            {!definitions && !error && <div className="loop-loading">Loading loops…</div>}
            <div className="loop-selector-list">
              {visible.map((definition) => (
                <button
                  key={definition.id}
                  className={"loop-card loop-select-card" + (definition.id === selected?.id ? " active" : "")}
                  onClick={() => setSelectedId(definition.id)}
                >
                  <div className="loop-card-head">
                    <span className="loop-card-icon"><LoopIcon size={16} /></span>
                    <span className="loop-card-copy">
                      <strong>
                        <Highlight text={definition.name} query={needle} />
                        <ScopeBadge definition={definition} />
                        {definition.is_default && <em className="loop-default-pill">default</em>}
                      </strong>
                      <small><Highlight text={definition.description} query={needle} /></small>
                    </span>
                    {definition.id === selected?.id && <CheckIcon size={16} />}
                  </div>
                  {definition.id === selected?.id && <StageStrip definition={definition} subtle />}
                </button>
              ))}
              {definitions && visible.length === 0 && (
                <div className="loop-search-empty">
                  <SearchIcon size={18} />
                  <span>No loops match “{query}”.</span>
                  <button className="btn sm" onClick={() => setEditor(newLoopSeed())}>+ Create a loop</button>
                </div>
              )}
            </div>
            {selected && (
              <div className="loop-edit-actions">
                <button className="btn sm" onClick={() => editDefinition(selected)}>
                  <EditIcon size={11} /> {selected.scope === "builtin" ? "Edit (forks to repo)" : "Edit loop"}
                </button>
                <button className="btn sm" onClick={() => editDefinition(selected, true)}><CopyIcon size={11} /> Duplicate</button>
                <button className="btn sm" onClick={() => setEditor(newLoopSeed())}>+ Create loop</button>
                <button className="btn ghost sm" onClick={() => setLibraryOpen(true)}><LoopIcon size={11} /> Library</button>
              </div>
            )}
          </div>
        </div>
        <div className="modal-ft">
          <button className="btn" disabled={starting} onClick={onClose}>Cancel</button>
          <span className="spacer" />
          <button className="btn primary" disabled={!selected || starting} onClick={() => selected && void start(selected)}>
            <PlayIcon size={12} /> {starting ? "Starting…" : "Start run"}
          </button>
        </div>
      </div>
    </div>
  );
}

function TargetRow({ target }: { target: PlanArtifactDetail }) {
  const criteria = acceptanceCriteriaCount(target.content);
  return (
    <div className="loop-target" data-arti={target.artifact.kind}>
      <span className="loop-target-glyph">{artifactGlyph(target.artifact.kind)}</span>
      <span className="loop-target-copy">
        <strong>{target.artifact.title}</strong>
        <small>{target.artifact.id.toUpperCase()} · {criteria} acceptance {criteria === 1 ? "criterion" : "criteria"} · source-backed</small>
      </span>
      <span className="loop-ready"><CheckIcon size={10} /> Ready</span>
    </div>
  );
}

export function LoopLibraryScreen({
  workSlug,
  rootPath,
  onClose,
}: {
  workSlug: string;
  rootPath: string;
  onClose: () => void;
}) {
  const [definitions, setDefinitions] = useState<LoopDefinition[] | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [editor, setEditor] = useState<EditorSeed | null>(null);
  const [busyId, setBusyId] = useState<string | null>(null);

  const refresh = () =>
    listLoopDefinitions(workSlug)
      .then((rows) => {
        setDefinitions(rows);
        setError(null);
      })
      .catch((err) => setError(err instanceof Error ? err.message : String(err)));

  useEffect(() => {
    void refresh();
  }, [workSlug]);

  if (editor) {
    return (
      <LoopEditorScreen
        workSlug={workSlug}
        seed={editor}
        onClose={() => setEditor(null)}
        onSaved={() => {
          setEditor(null);
          void refresh();
        }}
      />
    );
  }

  const builtins = definitions?.filter((row) => row.scope === "builtin") ?? [];
  const repository = definitions?.filter((row) => row.scope === "repo") ?? [];
  const defaultBuiltin = defaultLoop(builtins);

  async function remove(definition: LoopDefinition) {
    if (!window.confirm(`Delete ${definition.name}? Historical runs keep their snapshot.`)) return;
    setBusyId(definition.id);
    try {
      await deleteLoopDefinition(workSlug, definition.id);
      await refresh();
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err));
    } finally {
      setBusyId(null);
    }
  }

  return (
    <div className="loop-fullscreen">
      <header className="loop-library-head">
        <button className="btn ghost icon sm" onClick={onClose} aria-label="Back">←</button>
        <span className="loop-library-icon"><LoopIcon size={17} /></span>
        <span>
          <strong>Loops</strong>
          <small>{workSlug} · {rootPath}</small>
        </span>
        <button className="btn sm" disabled={!defaultBuiltin} onClick={() => defaultBuiltin && setEditor(editorSeed(defaultBuiltin, true))}><CopyIcon size={11} /> From existing</button>
        <button className="btn primary sm" onClick={() => setEditor(newLoopSeed())}>+ Create loop</button>
      </header>
      <main className="loop-library-body themed-scrollbar">
        {error && <div className="form-error">{error}</div>}
        <LoopLibraryGroup
          label="Built-in"
          note="read-only · bundled"
          definitions={builtins}
          onEdit={(definition) => setEditor(editorSeed(definition))}
          onDuplicate={(definition) => setEditor(editorSeed(definition, true))}
        />
        <LoopLibraryGroup
          label="Repository"
          note=".atelier/loops/"
          definitions={repository}
          onEdit={(definition) => setEditor(editorSeed(definition))}
          onDuplicate={(definition) => setEditor(editorSeed(definition, true))}
          onDelete={(definition) => void remove(definition)}
          busyId={busyId}
        />
      </main>
    </div>
  );
}

function LoopLibraryGroup({
  label,
  note,
  definitions,
  onEdit,
  onDuplicate,
  onDelete,
  busyId,
}: {
  label: string;
  note: string;
  definitions: LoopDefinition[];
  onEdit: (definition: LoopDefinition) => void;
  onDuplicate: (definition: LoopDefinition) => void;
  onDelete?: (definition: LoopDefinition) => void;
  busyId?: string | null;
}) {
  return (
    <section className="loop-library-group">
      <div className="loop-library-group-head"><strong>{label}</strong><span>{definitions.length}</span><small>{note}</small></div>
      <div className="loop-library-grid">
        {definitions.map((definition) => (
          <article
            key={definition.id}
            className={"loop-card loop-library-card" + (definition.valid ? "" : " invalid")}
            onClick={() => onEdit(definition)}
          >
            <div className="loop-card-head">
              <span className="loop-card-icon"><LoopIcon size={16} /></span>
              <span className="loop-card-copy">
                <strong>{definition.name}<ScopeBadge definition={definition} />{definition.is_default && <em className="loop-default-pill">default</em>}</strong>
                <small>{definition.description}</small>
              </span>
              <span className="loop-card-actions" onClick={(event) => event.stopPropagation()}>
                <button className="btn icon sm" title={definition.scope === "builtin" ? "Duplicate to repository" : "Edit"} onClick={() => definition.scope === "builtin" ? onDuplicate(definition) : onEdit(definition)}>
                  {definition.scope === "builtin" ? <CopyIcon size={12} /> : <EditIcon size={12} />}
                </button>
                {definition.scope === "repo" && <button className="btn icon sm" title="Duplicate" onClick={() => onDuplicate(definition)}><CopyIcon size={12} /></button>}
                {onDelete && <button className="btn icon sm danger" disabled={busyId === definition.id} title="Delete" onClick={() => onDelete(definition)}><TrashIcon size={12} /></button>}
              </span>
            </div>
            <div className="loop-card-meta">
              <span><b>{definition.stages.length}</b> stages</span>
              <span>rev <b>{definition.revision || "—"}</b></span>
              {definition.stages.some((stage) => stage.agent?.permissions === "write") && <span>✎ writes workspace</span>}
              <span className={definition.valid ? "valid" : "invalid"}>{definition.valid ? "✓ valid" : "⚠ invalid"}</span>
            </div>
            <StageStrip definition={definition} subtle />
            {!definition.valid && <div className="loop-invalid-note">{definition.errors.join(" ")}</div>}
          </article>
        ))}
      </div>
    </section>
  );
}

function LoopEditorScreen({
  workSlug,
  seed,
  onClose,
  onSaved,
}: {
  workSlug: string;
  seed: EditorSeed;
  onClose: () => void;
  onSaved: (definition: LoopDefinition) => void;
}) {
  const [draft, setDraft] = useState(() => structuredClone(seed.definition));
  const [selectedId, setSelectedId] = useState(draft.stages[0]?.id ?? "");
  const [tab, setTab] = useState<"instructions" | "context" | "agent" | "outcome">("instructions");
  const [preview, setPreview] = useState(false);
  const [advanced, setAdvanced] = useState(false);
  const [addAt, setAddAt] = useState<number | null>(null);
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [dirty, setDirty] = useState(seed.expectedRevision === null);
  const selected = draft.stages.find((stage) => stage.id === selectedId) ?? null;

  function updateDraft(next: LoopDefinition) {
    setDraft(next);
    setDirty(true);
  }

  function patchStage(stepId: string, patch: Partial<LoopStepDefinition>) {
    updateDraft({
      ...draft,
      stages: draft.stages.map((stage) =>
        stage.id === stepId ? { ...stage, ...patch } : stage,
      ),
    });
  }

  function addStage(index: number, preset: StagePreset) {
    const stage = stageFromPreset(preset, draft.stages);
    const stages = [...draft.stages];
    const previous = stages[index - 1];
    const next = stages[index];
    if (previous && previous.kind !== "user_approval") {
      stages[index - 1] = {
        ...previous,
        transitions: { ...previous.transitions, pass: stage.id },
      };
    }
    if (stage.kind !== "user_approval") {
      stage.transitions.pass = next?.id ?? "approval";
    }
    stages.splice(index, 0, stage);
    updateDraft({ ...draft, stages });
    setSelectedId(stage.id);
    setTab("instructions");
    setAddAt(null);
  }

  function moveStage(index: number, delta: number) {
    const destination = index + delta;
    if (destination < 0 || destination >= draft.stages.length) return;
    const stages = [...draft.stages];
    const [stage] = stages.splice(index, 1);
    stages.splice(destination, 0, stage);
    updateDraft({ ...draft, stages });
  }

  function duplicateStage(stage: LoopStepDefinition) {
    const id = uniqueId(`${stage.id}-copy`, draft.stages.map((item) => item.id));
    const copy = structuredClone({ ...stage, id, name: `${stage.name} copy` });
    const index = draft.stages.findIndex((item) => item.id === stage.id);
    const stages = [...draft.stages];
    stages.splice(index + 1, 0, copy);
    updateDraft({ ...draft, stages });
    setSelectedId(id);
  }

  function deleteStage(stage: LoopStepDefinition) {
    const stages = draft.stages
      .filter((item) => item.id !== stage.id)
      .map((item) => ({
        ...item,
        transitions: Object.fromEntries(
          Object.entries(item.transitions).map(([key, value]) => [key, value === stage.id ? null : value]),
        ),
      }));
    updateDraft({ ...draft, stages });
    if (selectedId === stage.id) setSelectedId(stages[0]?.id ?? "");
  }

  async function save() {
    setSaving(true);
    setError(null);
    try {
      const saved = await saveLoopDefinition(workSlug, {
        id: draft.id,
        name: draft.name,
        description: draft.description,
        forked_from: draft.forked_from,
        stages: draft.stages,
        expected_revision: seed.expectedRevision,
      });
      setDirty(false);
      onSaved(saved);
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err));
    } finally {
      setSaving(false);
    }
  }

  return (
    <div className="loop-fullscreen loop-editor">
      <header className="loop-editor-head">
        <button className="btn ghost icon sm" onClick={onClose} aria-label="Back">←</button>
        <div className="loop-editor-title">
          <span className="loop-editor-name-row">
            <input value={draft.name} onChange={(event) => updateDraft({ ...draft, name: event.target.value })} aria-label="Loop name" />
            <button
              className="btn ghost icon sm"
              disabled={seed.expectedRevision === null}
              title={
                seed.expectedRevision === null
                  ? "Save before revealing this loop"
                  : `.atelier/loops/${draft.id}`
              }
              onClick={() =>
                void revealLoopDefinition(workSlug, draft.id).catch((err) =>
                  setError(err instanceof Error ? err.message : String(err)),
                )
              }
            >
              <FolderIcon size={13} />
            </button>
          </span>
          <span>
            {draft.stages.length} stages
            {draft.forked_from && <> · forked from {draft.forked_from}</>}
            {dirty ? <em>● unsaved changes</em> : <> · rev {draft.revision}</>}
          </span>
        </div>
        <label className="loop-id-field">id <input value={draft.id} disabled={seed.expectedRevision !== null} onChange={(event) => updateDraft({ ...draft, id: slugify(event.target.value) })} /></label>
        <button className="btn sm" onClick={onClose}>Close</button>
        <button className="btn primary sm" disabled={!dirty || saving || !draft.name.trim() || !draft.id} onClick={() => void save()}><CheckIcon size={11} /> {saving ? "Saving…" : "Save"}</button>
      </header>
      {error && <div className="loop-editor-error form-error">{error}</div>}
      <div className="loop-editor-columns">
        <main className="loop-timeline themed-scrollbar">
          <div className="loop-contract-note">
            <span>🔒</span>
            <span>The immutable execution, reporting, and safety contract is appended by Atelier. You author <b>instructions</b> and <b>context</b>, never provider prompts.</span>
          </div>
          <label className="loop-description-field">Description<input value={draft.description} onChange={(event) => updateDraft({ ...draft, description: event.target.value })} placeholder="What this loop is for" /></label>
          <AddStageSlot index={0} open={addAt === 0} onToggle={() => setAddAt(addAt === 0 ? null : 0)} onAdd={addStage} />
          <div className="loop-stage-list">
            {draft.stages.map((stage, index) => (
              <div key={stage.id}>
                <StageTimelineCard
                  stage={stage}
                  stages={draft.stages}
                  index={index}
                  selected={stage.id === selectedId}
                  last={index === draft.stages.length - 1}
                  onSelect={() => {
                    setSelectedId(stage.id);
                    setTab("instructions");
                  }}
                  onMove={(delta) => moveStage(index, delta)}
                  onDuplicate={() => duplicateStage(stage)}
                  onDelete={() => deleteStage(stage)}
                />
                <AddStageSlot index={index + 1} open={addAt === index + 1} onToggle={() => setAddAt(addAt === index + 1 ? null : index + 1)} onAdd={addStage} />
              </div>
            ))}
          </div>
        </main>
        {selected ? (
          <StageInspector
            stage={selected}
            stages={draft.stages}
            tab={tab}
            preview={preview}
            advanced={advanced}
            onTab={setTab}
            onPreview={setPreview}
            onAdvanced={() => setAdvanced((value) => !value)}
            onPatch={(patch) => patchStage(selected.id, patch)}
          />
        ) : <aside className="loop-inspector loop-inspector-empty">Select a stage to edit its properties.</aside>}
      </div>
    </div>
  );
}

function StageTimelineCard({
  stage,
  stages,
  index,
  selected,
  last,
  onSelect,
  onMove,
  onDuplicate,
  onDelete,
}: {
  stage: LoopStepDefinition;
  stages: LoopStepDefinition[];
  index: number;
  selected: boolean;
  last: boolean;
  onSelect: () => void;
  onMove: (delta: number) => void;
  onDuplicate: () => void;
  onDelete: () => void;
}) {
  const backlink = stages.find((item) => item.id === stage.transitions.changes_requested);
  return (
    <div className="loop-stage-row" data-persona={stagePersona(stage.kind)}>
      <span className="loop-stage-rail"><i>{stageIcon(stage)}</i>{!last && <b />}</span>
      <div>
        {backlink && <div className="loop-stage-backlink"><ReturnIcon size={11} /> changes requested → {backlink.name}</div>}
        <button className={"loop-stage-card" + (selected ? " selected" : "")} onClick={onSelect}>
          <span className="loop-stage-card-head"><strong>{stage.name}</strong><em>{stageKindLabel(stage.kind)}</em></span>
          <span className="loop-stage-card-meta">
            {stage.agent && <><i>{stage.agent.permissions === "write" ? "write" : "read-only"}</i><i>{stage.agent.session} session</i></>}
            {stage.context.length > 0 && <i>{stage.context.length} context</i>}
            {stage.kind === "user_approval" && <i>waits for you</i>}
          </span>
          <span className="loop-stage-card-actions" onClick={(event) => event.stopPropagation()}>
            <button className="btn icon sm" disabled={index === 0} onClick={() => onMove(-1)} title="Move up">↑</button>
            <button className="btn icon sm" disabled={last} onClick={() => onMove(1)} title="Move down">↓</button>
            <button className="btn icon sm" onClick={onDuplicate} title="Duplicate"><CopyIcon size={12} /></button>
            <button className="btn icon sm danger" onClick={onDelete} title="Delete"><TrashIcon size={12} /></button>
          </span>
        </button>
      </div>
    </div>
  );
}

type InspectorTab = "instructions" | "context" | "agent" | "outcome";

function StageInspector({
  stage,
  stages,
  tab,
  preview,
  advanced,
  onTab,
  onPreview,
  onAdvanced,
  onPatch,
}: {
  stage: LoopStepDefinition;
  stages: LoopStepDefinition[];
  tab: InspectorTab;
  preview: boolean;
  advanced: boolean;
  onTab: (tab: InspectorTab) => void;
  onPreview: (preview: boolean) => void;
  onAdvanced: () => void;
  onPatch: (patch: Partial<LoopStepDefinition>) => void;
}) {
  const approval = stage.kind === "user_approval";
  const check = stage.kind === "deterministic_check";
  const tabs: Array<{ id: InspectorTab; label: string }> = approval
    ? [{ id: "instructions", label: "Decision" }, { id: "context", label: "Context" }]
    : [
        { id: "instructions", label: check ? "Check" : "Instructions" },
        { id: "context", label: "Context" },
        { id: "agent", label: check ? "Check" : "Agent" },
        { id: "outcome", label: "Outcome" },
      ];
  return (
    <aside className="loop-inspector">
      <div className="loop-inspector-head" data-persona={stagePersona(stage.kind)}>
        <span>{stageIcon(stage)} {stageKindLabel(stage.kind)}</span>
        <input value={stage.name} onChange={(event) => onPatch({ name: event.target.value })} />
      </div>
      <div className="loop-inspector-tabs">
        {tabs.map((item) => <button key={item.id} className={tab === item.id ? "active" : ""} onClick={() => onTab(item.id)}>{item.label}</button>)}
      </div>
      <div className="loop-inspector-body themed-scrollbar">
        {tab === "instructions" && <InstructionsPanel stage={stage} preview={preview} onPreview={onPreview} onPatch={onPatch} />}
        {tab === "context" && <ContextPanel stage={stage} stages={stages} onPatch={onPatch} />}
        {tab === "agent" && <AgentPanel stage={stage} onPatch={onPatch} />}
        {tab === "outcome" && <OutcomePanel stage={stage} stages={stages} onPatch={onPatch} />}
        {!approval && (
          <>
            <button className={"loop-advanced-toggle" + (advanced ? " open" : "")} onClick={onAdvanced}><ChevronRightIcon size={11} /> Advanced</button>
            {advanced && <AdvancedPanel stage={stage} onPatch={onPatch} />}
          </>
        )}
      </div>
    </aside>
  );
}

function InstructionsPanel({ stage, preview, onPreview, onPatch }: { stage: LoopStepDefinition; preview: boolean; onPreview: (value: boolean) => void; onPatch: (patch: Partial<LoopStepDefinition>) => void }) {
  if (stage.kind === "user_approval") {
    return <div className="loop-inspector-note"><UserCheckIcon size={13} /> The run pauses here until you approve the result or request changes.</div>;
  }
  if (stage.kind === "deterministic_check") {
    return (
      <>
        <InspectorField label="Check adapter"><input value={stage.check_adapter ?? ""} onChange={(event) => onPatch({ check_adapter: event.target.value || null })} placeholder="e.g. command" /></InspectorField>
        <InspectorField label="Command"><textarea value={stage.check_command.join(" ")} onChange={(event) => onPatch({ check_command: event.target.value.trim().split(/\s+/).filter(Boolean) })} placeholder="npm test" /></InspectorField>
        <div className="loop-inspector-note"><FlaskIcon size={13} /> Runs an explicitly configured local check in the run workspace.</div>
      </>
    );
  }
  return (
    <InspectorField label="Instructions · Markdown" action={<span className="loop-md-toggle"><button className={!preview ? "active" : ""} onClick={() => onPreview(false)}>Write</button><button className={preview ? "active" : ""} onClick={() => onPreview(true)}>Preview</button></span>}>
      {preview ? <div className="loop-md-preview"><MarkdownText text={stage.instructions} /></div> : <textarea className="loop-md-editor" value={stage.instructions} onChange={(event) => onPatch({ instructions: event.target.value })} />}
      <div className="loop-inspector-note"><DocIcon size={13} /> Stored as <code>steps/{stage.id}.md</code> and referenced from loop.yaml.</div>
    </InspectorField>
  );
}

function ContextPanel({ stage, stages, onPatch }: { stage: LoopStepDefinition; stages: LoopStepDefinition[]; onPatch: (patch: Partial<LoopStepDefinition>) => void }) {
  const context = stage.context;
  function patch(index: number, next: LoopContextReference) {
    onPatch({ context: context.map((item, itemIndex) => itemIndex === index ? next : item) });
  }
  function add(kind: LoopContextKind) {
    onPatch({ context: [...context, { kind, required: false, paths: [], step: null, ref: null }] });
  }
  return (
    <>
      <InspectorField label="Context references" hint="references, not copies">
        <div className="loop-context-list">
          {context.map((item, index) => {
            const meta = CONTEXT_KINDS.find((row) => row.kind === item.kind)!;
            return (
              <div className="loop-context-row" key={`${item.kind}:${index}`}>
                <span className="loop-context-icon">{contextIcon(item.kind)}</span>
                <span className="loop-context-copy"><strong>{meta.label}</strong><small>{meta.hint}</small>
                  {(item.kind === "files" || item.kind === "folder") && <input value={item.paths.join(", ")} onChange={(event) => patch(index, { ...item, paths: event.target.value.split(",").map((value) => value.trim()).filter(Boolean) })} placeholder="docs/adr/*.md" />}
                  {item.kind === "previous_report" && <select value={item.step ?? ""} onChange={(event) => patch(index, { ...item, step: event.target.value || null })}><option value="">Choose stage…</option>{stages.filter((row) => row.id !== stage.id).map((row) => <option key={row.id} value={row.id}>{row.name}</option>)}</select>}
                  {item.kind === "shared_context" && <input value={item.ref ?? ""} onChange={(event) => patch(index, { ...item, ref: event.target.value || null })} placeholder="Shared context reference" />}
                </span>
                <button className={"loop-required-toggle" + (item.required ? " active" : "")} onClick={() => patch(index, { ...item, required: !item.required })}>{item.required ? "required" : "optional"}</button>
                <button className="btn ghost icon sm" onClick={() => onPatch({ context: context.filter((_, itemIndex) => itemIndex !== index) })}>×</button>
              </div>
            );
          })}
          {context.length === 0 && <div className="loop-inspector-note">No context references yet.</div>}
        </div>
      </InspectorField>
      <InspectorField label="Add context">
        <div className="loop-add-context">{CONTEXT_KINDS.map((item) => <button key={item.kind} onClick={() => add(item.kind)}>+ {item.label}</button>)}</div>
      </InspectorField>
      <div className="loop-inspector-note">🔒 Required context blocks the stage when unresolved. Paths cannot escape the working root.</div>
    </>
  );
}

function AgentPanel({ stage, onPatch }: { stage: LoopStepDefinition; onPatch: (patch: Partial<LoopStepDefinition>) => void }) {
  const { descriptors } = useProviderDescriptors();
  if (stage.kind === "deterministic_check") {
    return <div className="loop-inspector-note"><FlaskIcon size={13} /> Checks use no agent and run with a bounded timeout.</div>;
  }
  if (!stage.agent) return null;
  const provider = descriptors?.find((item) => item.name === stage.agent?.provider) ?? null;
  const models = provider ? modelPickerOptions(provider) : [];
  const agent = stage.agent;
  return (
    <>
      <InspectorField label="Provider & model" hint="inherit loop defaults">
        <div className="loop-inspector-grid">
          <select value={agent.provider ?? ""} onChange={(event) => onPatch({ agent: { ...agent, provider: event.target.value || null, model: null } })}><option value="">inherit provider</option>{descriptors?.map((item) => <option key={item.name} value={item.name}>{item.label}</option>)}</select>
          <select value={agent.model ?? ""} disabled={!provider} onChange={(event) => onPatch({ agent: { ...agent, model: event.target.value || null } })}><option value="">inherit model</option>{models.map((item) => <option key={item.value} value={item.value}>{item.label}</option>)}</select>
        </div>
      </InspectorField>
      <PostureRow label="Session policy" hint={agent.session === "reuse" ? "Resume the same agent after change requests" : "Fresh agent for independent judgment"}>
        <select value={agent.session} onChange={(event) => onPatch({ agent: { ...agent, session: event.target.value as "reuse" | "fresh" } })}><option value="reuse">reuse</option><option value="fresh">fresh</option></select>
      </PostureRow>
      <PostureRow label="Permissions" hint={agent.permissions === "write" ? "May modify the run workspace" : "Inspects but cannot mutate the workspace"}>
        <select value={agent.permissions} onChange={(event) => onPatch({ agent: { ...agent, permissions: event.target.value as "read" | "write" } })}><option value="write">write</option><option value="read">read-only</option></select>
      </PostureRow>
      <div className="loop-inspector-note">🔒 Review and security stages default to read-only.</div>
    </>
  );
}

function OutcomePanel({ stage, stages, onPatch }: { stage: LoopStepDefinition; stages: LoopStepDefinition[]; onPatch: (patch: Partial<LoopStepDefinition>) => void }) {
  return (
    <InspectorField label="Outcome transitions">
      <div className="loop-outcome-list">
        {OUTCOMES.map((outcome) => (
          <label key={outcome.key} className={`loop-outcome-row ${outcome.key}`}><span><i />{outcome.label}</span><ChevronRightIcon size={12} /><select value={stage.transitions[outcome.key] ?? ""} onChange={(event) => onPatch({ transitions: { ...stage.transitions, [outcome.key]: event.target.value || null } })}><option value="">—</option>{stages.filter((item) => item.id !== stage.id).map((item) => <option key={item.id} value={item.id}>{item.name}</option>)}<option value="pause">pause (user blocker)</option><option value="fail">fail run</option><option value="complete">complete run</option></select></label>
        ))}
      </div>
      <div className="loop-inspector-note"><BranchIcon size={13} /> Cycles require an explicit changes-requested edge and bounded retries.</div>
    </InspectorField>
  );
}

function AdvancedPanel({ stage, onPatch }: { stage: LoopStepDefinition; onPatch: (patch: Partial<LoopStepDefinition>) => void }) {
  return (
    <div className="loop-advanced-panel">
      <InspectorField label="Retry limit"><input type="number" min={1} max={20} value={stage.retry.max_attempts} onChange={(event) => onPatch({ retry: { ...stage.retry, max_attempts: Number(event.target.value) } })} /></InspectorField>
      <InspectorField label="Timeout · minutes"><input type="number" min={1} max={1440} value={stage.retry.timeout_minutes} onChange={(event) => onPatch({ retry: { ...stage.retry, timeout_minutes: Number(event.target.value) } })} /></InspectorField>
      <InspectorField label="Report contract"><select value={stage.report_contract} onChange={(event) => onPatch({ report_contract: event.target.value })}><option value="implementation">implementation report</option><option value="review">review report</option><option value="check">check report</option><option value="generic">generic report</option></select></InspectorField>
    </div>
  );
}

function InspectorField({ label, hint, action, children }: { label: string; hint?: string; action?: React.ReactNode; children: React.ReactNode }) {
  return <section className="loop-inspector-field"><header><strong>{label}</strong>{hint && <small>{hint}</small>}{action}</header>{children}</section>;
}

function PostureRow({ label, hint, children }: { label: string; hint: string; children: React.ReactNode }) {
  return <div className="loop-posture-row"><span><strong>{label}</strong><small>{hint}</small></span>{children}</div>;
}

type StagePreset = "implementation" | "validation" | "code-review" | "security-review" | "approval" | "custom";

const STAGE_PRESETS: Array<{ id: StagePreset; name: string; kind: LoopStepKind; hint: string }> = [
  { id: "implementation", name: "Implementation", kind: "agent_task", hint: "Writes code in the run workspace" },
  { id: "validation", name: "Validation / check", kind: "deterministic_check", hint: "Runs a configured local check" },
  { id: "code-review", name: "Code review", kind: "agent_review", hint: "Fresh read-only reviewer" },
  { id: "security-review", name: "Security review", kind: "agent_review", hint: "Independent security pass" },
  { id: "approval", name: "Human approval", kind: "user_approval", hint: "Waits for your decision" },
  { id: "custom", name: "Custom agent stage", kind: "agent_task", hint: "Custom instructions and posture" },
];

function AddStageSlot({ index, open, onToggle, onAdd }: { index: number; open: boolean; onToggle: () => void; onAdd: (index: number, preset: StagePreset) => void }) {
  return <div className="loop-add-stage"><button onClick={onToggle}>+ Add stage</button>{open && <div className="loop-add-menu">{STAGE_PRESETS.map((preset) => <button key={preset.id} data-persona={stagePersona(preset.kind)} onClick={() => onAdd(index, preset.id)}><i>{stageIcon({ id: preset.id, name: preset.name, kind: preset.kind } as LoopStepDefinition)}</i><span><strong>{preset.name}</strong><small>{preset.hint}</small></span></button>)}</div>}</div>;
}

function StageStrip({ definition, subtle = false }: { definition: LoopDefinition; subtle?: boolean }) {
  return <div className={"loop-stage-strip" + (subtle ? " subtle" : "")}>{definition.stages.map((stage, index) => <span key={stage.id} className="loop-stage-strip-unit"><span className="loop-stage-chip" data-persona={stagePersona(stage.kind)}><i>{stageIcon(stage)}</i><b>{stage.name}</b>{!subtle && stage.agent && <small>{stage.agent.permissions}</small>}</span>{index < definition.stages.length - 1 && <ChevronRightIcon size={11} />}</span>)}</div>;
}

function ScopeBadge({ definition }: { definition: LoopDefinition }) {
  return <em className={`loop-scope-badge ${definition.scope}`}>{definition.scope === "builtin" ? "built-in" : "repo"}</em>;
}

function Highlight({ text, query }: { text: string; query: string }) {
  if (!query) return text;
  const index = text.toLowerCase().indexOf(query);
  if (index < 0) return text;
  return <>{text.slice(0, index)}<mark>{text.slice(index, index + query.length)}</mark>{text.slice(index + query.length)}</>;
}

function defaultLoop(definitions: LoopDefinition[]): LoopDefinition | null {
  return definitions.find((row) => row.is_default && row.valid) ?? definitions.find((row) => row.valid) ?? null;
}

function editorSeed(definition: LoopDefinition, duplicate = false): EditorSeed {
  if (definition.scope === "repo" && !duplicate) {
    return { definition: structuredClone(definition), expectedRevision: definition.revision };
  }
  const suffix = definition.scope === "builtin" && !duplicate ? "repository" : "copy";
  return {
    definition: {
      ...structuredClone(definition),
      id: `${definition.id}-${suffix}`,
      name: `${definition.name}${suffix === "repository" ? " — Repository" : " copy"}`,
      scope: "repo",
      revision: "",
      is_default: false,
      forked_from: definition.id,
      errors: [],
    },
    expectedRevision: null,
  };
}

function newLoopSeed(): EditorSeed {
  const implementation = stageFromPreset("implementation", []);
  const approval = stageFromPreset("approval", [implementation]);
  implementation.transitions.pass = approval.id;
  return {
    definition: {
      id: "untitled-loop",
      name: "Untitled loop",
      description: "",
      scope: "repo",
      revision: "",
      valid: true,
      errors: [],
      is_default: false,
      forked_from: null,
      stages: [implementation, approval],
    },
    expectedRevision: null,
  };
}

function stageFromPreset(preset: StagePreset, existing: LoopStepDefinition[]): LoopStepDefinition {
  const meta = STAGE_PRESETS.find((item) => item.id === preset)!;
  const id = uniqueId(preset === "custom" ? "custom-stage" : preset, existing.map((stage) => stage.id));
  const review = meta.kind === "agent_review";
  const approval = meta.kind === "user_approval";
  const check = meta.kind === "deterministic_check";
  return {
    id,
    name: meta.name,
    kind: meta.kind,
    instructions: approval || check ? "" : presetInstructions(preset),
    context: approval ? [] : [{ kind: "target", required: true, paths: [], step: null, ref: null }],
    agent: approval || check ? null : { session: review ? "fresh" : "reuse", permissions: review ? "read" : "write", provider: null, model: null, effort: null },
    report_contract: review ? "review" : check ? "check" : "implementation",
    retry: { max_attempts: check ? 1 : 2, timeout_minutes: check ? 10 : 20 },
    transitions: approval ? {} : { pass: "approval", changes_requested: review ? "implementation" : null, blocked_user: "pause", failed: "fail" },
    check_adapter: check ? "command" : null,
    check_command: [],
  };
}

function presetInstructions(preset: StagePreset): string {
  if (preset === "code-review") return "Review the implementation diff against the target and its acceptance criteria.\n\nReturn pass only when the result is ready for human approval; otherwise return changes requested with actionable findings and file references.\n";
  if (preset === "security-review") return "Perform an independent, read-only security review of the run workspace and diff.\n\nCheck authentication, input validation, secrets, and data exposure. Cite every finding.\n";
  return "Complete this stage against the target and report changes, validation evidence, divergences, skipped scope, and blockers.\n";
}

function uniqueId(base: string, ids: string[]): string {
  if (!ids.includes(base)) return base;
  let number = 2;
  while (ids.includes(`${base}-${number}`)) number += 1;
  return `${base}-${number}`;
}

function stagePersona(kind: LoopStepKind): string {
  if (kind === "agent_task") return "developer";
  if (kind === "agent_review") return "ux";
  if (kind === "deterministic_check") return "product";
  return "writer";
}

function stageKindLabel(kind: LoopStepKind): string {
  if (kind === "agent_task") return "Agent task";
  if (kind === "agent_review") return "Agent review";
  if (kind === "deterministic_check") return "Check";
  return "Approval";
}

function stageIcon(stage: Pick<LoopStepDefinition, "id" | "name" | "kind">) {
  if (stage.id.includes("security") || stage.name.toLowerCase().includes("security")) return <ShieldIcon size={13} />;
  if (stage.kind === "agent_review") return <EyeIcon size={13} />;
  if (stage.kind === "deterministic_check") return <FlaskIcon size={13} />;
  if (stage.kind === "user_approval") return <UserCheckIcon size={13} />;
  return <AgentIcon size={13} />;
}

function contextIcon(kind: LoopContextKind) {
  if (kind === "workspace_diff" || kind === "artifact_dependencies") return <BranchIcon size={13} />;
  if (kind === "folder") return <FolderIcon size={13} />;
  if (kind === "previous_report") return <ReturnIcon size={13} />;
  if (kind === "target" || kind === "plan_index" || kind === "files" || kind === "changed_files") return <DocIcon size={13} />;
  return <SlidersIcon size={13} />;
}

function acceptanceCriteriaCount(markdown: string): number {
  const section = /#{1,6}\s+Acceptance Criteria\s*\n([\s\S]*?)(?=\n#{1,6}\s|$)/i.exec(markdown)?.[1] ?? "";
  return section.split(/\r?\n/).filter((line) => /^\s*[-*]\s+/.test(line)).length;
}

function artifactGlyph(kind: string): string {
  if (kind === "story") return "ST";
  if (kind === "bug") return "BG";
  if (kind === "spike") return "SP";
  if (kind === "hotfix") return "HF";
  return kind.slice(0, 2).toUpperCase();
}

function slugify(value: string): string {
  return value.toLowerCase().replace(/[^a-z0-9]+/g, "-").replace(/^-|-$/g, "");
}
