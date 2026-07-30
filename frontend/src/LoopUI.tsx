import { type CSSProperties, useEffect, useState } from "react";

import {
  type LoopContextKind,
  type LoopContextReference,
  type LoopDefinition,
  type LoopOutcome,
  type LoopStepDefinition,
  type LoopStepKind,
  type PrStageConfig,
  type StageDefinition,
  type StageOverrides,
  deleteLoopDefinition,
  deleteStageDefinition,
  listLoopDefinitions,
  listAvailableStageDefinitions,
  revealLoopDefinition,
  revealStageDefinition,
  saveLoopDefinition,
  saveStageDefinition,
} from "./api";
import { CommandPrefixEditor } from "./CommandPrefixEditor";
import { FolderPickerDialog } from "./FolderPickerDialog";
import {
  AlertIcon,
  BranchIcon,
  CheckIcon,
  ChevronRightIcon,
  CopyIcon,
  DocIcon,
  EditIcon,
  EyeIcon,
  FlaskIcon,
  FolderIcon,
  LockIcon,
  LoopIcon,
  PersonIcon,
  ReturnIcon,
  SearchIcon,
  ShieldIcon,
  SlidersIcon,
  TrashIcon,
  UserCheckIcon,
} from "./Icons";
import { MarkdownText } from "./MarkdownText";
import { PaneResizeHandle } from "./PaneResizeHandle";
import { ShellTopbar } from "./ShellTopbar";
import {
  modelPickerOptions,
  optionLabel,
  providerEffortOption,
  providerFastOption,
  useProviderDescriptors,
} from "./providerDescriptors";
import {
  LOOP_INSPECTOR_MAX,
  LOOP_INSPECTOR_MIN,
  useLayoutStore,
} from "./state/layout";

type EditorSeed = {
  definition: LoopDefinition;
  expectedRevision: string | null;
};

type StageEditorSeed = {
  definition: StageDefinition;
  expectedRevision: string | null;
  source: StageDefinition | null;
};

type LoopSaveScope = "library" | "work";

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
  { kind: "note", label: "Note", hint: "Inline context included in every run" },
  { kind: "shared_context", label: "Shared context", hint: "Atelier shared-context reference" },
];

const BUNDLED_CONTEXT_KINDS = CONTEXT_KINDS.filter((item) =>
  item.kind === "files" || item.kind === "folder" || item.kind === "note" || item.kind === "shared_context",
);
const RUNTIME_CONTEXT_KINDS = CONTEXT_KINDS.filter((item) =>
  !BUNDLED_CONTEXT_KINDS.some((bundled) => bundled.kind === item.kind),
);

const OUTCOMES: Array<{ key: LoopOutcome; label: string }> = [
  { key: "pass", label: "pass" },
  { key: "changes_requested", label: "changes requested" },
  { key: "blocked_user", label: "blocked · user" },
  { key: "failed", label: "failed" },
];

/** Search + choose a loop, inline. Lives in the run-setup flow so choosing
 *  a loop is a section of the form rather than a modal detour: the sections
 *  below it reconfigure from whatever is selected. */
export function LoopPicker({
  definitions,
  selectedId,
  onSelect,
  onCreate,
}: {
  definitions: LoopDefinition[] | null;
  selectedId: string | null;
  onSelect: (definition: LoopDefinition) => void;
  onCreate?: () => void;
}) {
  const [query, setQuery] = useState("");
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
  return (
    <div className="loop-picker">
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
        />
        {query && <button type="button" onClick={() => setQuery("")} aria-label="Clear search">×</button>}
      </label>
      {!definitions && <div className="loop-loading">Loading loops…</div>}
      <div className="loop-selector-list">
        {visible.map((definition) => (
          <LoopDefinitionChoice
            key={definition.id}
            definition={definition}
            query={needle}
            selected={definition.id === selectedId}
            onSelect={() => onSelect(definition)}
          />
        ))}
        {definitions && visible.length === 0 && (
          <div className="loop-search-empty">
            <SearchIcon size={18} />
            <span>No loops match “{query}”.</span>
            {onCreate && <button className="btn sm" onClick={onCreate}>+ Create a loop</button>}
          </div>
        )}
      </div>
    </div>
  );
}



export function LoopDefinitionChoice({
  compact = false,
  definition,
  onSelect,
  query = "",
  selected,
}: {
  compact?: boolean;
  definition: LoopDefinition;
  onSelect: () => void;
  query?: string;
  selected: boolean;
}) {
  return (
    <button
      type="button"
      className={
        "loop-card loop-select-card" +
        (compact ? " compact" : "") +
        (selected ? " active" : "")
      }
      onClick={onSelect}
    >
      <div className="loop-card-head">
        <span className="loop-card-icon"><LoopIcon size={compact ? 13 : 16} /></span>
        <span className="loop-card-copy">
          <strong>
            <Highlight text={definition.name} query={query} />
            <ScopeBadge definition={definition} />
            {!compact && definition.is_default && (
              <em className="loop-default-pill">default</em>
            )}
          </strong>
          <small><Highlight text={definition.description} query={query} /></small>
        </span>
        {!compact && selected && <CheckIcon size={16} />}
      </div>
      {!compact && selected && <StageStrip definition={definition} subtle />}
    </button>
  );
}

export function LoopStructureEditor({
  workSlug,
  rootPath,
  definition,
  onClose,
  onSaved,
}: {
  workSlug: string;
  rootPath?: string | null;
  definition?: LoopDefinition;
  onClose: () => void;
  onSaved: (definition: LoopDefinition) => void;
}) {
  return (
    <LoopEditorScreen
      workSlug={workSlug}
      rootPath={rootPath}
      saveScope="work"
      seed={definition ? editorSeed(definition, false, "work") : newLoopSeed("work")}
      onClose={onClose}
      onSaved={onSaved}
    />
  );
}

export function LoopLibraryScreen({
  workSlug,
  rootPath,
  onClose,
  embedded = false,
}: {
  workSlug: string | null;
  rootPath?: string | null;
  onClose?: () => void;
  embedded?: boolean;
}) {
  const [definitions, setDefinitions] = useState<LoopDefinition[] | null>(null);
  const [stageDefinitions, setStageDefinitions] = useState<StageDefinition[] | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [editor, setEditor] = useState<EditorSeed | null>(null);
  const [stageEditor, setStageEditor] = useState<StageEditorSeed | null>(null);
  const [tab, setTab] = useState<"loops" | "stages">("loops");
  const [busyId, setBusyId] = useState<string | null>(null);

  const refresh = () =>
    Promise.all([
      listLoopDefinitions(workSlug, rootPath),
      listAvailableStageDefinitions(rootPath),
    ])
      .then(([loops, stages]) => {
        setDefinitions(loops);
        setStageDefinitions(stages);
        setError(null);
      })
      .catch((err) => setError(err instanceof Error ? err.message : String(err)));

  useEffect(() => {
    void refresh();
  }, [rootPath, workSlug]);

  if (editor) {
    return (
      <LoopEditorScreen
        workSlug={workSlug}
        rootPath={rootPath}
        saveScope={editor.definition.scope === "work" ? "work" : "library"}
        seed={editor}
        onClose={() => setEditor(null)}
        onSaved={() => {
          setEditor(null);
          void refresh();
        }}
      />
    );
  }

  if (stageEditor) {
    return (
      <StageEditorScreen
        rootPath={stageDefinitionRoot(stageEditor.definition, rootPath)}
        seed={stageEditor}
        onClose={() => setStageEditor(null)}
        onSaved={() => {
          setStageEditor(null);
          void refresh();
        }}
      />
    );
  }

  const builtins = definitions?.filter((row) => row.scope === "builtin") ?? [];
  const repository = definitions?.filter((row) => row.scope === "library") ?? [];
  const work = definitions?.filter((row) => row.scope === "work") ?? [];
  const defaultBuiltin = defaultLoop(builtins);
  const builtinStages = stageDefinitions?.filter((row) => row.scope === "builtin") ?? [];
  const repositoryStages = stageDefinitions?.filter((row) => row.scope === "library") ?? [];

  async function remove(definition: LoopDefinition) {
    if (!window.confirm(`Delete ${definition.name}? Historical runs keep their snapshot.`)) return;
    setBusyId(definition.id);
    try {
      await deleteLoopDefinition(workSlug, definition.id, rootPath, definition.scope);
      await refresh();
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err));
    } finally {
      setBusyId(null);
    }
  }

  async function removeStage(definition: StageDefinition) {
    const usage = definition.used_by.length
      ? ` It is linked by ${definition.used_by.length} loop${definition.used_by.length === 1 ? "" : "s"}.`
      : "";
    if (!window.confirm(`Delete ${definition.name}?${usage}`)) return;
    setBusyId(definition.id);
    try {
      await deleteStageDefinition(definition.id, stageDefinitionRoot(definition, rootPath));
      await refresh();
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err));
    } finally {
      setBusyId(null);
    }
  }

  const primaryAction = tab === "loops" ? (
    <>
      <button className="btn sm" disabled={!defaultBuiltin} onClick={() => defaultBuiltin && setEditor(editorSeed(defaultBuiltin, true, "library"))}><CopyIcon size={11} /> From existing</button>
      <button className="btn primary sm" onClick={() => setEditor(newLoopSeed("library"))}>+ Create loop</button>
    </>
  ) : <button className="btn primary sm" onClick={() => setStageEditor(newStageSeed())}>+ New stage</button>;

  return (
    <div className={"loop-fullscreen" + (embedded ? " embedded" : " has-topbar")}>
      {!embedded && (
        <ShellTopbar
          crumbs={workSlug
            ? [
                { href: `/works/${workSlug}`, label: workSlug },
                ...(onClose ? [{ onClick: onClose, label: "loop" }] : []),
                { label: "loops" },
              ]
            : [{ href: "/settings", label: "settings" }, { label: "loops" }]}
          view={{
            inline: true,
            title: <span className="loop-library-root">{rootPath ?? "Atelier library"}</span>,
          }}
        />
      )}
      {embedded && (
        <header className="loop-library-head embedded">
          <span className="loop-library-title">
            <strong>{tab === "loops" ? "Loops" : "Stages"}</strong>
            <small>{tab === "loops" ? <>Reusable multi-stage loops. Library loops live in <code>~/Atelier/loops/</code>.</> : <>Reusable stage definitions live in <code>~/Atelier/stages/</code>.</>}</small>
          </span>
          {primaryAction}
        </header>
      )}
      <main className="loop-library-body themed-scrollbar">
        {error && <div className="form-error">{error}</div>}
        <div className="loop-library-collection-bar">
          <div className="loop-library-tabs" role="tablist" aria-label="Loop library">
            <button role="tab" aria-selected={tab === "loops"} className={tab === "loops" ? "active" : ""} onClick={() => setTab("loops")}><LoopIcon size={12} /> Loops <span>{definitions?.length ?? 0}</span></button>
            <button role="tab" aria-selected={tab === "stages"} className={tab === "stages" ? "active" : ""} onClick={() => setTab("stages")}>◇ Stages <span>{stageDefinitions?.length ?? 0}</span></button>
          </div>
          {!embedded && <div className="loop-library-collection-actions">{primaryAction}</div>}
        </div>
        {tab === "loops" ? <>
          <LoopLibraryGroup
            label="Built-in"
            note="read-only · bundled"
            definitions={builtins}
            onEdit={(definition) => setEditor(editorSeed(definition, false, "library"))}
            onDuplicate={(definition) => setEditor(editorSeed(definition, true, "library"))}
          />
          <LoopLibraryGroup
            label="Library"
            note="reusable across Works"
            definitions={repository}
            onEdit={(definition) => setEditor(editorSeed(definition, false, "library"))}
            onDuplicate={(definition) => setEditor(editorSeed(definition, true, "library"))}
            onDelete={(definition) => void remove(definition)}
            busyId={busyId}
          />
          {work.length > 0 && <LoopLibraryGroup label="Work" note="available only to this Work" definitions={work} onEdit={(definition) => setEditor(editorSeed(definition, false, "work"))} onDuplicate={(definition) => setEditor(editorSeed(definition, true, "work"))} onDelete={(definition) => void remove(definition)} busyId={busyId} />}
        </> : <>
          <p className="stage-library-intro">Stages are loop-independent building blocks. A loop injects run-time inputs and wires outcomes.</p>
          <StageLibraryGroup label="Built-in" note="read-only · bundled" definitions={builtinStages} onEdit={(definition) => setStageEditor(stageEditorSeed(definition, false))} onDuplicate={(definition) => setStageEditor(stageEditorSeed(definition, true))} />
          <StageLibraryGroup label="Library" note="~/Atelier/stages/" definitions={repositoryStages} onEdit={(definition) => setStageEditor(stageEditorSeed(definition, false))} onDuplicate={(definition) => setStageEditor(stageEditorSeed(definition, true))} onDelete={(definition) => void removeStage(definition)} busyId={busyId} />
        </>}
      </main>
    </div>
  );
}

function StageLibraryGroup({ label, note, definitions, onEdit, onDuplicate, onDelete, busyId }: {
  label: string;
  note: string;
  definitions: StageDefinition[];
  onEdit: (definition: StageDefinition) => void;
  onDuplicate: (definition: StageDefinition) => void;
  onDelete?: (definition: StageDefinition) => void;
  busyId?: string | null;
}) {
  return <section className="loop-library-group">
    <div className="loop-library-group-head"><strong>{label}</strong><span>{definitions.length}</span><small>{note}</small></div>
    <div className="loop-library-grid">
      {definitions.map((definition) => <article key={`${definition.scope}:${definition.id}`} className="loop-card loop-library-card stage-library-card" data-stage-kind={definition.stage.kind} onClick={() => onEdit(definition)}>
        <div className="loop-card-head">
          <span className="loop-library-icon">{stageIcon(definition.stage)}</span>
          <span className="loop-card-copy"><strong>{definition.name}<em className={`loop-scope-badge ${definition.scope}`}>{definition.scope === "builtin" ? "built-in" : "library"}</em></strong><small>{definition.description}</small></span>
          <span className="loop-card-actions" onClick={(event) => event.stopPropagation()}>
            <button className="btn icon sm" title={definition.scope === "builtin" ? "Fork to library" : "Edit"} onClick={() => onEdit(definition)}>{definition.scope === "builtin" ? <CopyIcon size={12} /> : <EditIcon size={12} />}</button>
            {definition.scope === "library" && <button className="btn icon sm" title="Duplicate" onClick={() => onDuplicate(definition)}><CopyIcon size={12} /></button>}
            {onDelete && <button className="btn icon sm danger" disabled={busyId === definition.id} title="Delete" onClick={() => onDelete(definition)}><TrashIcon size={12} /></button>}
          </span>
        </div>
        <div className="loop-card-meta"><span>{stageKindLabel(definition.stage.kind)}</span><span>rev <b>{definition.revision || "—"}</b></span><span>{definition.used_by.length ? `used by ${definition.used_by.length} loops` : "not used yet"}</span></div>
      </article>)}
    </div>
  </section>;
}

function StageEditorScreen({
  rootPath,
  seed,
  onClose,
  onSaved,
  loopName,
  loopStages = [],
  onApplyLocal,
  onApplyLinked,
}: {
  rootPath?: string | null;
  seed: StageEditorSeed;
  onClose: () => void;
  onSaved?: (definition: StageDefinition) => void;
  loopName?: string;
  loopStages?: LoopStepDefinition[];
  onApplyLocal?: (stage: LoopStepDefinition, outcomes: LoopOutcome[]) => void;
  onApplyLinked?: (definition: StageDefinition) => void;
}) {
  const [draft, setDraft] = useState(() => structuredClone(seed.definition));
  const [preview, setPreview] = useState(false);
  const [advanced, setAdvanced] = useState(false);
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [dirty, setDirty] = useState(seed.expectedRevision === null);
  const [contextRoot, setContextRoot] = useState<string | null>(rootPath ?? null);
  const inspectorWidth = useLayoutStore((state) => state.loopInspectorWidth);
  const setInspectorWidth = useLayoutStore((state) => state.setLoopInspectorWidth);
  const local = Boolean(onApplyLocal);
  const availableStages = local ? [...loopStages, draft.stage] : [draft.stage];
  // A library stage always lives in the repo scope (`saveToLibrary` pins it),
  // so there is no work-scoped branch here as there is for a loop. A local
  // stage has no folder at all until it is saved, and says so in its own card.
  //
  // `unsaved` also covers builtins, which have no folder to reveal: opening
  // one seeds a fork with a null expected revision, so the button is disabled
  // rather than 404ing on the backend.
  const storage = local
    ? undefined
    : {
        path: `${rootPath ? `${rootPath.replace(/[\\/]+$/, "")}/` : ""}stages/${draft.id}`,
        unsaved: seed.expectedRevision === null,
        onReveal: () =>
          void revealStageDefinition(draft.id, rootPath).catch((reason) =>
            setError(reason instanceof Error ? reason.message : String(reason)),
          ),
      };

  function patchStage(patch: Partial<LoopStepDefinition>) {
    setDraft((current) => {
      const id = seed.expectedRevision === null && patch.name
        ? slugify(patch.name) || current.id
        : current.id;
      return {
        ...current,
        name: patch.name ?? current.name,
        id,
        stage: { ...current.stage, ...patch, id },
      };
    });
    setDirty(true);
  }

  function changeStageKind(kind: LoopStepKind) {
    const patch = stageKindPatch(kind);
    setDraft((current) => ({
      ...current,
      stage: { ...current.stage, ...patch, id: current.stage.id, name: current.stage.name },
      outcomes: stageKindOutcomes(kind),
    }));
    setPreview(false);
    setDirty(true);
  }

  async function saveToLibrary() {
    setSaving(true);
    setError(null);
    try {
      const saved = await saveStageDefinition({
        id: draft.id,
        name: draft.name,
        description: draft.description,
        scope: "library",
        forked_from: draft.forked_from,
        stage: {
          ...draft.stage,
          id: draft.id,
          name: draft.name,
          transitions: {},
          stage_ref: null,
          overrides: null,
        },
        outcomes: draft.outcomes,
        expected_revision: seed.expectedRevision,
      }, rootPath);
      setDirty(false);
      if (local) onApplyLinked?.(saved);
      else onSaved?.(saved);
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : String(reason));
    } finally {
      setSaving(false);
    }
  }

  const applyLocal = () => onApplyLocal?.(draft.stage, draft.outcomes);
  const stageLabel = draft.name.trim() || "Unnamed stage";
  const source = seed.source;
  const usage = source?.used_by ?? draft.used_by;
  const revision = source?.revision ?? draft.revision;
  const stageMeta = local
    ? <>{stageKindLabel(draft.stage.kind)} · <em>local to this loop · unsaved</em></>
    : <>{stageKindLabel(draft.stage.kind)} · {revision ? <>rev {revision} · {usage.length ? `used by ${usage.length} loop${usage.length === 1 ? "" : "s"}` : "not used yet"}</> : "new stage"}{source?.scope === "builtin" && <> · <span className="stage-source-meta">built-in · editing forks</span></>}</>;

  return <div className="loop-fullscreen loop-editor stage-editor has-topbar" style={{ "--loop-inspector-width": `${inspectorWidth}px` } as CSSProperties}>
    <ShellTopbar
      crumbs={local
        ? [{ href: "/settings", label: "settings" }, { onClick: onClose, label: "loops" }, { onClick: onClose, label: loopName ?? "edit loop" }, { label: stageLabel }]
        : [{ href: "/settings", label: "settings" }, { onClick: onClose, label: "loops" }, { onClick: onClose, label: "stages" }, { label: stageLabel }]}
      primaryAction={<><button className="btn sm" onClick={onClose}>Close</button><button className="btn primary sm" disabled={!dirty || saving || !draft.name.trim()} onClick={local ? applyLocal : () => void saveToLibrary()}><CheckIcon size={11} /> {saving ? "Saving…" : "Save"}</button></>}
      showUtilities={false}
      view={{ inline: true, title: <span className="loop-editor-meta">{stageMeta}</span> }}
    />
    {error && <div className="loop-editor-error form-error">{error}</div>}
    <div className="loop-editor-columns">
      <main className="stage-editor-main themed-scrollbar">
        <div className="stage-editor-content">
          {draft.stage.kind !== "pr" && <div className="loop-contract-note stage-definition-note"><span aria-hidden>◇</span><span>{local ? <>Created with <b>+ Add stage → Start blank</b> in {loopName ?? "this loop"}. It exists only in this loop until you save it to the library.</> : <>A stage is defined <b>without a loop</b>. Each loop injects the run-time inputs below and wires outcomes to its own destinations.</>}</span></div>}
          <StageDefinitionInstructions stage={draft.stage} documentId={source?.stage.id ?? draft.stage.id} preview={preview} onPreview={setPreview} onPatch={patchStage} />
          <StageContextSections stage={draft.stage} stages={availableStages} rootPath={contextRoot} editableInputs={local} showBundled={draft.stage.kind !== "pr"} onRootPath={setContextRoot} onPatch={patchStage} />
        </div>
      </main>
      <PaneResizeHandle defaultValue={384} edge="left" label="Resize stage inspector" min={LOOP_INSPECTOR_MIN} max={LOOP_INSPECTOR_MAX} value={inspectorWidth} onChange={setInspectorWidth} />
      <StageDefinitionInspector stage={draft.stage} stages={availableStages} outcomes={draft.outcomes} advanced={advanced} local={local} saving={saving} storage={storage} onAdvanced={() => setAdvanced((value) => !value)} onKindChange={changeStageKind} onPatch={patchStage} onOutcomes={(outcomes) => { setDraft((current) => ({ ...current, outcomes })); setDirty(true); }} onKeepLocal={local ? applyLocal : undefined} onSaveToLibrary={local ? () => void saveToLibrary() : undefined} />
    </div>
  </div>;
}

function StageDefinitionInstructions({ stage, documentId, preview, onPreview, onPatch }: {
  stage: LoopStepDefinition;
  documentId: string;
  preview: boolean;
  onPreview: (value: boolean) => void;
  onPatch: (patch: Partial<LoopStepDefinition>) => void;
}) {
  if (stage.kind === "user_approval" || stage.kind === "deterministic_check") {
    return <InstructionsPanel stage={stage} preview={preview} onPreview={onPreview} onPatch={onPatch} />;
  }
  const config = stage.pr_config ?? defaultPrConfig();
  const patchConfig = (patch: Partial<PrStageConfig>) => onPatch({ pr_config: { ...config, ...patch } });
  return <>
    <section className={`stage-document-editor${stage.kind === "pr" ? " pr" : ""}`}>
      <header>
        <strong>Instructions</strong>
        <small>steps/{documentId}.md · markdown</small>
        <button type="button" onClick={() => onPreview(!preview)}>{preview ? "✎ write" : "▤ preview"}</button>
      </header>
      {preview ? <div className="loop-md-preview"><MarkdownText text={stage.instructions} /></div> : <textarea className="loop-md-editor" rows={stage.kind === "pr" ? 6 : 10} value={stage.instructions} onChange={(event) => onPatch({ instructions: event.target.value })} />}
      <small className="stage-contract-footnote"><LockIcon size={10} /> the execution, reporting &amp; safety contract is appended by Atelier · never editable</small>
    </section>
    {stage.kind === "pr" && <section className="stage-pr-inputs">
      <header><strong>Stage inputs</strong><small>declared by this stage kind · asked in the one-off modal &amp; run setup</small></header>
      <label className="stage-pr-name"><span><strong>PR name</strong><small>resolved at run time</small></span><input value={config.name_template} onChange={(event) => patchConfig({ name_template: event.target.value })} /></label>
      <fieldset className="stage-pr-description">
        <legend>Description</legend>
        <div className={config.description_mode === "automatic" ? "selected" : ""}>
          <label><input type="radio" name="stage-pr-description" checked={config.description_mode === "automatic"} onChange={() => patchConfig({ description_mode: "automatic" })} /><span><strong>Automatic</strong><small>the agent writes it from the run report</small></span></label>
          {config.description_mode === "automatic" && <input value={config.description_instructions} onChange={(event) => patchConfig({ description_instructions: event.target.value })} placeholder="optional instructions for the description…" />}
        </div>
        <div className={config.description_mode === "manual" ? "selected" : ""}>
          <label><input type="radio" name="stage-pr-description" checked={config.description_mode === "manual"} onChange={() => patchConfig({ description_mode: "manual" })} /><span><strong>Manual</strong><small>write it here yourself · markdown · no pause</small></span></label>
          {config.description_mode === "manual" && <textarea rows={4} value={config.manual_body} onChange={(event) => patchConfig({ manual_body: event.target.value })} placeholder="Pull request description" />}
        </div>
      </fieldset>
      <div className="stage-pr-inline-fields">
        <span><strong>Status</strong><span className="segmented" role="group" aria-label="Pull request status"><button type="button" aria-pressed={config.status === "draft"} className={config.status === "draft" ? "active" : ""} onClick={() => patchConfig({ status: "draft" })}>draft</button><button type="button" aria-pressed={config.status === "open"} className={config.status === "open" ? "active" : ""} onClick={() => patchConfig({ status: "open" })}>open</button></span></span>
        <label><strong>Base</strong><input list="stage-pr-base-branches" value={config.base_branch} onChange={(event) => patchConfig({ base_branch: event.target.value })} /><datalist id="stage-pr-base-branches"><option value="master" /><option value="main" /></datalist></label>
        <small>detached HEAD → the run pauses to ask for a branch name</small>
      </div>
    </section>}
  </>;
}

function EditableRuntimeInputs({ stage, onPatch }: {
  stage: LoopStepDefinition;
  onPatch: (patch: Partial<LoopStepDefinition>) => void;
}) {
  const runtime = stage.context
    .map((item, index) => ({ item, index }))
    .filter(({ item }) => RUNTIME_CONTEXT_KINDS.some((kind) => kind.kind === item.kind));
  function patch(index: number, item: LoopContextReference) {
    onPatch({ context: stage.context.map((current, itemIndex) => itemIndex === index ? item : current) });
  }
  return <section className="loop-inspector-field stage-editor-section stage-runtime-editable">
    <header><strong>Run-time inputs</strong><small>bound by this loop · editable here, in the loop's context</small></header>
    <div className="stage-runtime-list">
      {runtime.map(({ item, index }) => <div className="stage-runtime-row" key={`${item.kind}:${index}`}>
        <span className="stage-runtime-glyph">⇣</span>
        <select aria-label="Run-time input" value={item.kind} onChange={(event) => patch(index, { ...item, kind: event.target.value as LoopContextKind })}>{RUNTIME_CONTEXT_KINDS.map((kind) => <option key={kind.kind} value={kind.kind}>{kind.label.toLowerCase()}</option>)}</select>
        <button type="button" className={item.required ? "required active" : "required"} onClick={() => patch(index, { ...item, required: !item.required })}>{item.required ? "required" : "optional"}</button>
        <em className="tag info">injected</em>
        <button type="button" className="btn ghost icon sm" onClick={() => onPatch({ context: stage.context.filter((_, itemIndex) => itemIndex !== index) })} aria-label={`Remove ${item.kind}`}>×</button>
      </div>)}
    </div>
    <label className="stage-runtime-add">+ add input<select aria-label="Add run-time input" value="" onChange={(event) => {
      if (!event.target.value) return;
      onPatch({ context: [...stage.context, { kind: event.target.value as LoopContextKind, required: false, paths: [], step: null, ref: null }] });
    }}><option value="">Choose…</option>{RUNTIME_CONTEXT_KINDS.map((kind) => <option key={kind.kind} value={kind.kind}>{kind.label}</option>)}</select></label>
    <small className="stage-editor-footnote">The library copy keeps these read-only. Loops adjust them at link time.</small>
  </section>;
}

function StageContextSections({ stage, stages, rootPath, editableInputs, showBundled, onRootPath, onPatch }: {
  stage: LoopStepDefinition;
  stages: LoopStepDefinition[];
  rootPath: string | null;
  editableInputs: boolean;
  showBundled: boolean;
  onRootPath: (path: string) => void;
  onPatch: (patch: Partial<LoopStepDefinition>) => void;
}) {
  const runtimeInputs = stage.context.filter((item) =>
    RUNTIME_CONTEXT_KINDS.some((kind) => kind.kind === item.kind),
  );
  return <>
    {editableInputs ? (
      <EditableRuntimeInputs stage={stage} onPatch={onPatch} />
    ) : (
      <section className="loop-inspector-field stage-editor-section">
        <header><strong>Run-time inputs</strong><small>provided by the loop · read-only here</small></header>
        <div className="stage-runtime-list">
          {runtimeInputs.map((item, index) => {
            const meta = CONTEXT_KINDS.find((kind) => kind.kind === item.kind);
            const prReport = stage.kind === "pr" && item.kind === "previous_report";
            return <div className="stage-runtime-row" key={`${item.kind}:${index}`}><span className="stage-runtime-glyph">⇣</span><strong>{prReport ? "run report" : (meta?.label ?? item.kind).toLowerCase()}</strong><span>{item.required ? "required" : "optional"}{prReport ? " · feeds the automatic description" : ""}</span><em className="tag info">injected</em></div>;
          })}
          {runtimeInputs.length === 0 && <div className="stage-editor-empty">No run-time inputs declared.</div>}
        </div>
        <small className="stage-editor-footnote">Bound automatically when a loop links this stage. Adjust them per loop in the loop editor.</small>
      </section>
    )}
    {showBundled && <ContextSubsetPanel
      stage={stage}
      stages={stages}
      rootPath={rootPath}
      kinds={BUNDLED_CONTEXT_KINDS}
      label="Bundled context"
      hint="travels with the stage into every loop"
      addLabel="Add bundled context"
      footnote="Bundled references travel with the stage; injected run-time inputs remain loop-owned."
      compact
      onRootPath={onRootPath}
      onPatch={onPatch}
    />}
  </>;
}

function ContextSubsetPanel({ stage, stages, rootPath, kinds, label, hint, addLabel, footnote, compact = false, subsetFirst = false, onRootPath, onPatch }: {
  stage: LoopStepDefinition;
  stages: LoopStepDefinition[];
  rootPath: string | null;
  kinds: typeof CONTEXT_KINDS;
  label: string;
  hint: string;
  addLabel: string;
  footnote: string;
  compact?: boolean;
  subsetFirst?: boolean;
  onRootPath: (path: string) => void;
  onPatch: (patch: Partial<LoopStepDefinition>) => void;
}) {
  const allowed = new Set(kinds.map((item) => item.kind));
  const subset = stage.context.filter((item) => allowed.has(item.kind));
  return <ContextPanel
    stage={{ ...stage, context: subset }}
    stages={stages}
    rootPath={rootPath}
    kinds={kinds}
    label={label}
    hint={hint}
    addLabel={addLabel}
    footnote={footnote}
    compact={compact}
    showSafetyNote={false}
    onRootPath={onRootPath}
    onPatch={(patch) => {
      if (patch.context === undefined) return;
      const other = stage.context.filter((item) => !allowed.has(item.kind));
      onPatch({ context: subsetFirst ? [...patch.context, ...other] : [...other, ...patch.context] });
    }}
  />;
}

function StageDefinitionInspector({ stage, stages, outcomes, advanced, local, saving, storage, onAdvanced, onKindChange, onPatch, onOutcomes, onKeepLocal, onSaveToLibrary }: {
  stage: LoopStepDefinition;
  stages: LoopStepDefinition[];
  outcomes: LoopOutcome[];
  advanced: boolean;
  local: boolean;
  saving: boolean;
  /** Omitted for a loop-local stage: it has no library folder yet. */
  storage?: { path: string; unsaved: boolean; onReveal: () => void };
  onAdvanced: () => void;
  onKindChange: (kind: LoopStepKind) => void;
  onPatch: (patch: Partial<LoopStepDefinition>) => void;
  onOutcomes: (outcomes: LoopOutcome[]) => void;
  onKeepLocal?: () => void;
  onSaveToLibrary?: () => void;
}) {
  const custom = local && stage.kind === "agent_task";
  return <aside className="loop-inspector stage-definition-inspector" data-stage-kind={stage.kind}>
    <div className="loop-inspector-head" data-stage-kind={stage.kind}>
      <span>{custom ? <span aria-hidden>◇</span> : stageIcon(stage)} {custom ? "Custom" : stageKindLabel(stage.kind)}</span>
      <input aria-label="Stage name" value={stage.name} onChange={(event) => onPatch({ name: event.target.value })} placeholder="Unnamed stage" />
      <label className="stage-kind-field"><small>Stage kind</small><select aria-label="Stage kind" value={stage.kind} onChange={(event) => onKindChange(event.target.value as LoopStepKind)}>
        <option value="agent_task">{custom ? "◇ custom" : "Agent task"}</option>
        <option value="agent_review">Agent review</option>
        <option value="deterministic_check">Check</option>
        <option value="user_approval">Approval</option>
        <option value="pr">Create PR</option>
      </select></label>
    </div>
    <div className="loop-inspector-body themed-scrollbar">
      {stage.agent && <section className="stage-editor-rail-group"><header><strong>Execution defaults</strong><small>{stage.kind === "pr" ? "defaults to the loop's implementing stage" : "loops & runs can override"}</small></header><AgentPanel stage={stage} stages={stages} stageEditor onPatch={onPatch} /></section>}
      <StageOutcomeContract stage={stage} outcomes={outcomes} onChange={onOutcomes} />
      {stage.kind !== "pr" && <div className="stage-outcome-note"><ReturnIcon size={12} /><span>A stage declares what it can return. It has no graph of its own. Each loop maps these outcomes to stages, pause, or fail.</span></div>}
      {stage.kind !== "user_approval" && stage.kind !== "pr" && <><button className={"loop-advanced-toggle" + (advanced ? " open" : "")} onClick={onAdvanced}><ChevronRightIcon size={11} /> Advanced · retry limit · timeout · report preset</button>{advanced && <AdvancedPanel stage={stage} onPatch={onPatch} />}</>}
      {storage && <section className="stage-editor-rail-group">
        <header><strong>Storage folder</strong><small>where the library keeps this stage</small></header>
        <div className="loop-storage-field">
          <button
            type="button"
            disabled={storage.unsaved}
            title={storage.unsaved ? "Save before opening this folder" : storage.path}
            onClick={storage.onReveal}
          >
            <FolderIcon size={13} />
            <code>{storage.path}</code>
          </button>
        </div>
      </section>}
      {local ? <section className="stage-linked-banner local stage-save-card">
        <span><strong>◇ Local to this loop</strong><em>unsaved</em></span>
        <small>Only this loop can select the stage until it is saved to the library.</small>
        <input value={stage.name} onChange={(event) => onPatch({ name: event.target.value })} placeholder="Stage name" />
        <code>→ stages/{slugify(stage.name) || "stage-id"}</code>
        <div className="stage-save-card-actions"><button className="btn sm" disabled={!stage.name.trim()} onClick={onKeepLocal}>Keep local</button><button className="btn primary sm" disabled={saving || !stage.name.trim()} onClick={onSaveToLibrary}>{saving ? "Saving…" : "↑ Save to library"}</button></div>
      </section> : stage.kind === "pr" ? <small className="stage-editor-footnote stage-editor-revision-note">When a loop contains this stage, the run view's ⇱ Create PR button is hidden. The stage owns it.</small> : <small className="stage-editor-footnote stage-editor-revision-note">Saving bumps the revision. Linked loops pick it up on their next run; loop-local overrides stay.</small>}
    </div>
  </aside>;
}

function StageOutcomeContract({ stage, outcomes, onChange }: {
  stage: LoopStepDefinition;
  outcomes: LoopOutcome[];
  onChange: (outcomes: LoopOutcome[]) => void;
}) {
  const rows = OUTCOMES.filter((outcome) => outcome.key === "changes_requested" || outcomes.includes(outcome.key));
  const hints: Record<LoopOutcome, string> = stage.kind === "pr" ? {
    pass: "run completes · the PR link lands on the work",
    changes_requested: "this stage never requests changes",
    blocked_user: "detached HEAD pauses here to name the branch",
    failed: "hook output shown · retry or the loop wires Implement",
  } : {
    pass: "continues · the loop wires what comes next",
    changes_requested: "returns work · the loop wires the return edge",
    blocked_user: "pauses the run and asks you",
    failed: "fails the run after retries",
  };
  return <section className="stage-outcome-contract">
    <header><strong>Outcomes</strong><small>destinations wired per loop</small></header>
    <div>{rows.map((outcome) => <div className={`stage-outcome-contract-row ${outcome.key}`} key={outcome.key}>
      <strong><i />{outcome.label}</strong>
      <small>{hints[outcome.key]}</small>
      {outcome.key === "changes_requested" && <select aria-label="Changes requested capability" disabled={stage.kind === "pr"} value={stage.kind === "pr" ? "never" : outcomes.includes(outcome.key) ? "emits" : "never"} onChange={(event) => onChange(event.target.value === "emits" ? [...new Set([...outcomes, outcome.key])] : outcomes.filter((item) => item !== outcome.key))}><option value="emits">emits</option><option value="never">never</option></select>}
    </div>)}</div>
  </section>;
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
                <button className="btn icon sm" title={definition.scope === "builtin" ? "Duplicate to library" : "Edit"} onClick={() => definition.scope === "builtin" ? onDuplicate(definition) : onEdit(definition)}>
                  {definition.scope === "builtin" ? <CopyIcon size={12} /> : <EditIcon size={12} />}
                </button>
                {definition.scope === "library" && <button className="btn icon sm" title="Duplicate" onClick={() => onDuplicate(definition)}><CopyIcon size={12} /></button>}
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
  rootPath,
  saveScope,
  seed,
  onClose,
  onSaved,
}: {
  workSlug: string | null;
  rootPath?: string | null;
  saveScope: LoopSaveScope;
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
  const [stageDefinitions, setStageDefinitions] = useState<StageDefinition[]>([]);
  const [libraryStageEditor, setLibraryStageEditor] = useState<StageEditorSeed | null>(null);
  const [localStageEditor, setLocalStageEditor] = useState<{ index: number; seed: StageEditorSeed } | null>(null);
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [dirty, setDirty] = useState(seed.expectedRevision === null);
  const [contextRoot, setContextRoot] = useState<string | null>(rootPath ?? null);
  const loopInspectorWidth = useLayoutStore((state) => state.loopInspectorWidth);
  const setLoopInspectorWidth = useLayoutStore((state) => state.setLoopInspectorWidth);
  const selected = draft.stages.find((stage) => stage.id === selectedId) ?? null;
  const storagePath = saveScope === "work" && workSlug
    ? `works/${workSlug}/loops/${draft.id}`
    : `${rootPath ? `${rootPath.replace(/[\\/]+$/, "")}/` : ""}loops/${draft.id}`;

  const refreshStages = () => listAvailableStageDefinitions(rootPath)
    .then(setStageDefinitions)
    .catch((reason) => setError(reason instanceof Error ? reason.message : String(reason)));

  useEffect(() => {
    void refreshStages();
  }, [rootPath, workSlug]);

  useEffect(() => {
    if (addAt === null) return;
    const closeAddStage = (event: KeyboardEvent) => {
      if (event.key !== "Escape") return;
      event.preventDefault();
      event.stopImmediatePropagation();
      setAddAt(null);
    };
    window.addEventListener("keydown", closeAddStage);
    return () => window.removeEventListener("keydown", closeAddStage);
  }, [addAt]);

  function updateDraft(next: LoopDefinition) {
    setDraft(next);
    setDirty(true);
  }

  function patchStage(stepId: string, patch: Partial<LoopStepDefinition>) {
    updateDraft({
      ...draft,
      stages: draft.stages.map((stage) =>
        stage.id === stepId
          ? {
              ...stage,
              ...patch,
              overrides: stage.stage_ref
                ? { ...(stage.overrides ?? {}), ...withoutWiring(patch) }
                : stage.overrides,
            }
          : stage,
      ),
    });
  }

  function addStage(index: number, definition?: StageDefinition) {
    if (!definition) {
      setLocalStageEditor({ index, seed: newLocalStageSeed(draft.stages) });
      setAddAt(null);
      return;
    }
    insertStage(index, stageFromDefinition(definition, draft.stages), definition.outcomes);
  }

  function insertStage(index: number, stage: LoopStepDefinition, declaredOutcomes: LoopOutcome[]) {
    const stages = [...draft.stages];
    const previous = stages[index - 1];
    const next = stages[index];
    if (previous) {
      stages[index - 1] = {
        ...previous,
        transitions: { ...previous.transitions, pass: stage.id },
      };
    }
    const declared = new Set<LoopOutcome>(declaredOutcomes);
    const returnTarget = [...stages.slice(0, index)].reverse().find((item) => (
      item.kind === "agent_task" && item.agent?.permissions !== "read"
    )) ?? stages.find((item) => item.kind === "agent_task" && item.agent?.permissions !== "read");
    stage.transitions = {
      ...(declared.has("pass") && (next || stage.kind !== "user_approval")
        ? { pass: next?.id ?? "complete" }
        : {}),
      ...(declared.has("changes_requested") && returnTarget
        ? { changes_requested: returnTarget.id }
        : {}),
      ...(declared.has("blocked_user") ? { blocked_user: "pause" } : {}),
      ...(declared.has("failed") ? { failed: "fail" } : {}),
    };
    stages.splice(index, 0, stage);
    updateDraft({ ...draft, stages });
    setSelectedId(stage.id);
    setTab("instructions");
    setAddAt(null);
  }

  function detachStage(stageId: string) {
    updateDraft({
      ...draft,
      stages: draft.stages.map((stage) => stage.id === stageId
        ? { ...stage, stage_ref: null, overrides: null }
        : stage),
    });
  }

  async function saveLocalStage(stage: LoopStepDefinition) {
    setSaving(true);
    setError(null);
    try {
      const definition = await saveStageDefinition({
        id: slugify(stage.name) || stage.id,
        name: stage.name,
        description: stage.instructions.split("\n").find(Boolean) ?? "",
        scope: "library",
        forked_from: null,
        stage: { ...stage, transitions: {}, stage_ref: null, overrides: null },
        outcomes: OUTCOMES.map((item) => item.key).filter((key) => key in stage.transitions),
        expected_revision: null,
      }, rootPath);
      updateDraft({
        ...draft,
        stages: draft.stages.map((item) => item.id === stage.id
          ? { ...definition.stage, id: item.id, transitions: item.transitions, stage_ref: { definition_id: definition.id, revision: definition.revision }, overrides: null }
          : item),
      });
      await refreshStages();
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : String(reason));
    } finally {
      setSaving(false);
    }
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

  async function save(targetScope: LoopSaveScope = saveScope) {
    setSaving(true);
    setError(null);
    try {
      const promoting = targetScope === "library" && saveScope === "work";
      let targetId = draft.id;
      let expectedRevision = seed.expectedRevision;
      let forkedFrom = draft.forked_from;
      if (promoting) {
        const library = await listLoopDefinitions(null);
        const baseId = slugify(draft.name) || draft.id;
        const previousPromotion = library.find((definition) =>
          definition.scope === "library" &&
          definition.forked_from === draft.id &&
          (definition.id === baseId || definition.id.startsWith(`${baseId}-`)),
        ) ?? library.find((definition) =>
          definition.scope === "library" &&
          definition.id === draft.id &&
          Boolean(definition.forked_from) &&
          draft.forked_from === definition.id,
        ) ?? null;
        targetId = previousPromotion?.id ?? uniqueId(
          baseId,
          library.map((definition) => definition.id),
        );
        expectedRevision = previousPromotion?.revision ?? null;
        forkedFrom = previousPromotion?.forked_from ?? draft.id;
      }
      const saved = await saveLoopDefinition(workSlug, {
        id: targetId,
        name: draft.name,
        description: draft.description,
        scope: targetScope,
        forked_from: forkedFrom,
        stages: draft.stages,
        expected_revision: expectedRevision,
      }, promoting ? undefined : rootPath);
      setDirty(false);
      onSaved(saved);
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err));
    } finally {
      setSaving(false);
    }
  }

  if (libraryStageEditor) {
    return <StageEditorScreen rootPath={stageDefinitionRoot(libraryStageEditor.definition, rootPath)} seed={libraryStageEditor} onClose={() => setLibraryStageEditor(null)} onSaved={() => { setLibraryStageEditor(null); void refreshStages(); }} />;
  }

  if (localStageEditor) {
    return <StageEditorScreen
      rootPath={rootPath}
      seed={localStageEditor.seed}
      loopName={draft.name}
      loopStages={draft.stages}
      onClose={() => setLocalStageEditor(null)}
      onApplyLocal={(stage, outcomes) => {
        const id = uniqueId(stage.id || slugify(stage.name) || "custom-stage", draft.stages.map((item) => item.id));
        insertStage(localStageEditor.index, { ...structuredClone(stage), id, stage_ref: null, overrides: null }, outcomes);
        setLocalStageEditor(null);
      }}
      onApplyLinked={(definition) => {
        insertStage(localStageEditor.index, stageFromDefinition(definition, draft.stages), definition.outcomes);
        setLocalStageEditor(null);
        void refreshStages();
      }}
    />;
  }

  return (
    <div
      className="loop-fullscreen loop-editor has-topbar"
      style={{ "--loop-inspector-width": `${loopInspectorWidth}px` } as CSSProperties}
    >
      <ShellTopbar
        crumbs={workSlug
          ? [
              { href: `/works/${workSlug}`, label: workSlug },
              { onClick: onClose, label: "loops" },
              { label: seed.expectedRevision === null ? "create loop" : "edit loop" },
            ]
          : [
              { href: "/settings", label: "settings" },
              { href: "/settings/loops", label: "loops" },
              { label: seed.expectedRevision === null ? "create loop" : "edit loop" },
            ]}
        primaryAction={(
          <>
            <button className="btn sm" onClick={onClose}>Close</button>
            {saveScope === "work" && (
              <button className="btn sm" disabled={saving || !draft.name.trim() || !draft.id} onClick={() => void save("library")}><CopyIcon size={11} /> Save to library</button>
            )}
            <button className="btn primary sm" disabled={!dirty || saving || !draft.name.trim() || !draft.id} onClick={() => void save()}><CheckIcon size={11} /> {saving ? "Saving…" : "Save"}</button>
          </>
        )}
        showUtilities={false}
        view={{
          inline: true,
          title: (
            <span className="loop-editor-meta">
              {draft.stages.length} stages
              {draft.forked_from && <> · forked from {draft.forked_from}</>}
              {dirty ? <em>● unsaved changes</em> : <> · rev {draft.revision}</>}
            </span>
          ),
        }}
      />
      {error && <div className="loop-editor-error form-error">{error}</div>}
      <div className="loop-editor-columns">
        <main className="loop-timeline themed-scrollbar">
          <div className="loop-contract-note">
            <LockIcon size={12} />
            <span>The immutable execution, reporting, and safety contract is appended by Atelier. You author <b>instructions</b> and <b>context</b>, never provider prompts.</span>
          </div>
          <div className="loop-identity-fields">
            <label className="loop-name-field">
              Loop name
              <input
                value={draft.name}
                onChange={(event) => {
                  const name = event.target.value;
                  updateDraft({
                    ...draft,
                    name,
                    id: seed.expectedRevision === null ? slugify(name) : draft.id,
                  });
                }}
                placeholder="Untitled loop"
              />
            </label>
            <div className="loop-storage-field">
              Storage folder
              <button
                type="button"
                disabled={seed.expectedRevision === null}
                title={seed.expectedRevision === null ? "Save before opening this folder" : storagePath}
                onClick={() =>
                  void revealLoopDefinition(workSlug, draft.id, rootPath, saveScope).catch((err) =>
                    setError(err instanceof Error ? err.message : String(err)),
                  )
                }
              >
                <FolderIcon size={13} />
                <code>{storagePath}</code>
              </button>
            </div>
          </div>
          <label className="loop-description-field">Description<input value={draft.description} onChange={(event) => updateDraft({ ...draft, description: event.target.value })} placeholder="What this loop is for" /></label>
          <AddStageSlot index={0} open={addAt === 0} definitions={stageDefinitions} onToggle={() => setAddAt(addAt === 0 ? null : 0)} onAdd={addStage} />
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
                <AddStageSlot index={index + 1} open={addAt === index + 1} definitions={stageDefinitions} onToggle={() => setAddAt(addAt === index + 1 ? null : index + 1)} onAdd={addStage} />
              </div>
            ))}
          </div>
        </main>
        <PaneResizeHandle
          defaultValue={384}
          edge="left"
          label="Resize loop inspector"
          min={LOOP_INSPECTOR_MIN}
          max={LOOP_INSPECTOR_MAX}
          value={loopInspectorWidth}
          onChange={setLoopInspectorWidth}
        />
        {selected ? (
          <StageInspector
            stage={selected}
            stages={draft.stages}
            rootPath={contextRoot}
            onRootPath={setContextRoot}
            tab={tab}
            preview={preview}
            advanced={advanced}
            onTab={setTab}
            onPreview={setPreview}
            onAdvanced={() => setAdvanced((value) => !value)}
            onPatch={(patch) => patchStage(selected.id, patch)}
            source={selected.stage_ref ? stageDefinitions.find((definition) => definition.id === selected.stage_ref?.definition_id) ?? null : null}
            onOpenSource={selected.stage_ref ? () => {
              const source = stageDefinitions.find((definition) => definition.id === selected.stage_ref?.definition_id);
              if (source) setLibraryStageEditor(stageEditorSeed(source, false));
            } : undefined}
            onDetach={selected.stage_ref ? () => detachStage(selected.id) : undefined}
            onSaveToLibrary={!selected.stage_ref ? () => void saveLocalStage(selected) : undefined}
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
  const backlinkIndex = backlink ? stages.findIndex((item) => item.id === backlink.id) : -1;
  const returnSpan = backlinkIndex >= 0 && backlinkIndex < index ? (index - backlinkIndex) * 102 : 0;
  const reviewGate = stage.review_gate
    ?? (stage.kind === "agent_review" && backlink
      ? { mode: "automatic" as const, max_passes: 3, locked: false }
      : null);
  return (
    <div className="loop-stage-row" data-stage-kind={stage.kind}>
      {returnSpan > 0 && reviewGate && (
        <span
          className="loop-stage-return-edge"
          style={{ "--loop-return-span": `${returnSpan}px` } as CSSProperties}
        >
          <button type="button" onClick={onSelect}>
            <em className={reviewGate.mode === "human_check" ? "warn" : ""}>{gateLabel(reviewGate)}</em>
            <small>changes requested{reviewGate.locked ? " · locked" : ""}</small>
          </button>
        </span>
      )}
      <span className="loop-stage-rail"><i>{stageIcon(stage)}</i>{!last && <b />}</span>
      <div>
        <button className={"loop-stage-card" + (selected ? " selected" : "")} onClick={onSelect}>
          <span className="loop-stage-card-head"><strong>{stage.name}</strong><em>{stageKindLabel(stage.kind)}</em></span>
          <span className="loop-stage-card-meta">
            {stage.agent && <><i>{stage.agent.permissions ?? "inherit"}</i><i>{stage.agent.session} session</i></>}
            {stage.note_required != null && <i>brief {stage.note_required ? "required" : "optional"}</i>}
            {stage.context.length > 0 && <i>{stage.context.length} context</i>}
            {stage.kind === "user_approval" && <i>waits for you</i>}
          </span>
          <span className="loop-stage-card-actions" onClick={(event) => event.stopPropagation()}>
            <button className="btn icon sm" disabled={index === 0} onClick={() => onMove(-1)} title="Move up">↑</button>
            <button className="btn icon sm" disabled={last} onClick={() => onMove(1)} title="Move down">↓</button>
            <button className="btn icon sm" onClick={onDuplicate} title="Duplicate"><CopyIcon size={14} /></button>
            <button className="btn icon sm" onClick={onDelete} title="Delete"><TrashIcon size={14} /></button>
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
  rootPath,
  onRootPath,
  tab,
  preview,
  advanced,
  onTab,
  onPreview,
  onAdvanced,
  onPatch,
  source,
  onOpenSource,
  onDetach,
  onSaveToLibrary,
}: {
  stage: LoopStepDefinition;
  stages: LoopStepDefinition[];
  rootPath?: string | null;
  onRootPath: (path: string) => void;
  tab: InspectorTab;
  preview: boolean;
  advanced: boolean;
  onTab: (tab: InspectorTab) => void;
  onPreview: (preview: boolean) => void;
  onAdvanced: () => void;
  onPatch: (patch: Partial<LoopStepDefinition>) => void;
  source?: StageDefinition | null;
  onOpenSource?: () => void;
  onDetach?: () => void;
  onSaveToLibrary?: () => void;
}) {
  const approval = stage.kind === "user_approval";
  const check = stage.kind === "deterministic_check";
  const tabs: Array<{ id: InspectorTab; label: string }> = approval
    ? [{ id: "instructions", label: "Decision" }, { id: "context", label: "Context" }]
    : check
      ? [{ id: "instructions", label: "Check" }, { id: "context", label: "Context" }, { id: "outcome", label: "Outcome" }]
    : [
        { id: "instructions", label: "Instructions" },
        { id: "context", label: "Context" },
        { id: "agent", label: "Agent" },
        { id: "outcome", label: "Outcome" },
      ];
  return (
    <aside className="loop-inspector">
      <div className="loop-inspector-head" data-stage-kind={stage.kind}>
        <span>{stageIcon(stage)} {stageKindLabel(stage.kind)}</span>
        <input value={stage.name} onChange={(event) => onPatch({ name: event.target.value })} />
        <select aria-label="Stage kind" title={stage.stage_ref ? "Detach this stage before changing its kind" : "Stage kind"} disabled={Boolean(stage.stage_ref)} value={stage.kind} onChange={(event) => onPatch(stageKindPatch(event.target.value as LoopStepKind))}>
          <option value="agent_task">Agent task</option>
          <option value="agent_review">Agent review</option>
          <option value="deterministic_check">Check</option>
          <option value="user_approval">Approval</option>
          <option value="pr">Create PR</option>
        </select>
      </div>
      <div className="loop-inspector-tabs">
        {tabs.map((item) => <button key={item.id} className={tab === item.id ? "active" : ""} onClick={() => onTab(item.id)}>{item.label}</button>)}
      </div>
      <div className="loop-inspector-body themed-scrollbar">
        {stage.stage_ref && <div className="stage-linked-banner"><span><CopyIcon size={12} /><strong>Linked · {stage.stage_ref.definition_id}</strong><em>{source?.scope === "builtin" ? "built-in" : "library"}</em></span><small>rev {stage.stage_ref.revision} · edits below are loop-local overrides</small><div>{onOpenSource && <button onClick={onOpenSource}>Open stage</button>}{onDetach && <button onClick={onDetach}>Detach</button>}</div></div>}
        {!stage.stage_ref && onSaveToLibrary && <div className="stage-linked-banner local"><span><strong>Local to this loop</strong><em>unsaved</em></span><small>Save it once to reuse it in other loops.</small><div><button onClick={onSaveToLibrary}>Save to library</button></div></div>}
        {tab === "instructions" && <InstructionsPanel stage={stage} preview={preview} onPreview={onPreview} onPatch={onPatch} />}
        {tab === "context" && <ContextPanel stage={stage} stages={stages} rootPath={rootPath} onRootPath={onRootPath} onPatch={onPatch} />}
        {tab === "agent" && <AgentPanel stage={stage} stages={stages} onPatch={onPatch} />}
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
  if (stage.kind === "pr") {
    const config = stage.pr_config ?? defaultPrConfig();
    const patchConfig = (patch: Partial<typeof config>) => onPatch({ pr_config: { ...config, ...patch } });
    return <>
      <InspectorField label="Instructions · Markdown"><textarea className="loop-md-editor" value={stage.instructions} onChange={(event) => onPatch({ instructions: event.target.value })} /></InspectorField>
      <InspectorField label="PR name template"><input value={config.name_template} onChange={(event) => patchConfig({ name_template: event.target.value })} placeholder="Derived from the run goal" /></InspectorField>
      <InspectorField label="Description">
        <select value={config.description_mode} onChange={(event) => patchConfig({ description_mode: event.target.value as "automatic" | "manual" })}><option value="automatic">Automatic from run report</option><option value="manual">Manual Markdown</option></select>
        <textarea value={config.description_mode === "manual" ? config.manual_body : config.description_instructions} onChange={(event) => patchConfig(config.description_mode === "manual" ? { manual_body: event.target.value } : { description_instructions: event.target.value })} placeholder={config.description_mode === "manual" ? "PR description" : "Optional instructions for the generated description"} />
      </InspectorField>
      <div className="loop-inspector-grid">
        <InspectorField label="Status"><select value={config.status} onChange={(event) => patchConfig({ status: event.target.value as "draft" | "open" })}><option value="draft">Draft</option><option value="open">Open</option></select></InspectorField>
        <InspectorField label="Base branch"><input value={config.base_branch} onChange={(event) => patchConfig({ base_branch: event.target.value })} placeholder="master" /></InspectorField>
      </div>
      <div className="loop-inspector-note"><BranchIcon size={13} /> Run report, workspace diff, changed files, and branch state are injected when this stage runs.</div>
    </>;
  }
  return (
    <>
      <InspectorField label="Instructions · Markdown" action={<span className="loop-md-toggle"><button className={!preview ? "active" : ""} onClick={() => onPreview(false)}>Write</button><button className={preview ? "active" : ""} onClick={() => onPreview(true)}>Preview</button></span>}>
        {preview ? <div className="loop-md-preview"><MarkdownText text={stage.instructions} /></div> : <textarea className="loop-md-editor" value={stage.instructions} onChange={(event) => onPatch({ instructions: event.target.value })} />}
        <div className="loop-inspector-note"><DocIcon size={13} /><span>Stored as <code>steps/{stage.id}.md</code> and referenced from loop.yaml.</span></div>
      </InspectorField>
      <InspectorField label="Work brief slot" hint="per-work input; never changes this template">
        <select
          value={stage.note_required === true ? "required" : stage.note_required === false ? "optional" : "none"}
          onChange={(event) => onPatch({
            note_required: event.target.value === "required" ? true : event.target.value === "optional" ? false : null,
          })}
        >
          <option value="none">No stage note</option>
          <option value="optional">Optional note</option>
          <option value="required">Required note</option>
        </select>
        <div className="loop-inspector-note"><EditIcon size={13} /> Required notes gate Start in Loop mode. Planning binds the task from its story.</div>
      </InspectorField>
    </>
  );
}

function ContextPanel({ stage, stages, rootPath, kinds = CONTEXT_KINDS, label = "Context references", hint = "references, not copies", addLabel = "Add context", footnote, compact = false, showSafetyNote = true, onRootPath, onPatch }: {
  stage: LoopStepDefinition;
  stages: LoopStepDefinition[];
  rootPath?: string | null;
  kinds?: typeof CONTEXT_KINDS;
  label?: string;
  hint?: string;
  addLabel?: string;
  footnote?: string;
  compact?: boolean;
  showSafetyNote?: boolean;
  onRootPath: (path: string) => void;
  onPatch: (patch: Partial<LoopStepDefinition>) => void;
}) {
  const context = stage.context;
  const [pickerIndex, setPickerIndex] = useState<number | null>(null);
  const [pickerError, setPickerError] = useState<string | null>(null);
  const [choosingRoot, setChoosingRoot] = useState(false);
  function patch(index: number, next: LoopContextReference) {
    onPatch({ context: context.map((item, itemIndex) => itemIndex === index ? next : item) });
  }
  function choosePath(index: number) {
    setPickerError(null);
    setPickerIndex(index);
    setChoosingRoot(!rootPath);
  }
  function add(kind: LoopContextKind) {
    onPatch({ context: [...context, { kind, required: false, paths: [], step: null, ref: null }] });
    if (compact && (kind === "files" || kind === "folder")) choosePath(context.length);
  }
  return (
    <>
      <InspectorField label={label} hint={hint}>
        <div className={`loop-context-list${compact ? " stage-bundled-list" : ""}`}>
          {context.map((item, index) => {
            const meta = CONTEXT_KINDS.find((row) => row.kind === item.kind)!;
            if (compact) {
              const kindLabel = item.kind === "files" ? "file" : item.kind === "shared_context" ? "shared context" : item.kind;
              const value = item.paths.join(", ") || item.ref || `Choose ${kindLabel}`;
              return (
                <div className="stage-bundled-row" key={`${item.kind}:${index}`}>
                  <span className="stage-bundled-glyph">◆</span>
                  {item.kind === "note" || item.kind === "shared_context" ? (
                    <input aria-label={meta.label} value={item.ref ?? ""} onChange={(event) => patch(index, { ...item, ref: event.target.value || null })} placeholder={item.kind === "note" ? "Context note" : "Shared context reference"} />
                  ) : <code title={value}>{value}</code>}
                  <span>{kindLabel} · <button className={item.required ? "active" : ""} onClick={() => patch(index, { ...item, required: !item.required })}>{item.required ? "required" : "optional"}</button></span>
                  {!item.paths.length && (item.kind === "files" || item.kind === "folder") && <button className="btn sm" onClick={() => choosePath(index)}>Choose</button>}
                  <button className="btn ghost icon sm" onClick={() => onPatch({ context: context.filter((_, itemIndex) => itemIndex !== index) })} aria-label={`Remove ${meta.label}`}>×</button>
                </div>
              );
            }
            return (
              <div className="loop-context-row" key={`${item.kind}:${index}`}>
                <span className="loop-context-icon">{contextIcon(item.kind)}</span>
                <span className="loop-context-copy"><strong>{meta.label}</strong><small>{meta.hint}</small>
                  {(item.kind === "files" || item.kind === "folder") && (
                    <div className="loop-context-path-picker">
                      {item.paths.map((path) => (
                        <span key={path}>{path}<button type="button" onClick={() => patch(index, { ...item, paths: item.paths.filter((value) => value !== path) })} aria-label={`Remove ${path}`}>×</button></span>
                      ))}
                      <button type="button" onClick={() => choosePath(index)}>
                        <FolderIcon size={10} /> {item.paths.length ? "Add" : "Choose"} {item.kind === "files" ? "file" : "folder"}
                      </button>
                    </div>
                  )}
                  {item.kind === "previous_report" && <select value={item.step ?? ""} onChange={(event) => patch(index, { ...item, step: event.target.value || null })}><option value="">Choose stage…</option>{stages.filter((row) => row.id !== stage.id).map((row) => <option key={row.id} value={row.id}>{row.name}</option>)}</select>}
                  {(item.kind === "note" || item.kind === "shared_context") && <input value={item.ref ?? ""} onChange={(event) => patch(index, { ...item, ref: event.target.value || null })} placeholder={item.kind === "note" ? "Context note" : "Shared context reference"} />}
                </span>
                <button className={"loop-required-toggle" + (item.required ? " active" : "")} onClick={() => patch(index, { ...item, required: !item.required })}>{item.required ? "required" : "optional"}</button>
                <button className="btn ghost icon sm" onClick={() => onPatch({ context: context.filter((_, itemIndex) => itemIndex !== index) })}>×</button>
              </div>
            );
          })}
          {context.length === 0 && !compact && <div className="loop-inspector-note">No context references yet.</div>}
        </div>
        {compact && <div className="loop-add-context stage-context-add">{kinds.filter((item) => item.kind !== "shared_context").map((item) => <button key={item.kind} onClick={() => add(item.kind)}>{item.kind === "files" ? "@" : item.kind === "folder" ? <FolderIcon size={10} /> : <EditIcon size={10} />} {item.kind === "files" ? "File" : item.label}</button>)}</div>}
      </InspectorField>
      {!compact && <InspectorField label={addLabel}>
        <div className="loop-add-context">{kinds.map((item) => <button key={item.kind} onClick={() => add(item.kind)}>+ {item.label}</button>)}</div>
      </InspectorField>}
      {pickerError && <div className="loop-inspector-note"><AlertIcon size={13} /> {pickerError}</div>}
      {rootPath && <div className="loop-inspector-note"><FolderIcon size={13} /> Paths are relative to <code>{rootPath}</code>.</div>}
      {footnote && !compact && <small className="stage-editor-footnote">{footnote}</small>}
      {showSafetyNote && <div className="loop-inspector-note"><LockIcon size={13} /> Required context blocks the stage when unresolved. Paths cannot escape the working root.</div>}
      {choosingRoot && (
        <FolderPickerDialog
          mode="folder"
          onCancel={() => { setChoosingRoot(false); setPickerIndex(null); }}
          onPick={(path) => { onRootPath(path); setChoosingRoot(false); }}
        />
      )}
      {pickerIndex !== null && rootPath && (
        <FolderPickerDialog
          initialPath={rootPath}
          mode={context[pickerIndex]?.kind === "files" ? "file" : "folder"}
          onCancel={() => setPickerIndex(null)}
          onPick={(path) => {
            const relative = relativeContextPath(rootPath, path);
            const item = context[pickerIndex];
            if (!relative || !item) {
              setPickerError("Choose a path inside the working folder.");
              setPickerIndex(null);
              return;
            }
            patch(pickerIndex, {
              ...item,
              paths: item.kind === "files"
                ? [...new Set([...item.paths, relative])]
                : [relative],
            });
            setPickerIndex(null);
          }}
        />
      )}
    </>
  );
}

function AgentPanel({ stage, stages, stageEditor = false, onPatch }: { stage: LoopStepDefinition; stages: LoopStepDefinition[]; stageEditor?: boolean; onPatch: (patch: Partial<LoopStepDefinition>) => void }) {
  const { descriptors } = useProviderDescriptors();
  if (stage.kind === "deterministic_check") {
    return <div className="loop-inspector-note"><FlaskIcon size={13} /> Checks use no agent and run with a bounded timeout.</div>;
  }
  if (!stage.agent) return null;
  const provider = descriptors?.find((item) => item.name === stage.agent?.provider) ?? null;
  const models = provider ? modelPickerOptions(provider) : [];
  const agent = stage.agent;
  const baseStage = stages.find((item) => item.agent !== null) ?? null;
  const isBaseStage = baseStage?.id === stage.id;
  const inheritedFast = !isBaseStage ? baseStage?.agent?.fast ?? null : null;
  const fastProvider = provider ?? (
    agent.provider == null
      ? descriptors?.find((item) => item.name === baseStage?.agent?.provider) ?? null
      : null
  );
  const fastOption = fastProvider ? providerFastOption(fastProvider) : null;
  const fastEnabled = agent.fast ?? inheritedFast ?? false;
  const inheritedPrefixes = baseStage?.agent?.approved_command_prefixes ?? [];
  const effort = provider
    ? providerEffortOption(provider, agent.model ?? provider.primary_field.default)
    : null;
  const inheritedEffortProviders = (descriptors ?? []).filter(
    (descriptor) => !agent.model || descriptor.primary_field.values.includes(agent.model),
  );
  const modelChoices = provider
    ? models
    : Array.from(
        new Map(
          (descriptors ?? [])
            .flatMap((descriptor) =>
              modelPickerOptions(descriptor).map((item) => ({
                ...item,
                label: `${descriptor.label} · ${item.label}`,
              })),
            )
            .map((item) => [item.value, item]),
        ).values(),
      );
  const effortChoices = provider && effort
    ? effort.field.values.map((value) => ({
        value,
        label: optionLabel(effort.field, value),
      }))
    : Array.from(
        new Map(
          inheritedEffortProviders
            .flatMap((descriptor) => {
              const option = providerEffortOption(
                descriptor,
                agent.model ?? descriptor.primary_field.default,
              );
              return option?.field.values.map((value) => ({
                value,
                label: optionLabel(option.field, value),
              })) ?? [];
            })
            .map((item) => [item.value, item]),
        ).values(),
      );
  const prefixControl = !isBaseStage && agent.approved_command_prefixes == null ? (
    <div className="loop-command-inherit">
      <span>{inheritedPrefixes.length ? `${inheritedPrefixes.length} inherited` : "No inherited prefixes"}</span>
      <button type="button" onClick={() => onPatch({ agent: { ...agent, approved_command_prefixes: [...inheritedPrefixes] } })}>Change</button>
    </div>
  ) : (
    <>
      <CommandPrefixEditor
        key={stage.id}
        prefixes={agent.approved_command_prefixes ?? []}
        compact={stageEditor}
        onChange={(approved_command_prefixes) => onPatch({ agent: { ...agent, approved_command_prefixes } })}
      />
      {!isBaseStage && <button type="button" className="loop-command-reset" onClick={() => onPatch({ agent: { ...agent, approved_command_prefixes: null } })}>Reset to inherit</button>}
    </>
  );
  if (stageEditor) {
    const permissionHint = agent.permissions === "write"
      ? stage.kind === "pr" ? "commits in the worktree · allowed commands & prefixes skip prompts" : "may modify the workspace · allowed commands & prefixes skip prompts"
      : agent.permissions === "read" ? "inspects the workspace · allowed commands & prefixes skip prompts" : "inherits the run posture · allowed commands & prefixes skip prompts";
    const sessionHint = stage.kind === "pr" ? "a fresh agent · commits & pushes only" : agent.session === "reuse" ? "resumes the same agent after change requests" : "a new agent per stage occurrence · independent judgment";
    return <>
      <div className="stage-execution-selects">
        <label><span>provider:</span><select aria-label="Provider" value={agent.provider ?? ""} onChange={(event) => onPatch({ agent: { ...agent, provider: event.target.value || null, model: null, effort: null, fast: null } })}><option value="">↑ inherit</option>{descriptors?.map((item) => <option key={item.name} value={item.name}>{item.label}</option>)}</select></label>
        <label><span>model:</span><select aria-label="Model" value={agent.model ?? ""} disabled={!modelChoices.length && !agent.model} onChange={(event) => onPatch({ agent: { ...agent, model: event.target.value || null, effort: null } })}><option value="">↑ inherit</option>{agent.model && !modelChoices.some((item) => item.value === agent.model) && <option value={agent.model}>{agent.model}</option>}{modelChoices.map((item) => <option key={item.value} value={item.value}>{item.label}</option>)}</select></label>
        <label><span>effort:</span><select aria-label="Effort" value={agent.effort ?? ""} disabled={!effortChoices.length && !agent.effort} onChange={(event) => onPatch({ agent: { ...agent, effort: event.target.value || null } })}><option value="">↑ inherit</option>{agent.effort && !effortChoices.some((item) => item.value === agent.effort) && <option value={agent.effort}>{agent.effort}</option>}{effortChoices.map((item) => <option key={item.value} value={item.value}>{item.label}</option>)}</select></label>
      </div>
      {fastOption && <div className="stage-posture-card">
        <span><strong>Fast mode</strong><small>{agent.fast == null ? "inherits the run setting" : agent.fast ? "faster responses · increased usage" : "normal speed and usage"}</small></span>
        <div className="stage-fast-control">
          <label className="pm-fast-toggle">
            <input type="checkbox" aria-label="Fast mode" checked={fastEnabled} onChange={(event) => onPatch({ agent: { ...agent, fast: event.target.checked } })} />
            <span aria-hidden />
            Fast
          </label>
          {agent.fast != null && <button type="button" onClick={() => onPatch({ agent: { ...agent, fast: null } })}>inherit</button>}
        </div>
      </div>}
      <div className="stage-posture-card">
        <span><strong>Session</strong><small>{sessionHint}</small></span>
        <select aria-label="Session" value={agent.session} onChange={(event) => onPatch({ agent: { ...agent, session: event.target.value as "reuse" | "fresh" } })}><option value="reuse">reuse</option><option value="fresh">fresh</option></select>
      </div>
      <div className="stage-permission-card">
        <div><span><strong>Permissions</strong><small>{permissionHint}</small></span><select aria-label="Permissions" value={agent.permissions ?? ""} onChange={(event) => onPatch({ agent: { ...agent, permissions: event.target.value ? event.target.value as "read" | "write" : null } })}><option value="">↑ inherit</option><option value="write">✎ write</option><option value="read">◉ read-only</option></select></div>
        {prefixControl}
        <small><LockIcon size={10} /> Literal command prefixes only. Shell composition or chaining may still ask.</small>
      </div>
    </>;
  }
  return (
    <>
      <InspectorField label="Provider & model" hint="each setting can inherit the parent">
        <div className="loop-inspector-grid">
          <select value={agent.provider ?? ""} onChange={(event) => onPatch({ agent: { ...agent, provider: event.target.value || null, model: null, effort: null, fast: null } })}><option value="">inherit provider</option>{descriptors?.map((item) => <option key={item.name} value={item.name}>{item.label}</option>)}</select>
          <select value={agent.model ?? ""} disabled={!modelChoices.length && !agent.model} onChange={(event) => onPatch({ agent: { ...agent, model: event.target.value || null, effort: null } })}>
            <option value="">inherit model</option>
            {agent.model && !modelChoices.some((item) => item.value === agent.model) && <option value={agent.model}>{agent.model}</option>}
            {modelChoices.map((item) => <option key={item.value} value={item.value}>{item.label}</option>)}
          </select>
        </div>
      </InspectorField>
      <PostureRow label="Effort" hint={agent.effort ? "Overrides the parent run effort" : "Uses the parent run effort"}>
        <select value={agent.effort ?? ""} disabled={!effortChoices.length && !agent.effort} onChange={(event) => onPatch({ agent: { ...agent, effort: event.target.value || null } })}>
          <option value="">inherit</option>
          {agent.effort && !effortChoices.some((item) => item.value === agent.effort) && <option value={agent.effort}>{agent.effort}</option>}
          {effortChoices.map((item) => <option key={item.value} value={item.value}>{item.label}</option>)}
        </select>
      </PostureRow>
      {fastOption && <PostureRow label="Fast mode" hint={agent.fast == null ? "Uses the parent run setting" : agent.fast ? "Faster responses with increased usage" : "Uses normal speed and usage"}>
        <div className="stage-fast-control">
          <label className="pm-fast-toggle">
            <input type="checkbox" aria-label="Fast mode" checked={fastEnabled} onChange={(event) => onPatch({ agent: { ...agent, fast: event.target.checked } })} />
            <span aria-hidden />
            Fast
          </label>
          {agent.fast != null && <button type="button" onClick={() => onPatch({ agent: { ...agent, fast: null } })}>inherit</button>}
        </div>
      </PostureRow>}
      <PostureRow label="Session policy" hint={agent.session === "reuse" ? "Resume the same agent after change requests" : "Fresh agent for each stage occurrence"}>
        <select value={agent.session} onChange={(event) => onPatch({ agent: { ...agent, session: event.target.value as "reuse" | "fresh" } })}><option value="reuse">reuse</option><option value="fresh">fresh</option></select>
      </PostureRow>
      <PostureRow label="Permissions" hint={agent.permissions === "write" ? "May modify the run workspace" : agent.permissions === "read" ? "Inspects but cannot mutate the workspace" : "Uses the parent run permissions"}>
        <select value={agent.permissions ?? ""} onChange={(event) => onPatch({ agent: { ...agent, permissions: event.target.value ? event.target.value as "read" | "write" : null } })}><option value="">inherit</option><option value="write">write</option><option value="read">read-only</option></select>
      </PostureRow>
      <InspectorField
        label="Approved command prefixes"
        hint={isBaseStage ? "loop default for later stages" : agent.approved_command_prefixes == null ? "inherits the loop default" : "stage override"}
      >
        {prefixControl}
      </InspectorField>
      <div className="loop-inspector-note"><LockIcon size={13} /> Review and security stages default to read-only.</div>
    </>
  );
}

function OutcomePanel({ stage, stages, onPatch }: { stage: LoopStepDefinition; stages: LoopStepDefinition[]; onPatch: (patch: Partial<LoopStepDefinition>) => void }) {
  const gate = stage.review_gate ?? { mode: "automatic" as const, max_passes: 3, locked: false };
  const hasReturnEdge = stage.kind === "agent_review" && Boolean(stage.transitions.changes_requested);
  return (
    <InspectorField label="Outcome transitions">
      <div className="loop-outcome-list">
        {OUTCOMES.map((outcome) => (
          <label key={outcome.key} className={`loop-outcome-row ${outcome.key}`}><span><i />{outcome.label}</span><ChevronRightIcon size={12} /><select value={stage.transitions[outcome.key] ?? ""} onChange={(event) => onPatch({ transitions: { ...stage.transitions, [outcome.key]: event.target.value || null } })}><option value="">—</option>{stages.filter((item) => item.id !== stage.id).map((item) => <option key={item.id} value={item.id}>{item.name}</option>)}<option value="pause">pause (user blocker)</option><option value="fail">fail run</option><option value="complete">complete run</option></select></label>
        ))}
      </div>
      {hasReturnEdge && (
        <div className="loop-gate-editor">
          <strong>Who closes the loop</strong>
          <button type="button" className={gate.mode === "automatic" ? "active" : ""} onClick={() => onPatch({ review_gate: { ...gate, mode: "automatic" } })}>
            <i /><span><b>⟲ Automatic</b><small>All findings return to implementation. The run pauses when the pass budget is spent.</small></span>
            <select value={gate.max_passes} onClick={(event) => event.stopPropagation()} onChange={(event) => onPatch({ review_gate: { ...gate, mode: "automatic", max_passes: Number(event.target.value) } })}>{[1, 2, 3, 4, 5].map((value) => <option value={value} key={value}>max {value}</option>)}</select>
          </button>
          <button type="button" className={gate.mode === "human_check" ? "active" : ""} onClick={() => onPatch({ review_gate: { ...gate, mode: "human_check" } })}>
            <i /><span><b>⚉ Human check</b><small>Pause so you can enforce findings, add an instruction, or approve as is.</small></span>
          </button>
          <label><input type="checkbox" checked={gate.locked} onChange={(event) => onPatch({ review_gate: { ...gate, locked: event.target.checked } })} /> lock · runs cannot override this gate in setup</label>
        </div>
      )}
      <div className="loop-inspector-note"><BranchIcon size={13} /> Cycles require an explicit changes-requested edge and bounded retries.</div>
    </InspectorField>
  );
}

function AdvancedPanel({ stage, onPatch }: { stage: LoopStepDefinition; onPatch: (patch: Partial<LoopStepDefinition>) => void }) {
  return (
    <div className="loop-advanced-panel">
      <InspectorField label="Retry limit"><input type="number" min={1} max={20} value={stage.retry.max_attempts} onChange={(event) => onPatch({ retry: { ...stage.retry, max_attempts: Number(event.target.value) } })} /></InspectorField>
      <InspectorField label="Timeout · minutes"><input type="number" min={1} max={1440} value={stage.retry.timeout_minutes} onChange={(event) => onPatch({ retry: { ...stage.retry, timeout_minutes: Number(event.target.value) } })} /></InspectorField>
    </div>
  );
}

function InspectorField({ label, hint, action, children }: { label: string; hint?: string; action?: React.ReactNode; children: React.ReactNode }) {
  return <section className="loop-inspector-field"><header><strong>{label}</strong>{hint && <small>{hint}</small>}{action}</header>{children}</section>;
}

function PostureRow({ label, hint, children }: { label: string; hint: string; children: React.ReactNode }) {
  return <div className="loop-posture-row"><span><strong>{label}</strong><small>{hint}</small></span>{children}</div>;
}

type StagePreset = "implementation" | "validation" | "code-review" | "security-review" | "approval" | "create-pr" | "custom";

const STAGE_PRESETS: Array<{ id: StagePreset; name: string; kind: LoopStepKind; hint: string }> = [
  { id: "implementation", name: "Implementation", kind: "agent_task", hint: "Writes code in the run workspace" },
  { id: "validation", name: "Validation / check", kind: "deterministic_check", hint: "Runs a configured local check" },
  { id: "code-review", name: "Code review", kind: "agent_review", hint: "Fresh read-only reviewer" },
  { id: "security-review", name: "Security review", kind: "agent_review", hint: "Independent security pass" },
  { id: "approval", name: "Human approval", kind: "user_approval", hint: "Waits for your decision" },
  { id: "create-pr", name: "Create PR", kind: "pr", hint: "Commits, pushes, and opens or updates the PR" },
  { id: "custom", name: "Custom agent stage", kind: "agent_task", hint: "Custom instructions and posture" },
];

function AddStageSlot({ index, open, definitions, onToggle, onAdd }: { index: number; open: boolean; definitions: StageDefinition[]; onToggle: () => void; onAdd: (index: number, definition?: StageDefinition) => void }) {
  const [query, setQuery] = useState("");
  useEffect(() => {
    if (!open) setQuery("");
  }, [open]);
  const needle = query.trim().toLowerCase();
  const visible = definitions.filter((definition) => !needle || `${definition.name} ${definition.description} ${stageKindLabel(definition.stage.kind)}`.toLowerCase().includes(needle));
  return <div className="loop-add-stage">
    <button type="button" aria-expanded={open} onClick={onToggle}>+ Add stage</button>
    {open && <div className="loop-add-menu" role="dialog" aria-label="Add stage">
      <label className="loop-add-menu-search"><SearchIcon size={12} /><input autoFocus value={query} onChange={(event) => setQuery(event.target.value)} placeholder="Search stages…" /></label>
      {(["builtin", "library"] as const).map((scope) => {
        const rows = visible.filter((definition) => definition.scope === scope);
        return rows.length ? <section key={scope}><header>{scope === "builtin" ? "Built-in stages" : "Reusable stages"}</header>{rows.map((definition) => <button key={`${scope}:${definition.id}`} data-stage-kind={definition.stage.kind} onClick={() => onAdd(index, definition)}><i>{stageIcon(definition.stage)}</i><span><strong>{definition.name}</strong><small>{definition.description || stageKindLabel(definition.stage.kind)}</small></span>{scope === "library" && <em>library</em>}</button>)}</section> : null;
      })}
      {visible.length === 0 && <p>No stages match “{query}”.</p>}
      <button className="loop-add-menu-blank" data-stage-kind="agent_task" onClick={() => onAdd(index)}><i>+</i><span><strong>Start blank</strong><small>Create a stage in this loop</small></span></button>
    </div>}
  </div>;
}

function StageStrip({ definition, subtle = false }: { definition: LoopDefinition; subtle?: boolean }) {
  const gated = definition.stages.find((stage) => stage.review_gate && stage.transitions.changes_requested);
  const target = definition.stages.find((stage) => stage.id === gated?.transitions.changes_requested);
  return <div className={"loop-stage-strip" + (subtle ? " subtle" : "")}>{definition.stages.map((stage, index) => <span key={stage.id} className="loop-stage-strip-unit"><span className="loop-stage-chip" data-stage-kind={stage.kind}><i>{stageIcon(stage)}</i><b>{stage.name}</b>{!subtle && stage.agent && <small>{stage.agent.permissions ?? "inherit"}</small>}</span>{index < definition.stages.length - 1 && <ChevronRightIcon size={11} />}</span>)}{gated?.review_gate && target && <span className="loop-gate-edge"><ReturnIcon size={10} /> {gated.name} → {target.name}<em className={gated.review_gate.mode === "human_check" ? "warn" : ""}>{gateLabel(gated.review_gate)}</em></span>}</div>;
}

function ScopeBadge({ definition }: { definition: LoopDefinition }) {
  return <em className={`loop-scope-badge ${definition.scope}`}>{definition.scope === "builtin" ? "built-in" : definition.scope}</em>;
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

function stageDefinitionRoot(
  definition: StageDefinition,
  fallback?: string | null,
): string | null | undefined {
  return definition.catalog_root !== undefined ? definition.catalog_root : fallback;
}

function editorSeed(definition: LoopDefinition, duplicate = false, scope: LoopSaveScope = "library"): EditorSeed {
  if (definition.scope === scope && !duplicate) {
    return { definition: structuredClone(definition), expectedRevision: definition.revision };
  }
  if (scope === "work" && !duplicate) {
    return {
      definition: {
        ...structuredClone(definition),
        scope: "work",
        revision: "",
        is_default: false,
        forked_from: definition.id,
        errors: [],
      },
      expectedRevision: null,
    };
  }
  const name = `${definition.name}${duplicate ? " copy" : ""}`;
  return {
    definition: {
      ...structuredClone(definition),
      // Id is the slug of the name at creation, then immutable (the editor
      // keeps them in sync only while unsaved). This is what stops an id
      // from drifting into a fork artifact like `code-review-repository`.
      id: slugify(name) || definition.id,
      name,
      scope,
      revision: "",
      is_default: false,
      forked_from: definition.id,
      errors: [],
    },
    expectedRevision: null,
  };
}

function newLoopSeed(scope: LoopSaveScope = "library"): EditorSeed {
  const implementation = stageFromPreset("implementation", []);
  const approval = stageFromPreset("approval", [implementation]);
  implementation.transitions.pass = approval.id;
  return {
    definition: {
      id: "untitled-loop",
      name: "Untitled loop",
      description: "",
      scope,
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
  const pr = meta.kind === "pr";
  return {
    id,
    name: meta.name,
    kind: meta.kind,
    instructions: approval || check ? "" : presetInstructions(preset),
    context: approval ? [] : pr ? [
      { kind: "workspace_diff", required: true, paths: [], step: null, ref: null },
      { kind: "changed_files", required: true, paths: [], step: null, ref: null },
      { kind: "previous_report", required: true, paths: [], step: null, ref: null },
    ] : review ? [
      { kind: "target", required: true, paths: [], step: null, ref: null },
      { kind: "workspace_diff", required: true, paths: [], step: null, ref: null },
    ] : [{ kind: "target", required: true, paths: [], step: null, ref: null }],
    agent: approval || check ? null : { session: "fresh", permissions: review ? "read" : pr ? "write" : null, provider: null, model: null, effort: null, fast: null, approved_command_prefixes: pr ? ["git add", "git commit"] : null },
    report_contract: review ? "review" : check ? "check" : pr ? "pr" : "implementation",
    retry: { max_attempts: check ? 1 : 2, timeout_minutes: check ? 10 : 20 },
    transitions: approval ? {} : { pass: "approval", changes_requested: review ? "implementation" : null, blocked_user: "pause", failed: "fail" },
    check_adapter: check ? "command" : null,
    check_command: [],
    note_required: approval || check ? null : false,
    review_gate: review ? { mode: "automatic", max_passes: 3, locked: false } : null,
    pr_config: pr ? defaultPrConfig() : null,
  };
}

function stageFromDefinition(definition: StageDefinition, existing: LoopStepDefinition[]): LoopStepDefinition {
  const id = uniqueId(definition.stage.id || definition.id, existing.map((stage) => stage.id));
  return {
    ...structuredClone(definition.stage),
    id,
    name: definition.name,
    transitions: {},
    stage_ref: { definition_id: definition.id, revision: definition.revision },
    overrides: null,
  };
}

function stageEditorSeed(definition: StageDefinition, duplicate: boolean): StageEditorSeed {
  if (definition.scope === "library" && !duplicate) {
    return { definition: structuredClone(definition), expectedRevision: definition.revision, source: null };
  }
  const name = `${definition.name}${duplicate ? " copy" : ""}`;
  const id = slugify(name) || definition.id;
  return {
    definition: {
      ...structuredClone(definition),
      id,
      name,
      scope: "library",
      revision: "",
      errors: [],
      forked_from: definition.id,
      used_by: [],
      stage: { ...structuredClone(definition.stage), id, name: `${definition.name}${duplicate ? " copy" : ""}` },
    },
    expectedRevision: null,
    source: definition.scope === "builtin" && !duplicate ? structuredClone(definition) : null,
  };
}

function newStageSeed(): StageEditorSeed {
  const stage = stageFromPreset("custom", []);
  stage.name = "Untitled stage";
  stage.transitions = {};
  return {
    definition: {
      id: "untitled-stage",
      name: "Untitled stage",
      description: "",
      scope: "library",
      revision: "",
      valid: true,
      errors: [],
      forked_from: null,
      used_by: [],
      stage,
      outcomes: ["pass", "blocked_user", "failed"],
    },
    expectedRevision: null,
    source: null,
  };
}

function newLocalStageSeed(existing: LoopStepDefinition[]): StageEditorSeed {
  const stage = stageFromPreset("custom", existing);
  stage.name = "";
  stage.context = [];
  stage.transitions = {};
  return {
    definition: {
      id: stage.id,
      name: "",
      description: "",
      scope: "library",
      revision: "",
      valid: true,
      errors: [],
      forked_from: null,
      used_by: [],
      stage,
      outcomes: ["pass", "changes_requested", "failed"],
    },
    expectedRevision: null,
    source: null,
  };
}

function withoutWiring(patch: Partial<LoopStepDefinition>): StageOverrides {
  return {
    ...(patch.name !== undefined ? { name: patch.name } : {}),
    ...(patch.instructions !== undefined ? { instructions: patch.instructions } : {}),
    ...(patch.context !== undefined ? { context: patch.context } : {}),
    ...(patch.agent !== undefined ? { agent: patch.agent } : {}),
    ...(patch.report_contract !== undefined ? { report_contract: patch.report_contract } : {}),
    ...(patch.retry !== undefined ? { retry: patch.retry } : {}),
    ...(patch.check_adapter !== undefined ? { check_adapter: patch.check_adapter } : {}),
    ...(patch.check_command !== undefined ? { check_command: patch.check_command } : {}),
    ...(patch.note_required !== undefined ? { note_required: patch.note_required } : {}),
    ...(patch.review_gate !== undefined ? { review_gate: patch.review_gate } : {}),
    ...(patch.pr_config !== undefined ? { pr_config: patch.pr_config } : {}),
  };
}

function defaultPrConfig(): PrStageConfig {
  return {
    name_template: "{goal} - {work-id}",
    description_mode: "automatic",
    description_instructions: "",
    manual_body: "",
    status: "draft",
    base_branch: "master",
    branch_name: null,
  };
}

function stageKindPatch(kind: LoopStepKind): Partial<LoopStepDefinition> {
  const preset: StagePreset = kind === "agent_review" ? "code-review" : kind === "deterministic_check" ? "validation" : kind === "user_approval" ? "approval" : kind === "pr" ? "create-pr" : "custom";
  const defaults = stageFromPreset(preset, []);
  return {
    kind,
    instructions: defaults.instructions,
    context: defaults.context,
    agent: defaults.agent,
    report_contract: defaults.report_contract,
    retry: defaults.retry,
    check_adapter: defaults.check_adapter,
    check_command: defaults.check_command,
    note_required: defaults.note_required,
    review_gate: defaults.review_gate,
    pr_config: defaults.pr_config,
  };
}

function stageKindOutcomes(kind: LoopStepKind): LoopOutcome[] {
  if (kind === "agent_task") return ["pass", "blocked_user", "failed"];
  if (kind === "deterministic_check") return ["pass", "changes_requested", "failed"];
  if (kind === "user_approval") return ["pass", "changes_requested"];
  return ["pass", "changes_requested", "blocked_user", "failed"];
}

function gateLabel(gate: NonNullable<LoopStepDefinition["review_gate"]>): string {
  return gate.mode === "human_check" ? "⚉ human check" : `⟲ auto · max ${gate.max_passes}`;
}

function presetInstructions(preset: StagePreset): string {
  if (preset === "code-review") return "Review the implementation diff against the target and its acceptance criteria.\n\nReturn pass only when the result is ready for human approval; otherwise return changes requested with actionable findings and file references.\n";
  if (preset === "security-review") return "Perform an independent, read-only security review of the run workspace and diff.\n\nCheck authentication, input validation, secrets, and data exposure. Cite every finding.\n";
  if (preset === "create-pr") return "Commit the worktree, push its branch, and create or update the pull request. Never bypass hooks or force-push; report hook failures verbatim.\n";
  return "Complete this stage against the target and report changes, validation evidence, divergences, skipped scope, and blockers.\n";
}

function uniqueId(base: string, ids: string[]): string {
  if (!ids.includes(base)) return base;
  let number = 2;
  while (ids.includes(`${base}-${number}`)) number += 1;
  return `${base}-${number}`;
}

function stageKindLabel(kind: LoopStepKind): string {
  if (kind === "agent_task") return "Agent task";
  if (kind === "agent_review") return "Agent review";
  if (kind === "deterministic_check") return "Check";
  if (kind === "pr") return "Create PR";
  return "Approval";
}

function stageIcon(stage: Pick<LoopStepDefinition, "id" | "name" | "kind">) {
  if (stage.id.includes("security") || stage.name.toLowerCase().includes("security")) return <ShieldIcon size={14} />;
  if (stage.kind === "agent_review") return <EyeIcon size={14} />;
  if (stage.kind === "deterministic_check") return <FlaskIcon size={14} />;
  if (stage.kind === "user_approval") return <UserCheckIcon size={14} />;
  if (stage.kind === "pr") return <span aria-hidden>⇱</span>;
  return <PersonIcon size={14} />;
}

function contextIcon(kind: LoopContextKind) {
  if (kind === "workspace_diff" || kind === "artifact_dependencies") return <BranchIcon size={13} />;
  if (kind === "folder") return <FolderIcon size={13} />;
  if (kind === "note") return <EditIcon size={13} />;
  if (kind === "previous_report") return <ReturnIcon size={13} />;
  if (kind === "target" || kind === "plan_index" || kind === "files" || kind === "changed_files") return <DocIcon size={13} />;
  return <SlidersIcon size={13} />;
}

function slugify(value: string): string {
  return value.toLowerCase().replace(/[^a-z0-9]+/g, "-").replace(/^-|-$/g, "");
}

function relativeContextPath(rootPath: string, selectedPath: string): string | null {
  const root = rootPath.replace(/\\/g, "/").replace(/\/+$/, "");
  const selected = selectedPath.replace(/\\/g, "/");
  if (selected === root) return ".";
  return selected.startsWith(`${root}/`)
    ? selected.slice(root.length + 1)
    : null;
}
