import { useEffect, useMemo, useRef, useState } from "react";

import {
  type ArtifactSummary,
  type ChatSummary,
  type LoopDefinition,
  type LoopStatus,
  type ProviderDescriptor,
  type ProjectSummary,
  type WorkDetail,
  type WorkLoopRun,
  acceptWorkLoopRun,
  createWorkLoopRunPullRequest,
  getWorkLoopRun,
  listLoopDefinitions,
  listWorkLoopRuns,
  requestWorkLoopRunChanges,
  rerunWorkLoopRun,
  resumeWorkLoopRun,
  startWorkLoopRun,
} from "./api";
import { ChatComposer } from "./Chat";
import { FolderPickerDialog } from "./FolderPickerDialog";
import {
  AlertIcon,
  BranchIcon,
  ChatIcon,
  CheckIcon,
  DocIcon,
  EditIcon,
  FolderIcon,
  LoopIcon,
  PlayIcon,
  ReturnIcon,
} from "./Icons";
import {
  LoopDefinitionPickerDialog,
  LoopDefinitionSummaryCard,
  LoopStructureEditor,
} from "./LoopUI";
import {
  LoopRunStageSpine,
  LoopRunStageTimeline,
} from "./LoopRunView";
import { PaneResizeHandle } from "./PaneResizeHandle";
import { PlanningAgentControls } from "./PlanningMode";
import { type LoopStartSeed, loopStartStorageKey } from "./loopSetup";
import { type PlanningAgentConfig } from "./planningSetup";
import {
  optionFieldForModel,
  optionLabel,
  providerEffortOption,
  providerOptionsPayload,
  providerPermissionOption,
  useProviderDescriptors,
} from "./providerDescriptors";
import { ShellTopbar } from "./ShellTopbar";
import {
  useLayoutStore,
  WORK_RAIL_MAX,
  WORK_RAIL_MIN,
} from "./state/layout";
import { editorUrl, useSettingsStore } from "./state/settings";

type Props = {
  artifacts: ArtifactSummary[];
  chats: ChatSummary[];
  initialSeed: LoopStartSeed | null;
  onOpenChat: (chatSlug: string) => void;
  onSearch: () => void;
  project: ProjectSummary | null;
  work: WorkDetail;
};

const ACTIVE_RUN_STATUSES = new Set<LoopStatus>([
  "pending",
  "running",
  "waiting_report",
  "assessing",
  "needs_agent",
]);

export function LoopMode({
  artifacts,
  chats,
  initialSeed,
  onOpenChat,
  onSearch,
  project,
  work,
}: Props) {
  const [goal, setGoal] = useState(
    initialSeed?.goal || work.description || work.name,
  );
  const [folder, setFolder] = useState(initialSeed?.folder ?? "");
  const [definitions, setDefinitions] = useState<LoopDefinition[] | null>(null);
  const [selectedDefinition, setSelectedDefinition] =
    useState<LoopDefinition | null>(null);
  const [agentConfig, setAgentConfig] = useState<PlanningAgentConfig | null>(null);
  const [advanced, setAdvanced] = useState(false);
  const [folderPickerOpen, setFolderPickerOpen] = useState(false);
  const [selectorOpen, setSelectorOpen] = useState(false);
  const [editorOpen, setEditorOpen] = useState(initialSeed?.createDefinition ?? false);
  const [creatingDefinition, setCreatingDefinition] = useState(
    initialSeed?.createDefinition ?? false,
  );
  const [runs, setRuns] = useState<WorkLoopRun[]>([]);
  const [selectedRunId, setSelectedRunId] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [discussRun, setDiscussRun] = useState<WorkLoopRun | null>(null);
  const railWidth = useLayoutStore((state) => state.workRailWidth);
  const setRailWidth = useLayoutStore((state) => state.setWorkRailWidth);
  const editor = useSettingsStore((state) => state.editor);
  const { descriptors } = useProviderDescriptors();
  const requestSequence = useRef(0);

  const activeRun = useMemo(
    () => runs.find((run) => run.id === selectedRunId) ?? runs[0] ?? null,
    [runs, selectedRunId],
  );
  const pullRequests = artifacts.filter((artifact) => artifact.type === "pr");
  const provider = descriptors?.find((item) => item.name === agentConfig?.provider) ?? null;

  useEffect(() => {
    let cancelled = false;
    listLoopDefinitions(work.slug, folder)
      .then((rows) => {
        if (cancelled) return;
        setDefinitions(rows);
        setSelectedDefinition((current) =>
          rows.find((row) => row.id === current?.id && row.valid) ??
          rows.find((row) => row.id === initialSeed?.definitionId && row.valid) ??
          rows.find((row) => row.is_default && row.valid) ??
          rows.find((row) => row.valid) ??
          null,
        );
      })
      .catch((reason) => {
        if (!cancelled) setError(errorMessage(reason));
      });
    listWorkLoopRuns(work.slug)
      .then((rows) => {
        if (cancelled) return;
        const ordered = orderRuns(rows);
        setRuns(ordered);
        setSelectedRunId(ordered[0]?.id ?? null);
      })
      .catch(() => {
        // The generic run endpoint is the deliberate backend follow-up.
        // Setup remains usable while that endpoint is absent.
      });
    return () => {
      cancelled = true;
    };
  }, [folder, initialSeed?.definitionId, work.slug]);

  useEffect(() => {
    if (!activeRun || !ACTIVE_RUN_STATUSES.has(activeRun.status)) return;
    const runId = activeRun.id;
    const timer = window.setInterval(() => {
      const request = ++requestSequence.current;
      getWorkLoopRun(work.slug, runId)
        .then((next) => {
          if (request !== requestSequence.current) return;
          replaceRun(next);
        })
        .catch((reason) => setError(errorMessage(reason)));
    }, 2500);
    return () => window.clearInterval(timer);
  }, [activeRun?.id, activeRun?.status, work.slug]);

  function replaceRun(next: WorkLoopRun) {
    setRuns((current) => orderRuns([next, ...current.filter((run) => run.id !== next.id)]));
    setSelectedRunId(next.id);
  }

  function changeAdvancedOption(key: string, value: string) {
    if (!agentConfig) return;
    setAgentConfig({
      ...agentConfig,
      options: { ...agentConfig.options, [key]: value },
    });
  }

  async function start() {
    if (!selectedDefinition || !agentConfig || !goal.trim() || !folder.trim()) return;
    setBusy(true);
    setError(null);
    try {
      const created = await startWorkLoopRun(work.slug, {
        goal: goal.trim(),
        root_path: folder.trim(),
        loop_definition_id: selectedDefinition.id,
        loop_revision: selectedDefinition.revision,
        provider: agentConfig.provider,
        model: agentConfig.model,
        options: providerOptionsPayload(
          provider ?? undefined,
          agentConfig.model,
          agentConfig.options,
        ),
      });
      sessionStorage.removeItem(loopStartStorageKey(work.slug));
      replaceRun(created);
    } catch (reason) {
      setError(errorMessage(reason));
    } finally {
      setBusy(false);
    }
  }

  async function act(action: () => Promise<WorkLoopRun>) {
    setBusy(true);
    setError(null);
    try {
      replaceRun(await action());
    } catch (reason) {
      setError(errorMessage(reason));
    } finally {
      setBusy(false);
    }
  }

  const style = {
    ["--loop-rail-width" as string]: `${railWidth}px`,
    ["--shell-left-width" as string]: `${railWidth}px`,
    ...(project
      ? {
          ["--proj-h" as string]: String(project.color),
        }
      : {}),
  };

  return (
    <div className="loop-mode-screen" style={style}>
      <ShellTopbar
        crumbs={[
          ...(project
            ? [{
                href: `/projects/${project.slug}`,
                hue: project.color,
                label: project.name,
              }]
            : []),
          { href: `/works/${work.slug}?mode=manual`, label: work.slug },
          { label: "loop" },
        ]}
        onSearch={onSearch}
      />
      <div className="loop-mode-shell">
        <LoopModeRail
          work={work}
          goal={goal}
          folder={folder}
          definition={selectedDefinition}
          runs={runs}
          selectedRunId={activeRun?.id ?? null}
          pullRequests={pullRequests}
          chats={chats}
          onRun={setSelectedRunId}
          onChat={onOpenChat}
        />
        <PaneResizeHandle
          defaultValue={280}
          edge="right"
          label="Resize loop rail"
          min={WORK_RAIL_MIN}
          max={WORK_RAIL_MAX}
          value={railWidth}
          onChange={setRailWidth}
        />
        <main className="loop-mode-main themed-scrollbar">
          {activeRun ? (
            <LoopModeRunView
              run={activeRun}
              busy={busy}
              error={error}
              onResume={(note) => act(() => resumeWorkLoopRun(work.slug, activeRun.id, note))}
              onRequestChanges={(note) => act(() => requestWorkLoopRunChanges(work.slug, activeRun.id, note))}
              onAccept={() => act(() => acceptWorkLoopRun(work.slug, activeRun.id))}
              onCreatePullRequest={() => act(() => createWorkLoopRunPullRequest(work.slug, activeRun.id))}
              onOpenEditor={() => {
                if (activeRun.workspace_path) window.location.href = editorUrl(editor, activeRun.workspace_path);
              }}
              onDiscuss={() => setDiscussRun(activeRun)}
              onRerun={() => act(() => rerunWorkLoopRun(work.slug, activeRun.id))}
            />
          ) : (
            <LoopModeSetup
              goal={goal}
              folder={folder}
              definitions={definitions}
              definition={selectedDefinition}
              agentConfig={agentConfig}
              advanced={advanced}
              provider={provider}
              busy={busy}
              error={error}
              onGoal={setGoal}
              onAgentConfig={setAgentConfig}
              onAdvanced={() => setAdvanced((value) => !value)}
              onAdvancedOption={changeAdvancedOption}
              onChooseFolder={() => setFolderPickerOpen(true)}
              onChangeLoop={() => setSelectorOpen(true)}
              onEditLoop={() => {
                if (!selectedDefinition) return;
                setCreatingDefinition(false);
                setEditorOpen(true);
              }}
              onStart={() => void start()}
            />
          )}
        </main>
      </div>

      {folderPickerOpen && (
        <FolderPickerDialog
          initialPath={folder || null}
          onCancel={() => setFolderPickerOpen(false)}
          onPick={(path) => {
            setFolder(path);
            setFolderPickerOpen(false);
          }}
        />
      )}
      {selectorOpen && (
        <LoopDefinitionPickerDialog
          workSlug={work.slug}
          rootPath={folder}
          goal={goal}
          initialDefinitionId={selectedDefinition?.id}
          onClose={() => setSelectorOpen(false)}
          onSelect={async (definition) => {
            setSelectedDefinition(definition);
            setSelectorOpen(false);
          }}
        />
      )}
      {editorOpen && (creatingDefinition || selectedDefinition) && (
        <LoopStructureEditor
          workSlug={work.slug}
          rootPath={folder}
          definition={creatingDefinition ? undefined : selectedDefinition ?? undefined}
          onClose={() => setEditorOpen(false)}
          onSaved={(definition) => {
            setSelectedDefinition(definition);
            setCreatingDefinition(false);
            setEditorOpen(false);
            setDefinitions((current) => current
              ? [definition, ...current.filter((item) => item.id !== definition.id)]
              : [definition]);
          }}
        />
      )}
      {discussRun && (
        <ChatComposer
          projects={project ? [project] : []}
          works={[work]}
          presetGrounding={{ kind: "work", ref: work.slug }}
          presetWorkingDirectory={discussRun.workspace_path}
          hideGrounding
          onClose={() => setDiscussRun(null)}
          onStarted={(chat) => window.location.assign(`/chats/${chat.slug}`)}
        />
      )}
    </div>
  );
}

function LoopModeRail({
  work,
  goal,
  folder,
  definition,
  runs,
  selectedRunId,
  pullRequests,
  chats,
  onRun,
  onChat,
}: {
  work: WorkDetail;
  goal: string;
  folder: string;
  definition: LoopDefinition | null;
  runs: WorkLoopRun[];
  selectedRunId: string | null;
  pullRequests: ArtifactSummary[];
  chats: ChatSummary[];
  onRun: (runId: string) => void;
  onChat: (chatSlug: string) => void;
}) {
  return (
    <aside className="loop-mode-rail">
      <div className="loop-mode-label">
        <span className="loop-mode-pip"><LoopIcon size={13} /></span>
        <span>Loop</span>
        <em>no plan needed</em>
      </div>
      <div className="loop-mode-work">
        <strong>{work.name}</strong>
        <span>{work.slug} · {compactPath(folder)}</span>
      </div>
      <p className="loop-mode-goal">{goal || "Describe the goal before starting."}</p>

      <RailSection label="Loop">
        {definition ? (
          <div className="loop-mode-rail-definition">
            <strong><LoopIcon size={11} /> {definition.name}</strong>
            <div className="loop-mode-rail-strip">
              {definition.stages.map((stage) => (
                <i key={stage.id} data-stage-kind={stage.kind} title={stage.name} />
              ))}
            </div>
          </div>
        ) : <span className="loop-mode-rail-empty">Choose a loop to continue.</span>}
      </RailSection>

      {runs.length > 0 ? (
        <>
          <RailSection label="Runs" count={runs.length}>
            {runs.map((run) => (
              <button
                key={run.id}
                className={"loop-mode-rail-row" + (run.id === selectedRunId ? " active" : "")}
                onClick={() => onRun(run.id)}
              >
                <span>run {run.number}</span>
                <strong>{run.source_run_id ? `from run ${run.source_run_id}` : "initial"}</strong>
                <em className={statusTone(run.status)}>{statusLabel(run.status)}</em>
              </button>
            ))}
          </RailSection>
          <RailSection label="Pull requests" count={pullRequests.length}>
            {pullRequests.map((artifact) => (
              <a
                className="loop-mode-rail-row"
                href={artifact.url ?? undefined}
                key={artifact.slug}
                target={artifact.url ? "_blank" : undefined}
                rel={artifact.url ? "noreferrer" : undefined}
              >
                <span>PR</span><strong>{artifact.title}</strong><em className="good">{artifact.status}</em>
              </a>
            ))}
            {pullRequests.length === 0 && <span className="loop-mode-rail-empty">No pull requests yet.</span>}
          </RailSection>
          <RailSection label="Chats" count={chats.length} aux="+ C">
            {chats.map((chat) => (
              <button className="loop-mode-rail-row" key={chat.slug} onClick={() => onChat(chat.slug)}>
                <span className="chat"><ChatIcon size={11} /></span><strong>{chat.title}</strong>
              </button>
            ))}
            {chats.length === 0 && <span className="loop-mode-rail-empty">No chats yet.</span>}
          </RailSection>
        </>
      ) : (
        <span className="loop-mode-rail-foot">runs, PRs and chats appear here once the loop starts</span>
      )}
    </aside>
  );
}

function RailSection({
  label,
  count,
  aux,
  children,
}: {
  label: string;
  count?: number;
  aux?: string;
  children: React.ReactNode;
}) {
  return (
    <section className="loop-mode-rail-section">
      <header><span>{label}</span>{count !== undefined && <b>{count}</b>}{aux && <em>{aux}</em>}</header>
      {children}
    </section>
  );
}

function LoopModeSetup({
  goal,
  folder,
  definitions,
  definition,
  agentConfig,
  advanced,
  provider,
  busy,
  error,
  onGoal,
  onAgentConfig,
  onAdvanced,
  onAdvancedOption,
  onChooseFolder,
  onChangeLoop,
  onEditLoop,
  onStart,
}: {
  goal: string;
  folder: string;
  definitions: LoopDefinition[] | null;
  definition: LoopDefinition | null;
  agentConfig: PlanningAgentConfig | null;
  advanced: boolean;
  provider: ProviderDescriptor | null;
  busy: boolean;
  error: string | null;
  onGoal: (goal: string) => void;
  onAgentConfig: (config: PlanningAgentConfig) => void;
  onAdvanced: () => void;
  onAdvancedOption: (key: string, value: string) => void;
  onChooseFolder: () => void;
  onChangeLoop: () => void;
  onEditLoop: () => void;
  onStart: () => void;
}) {
  const effortKey = provider && agentConfig
    ? providerEffortOption(provider, agentConfig.model)?.key
    : null;
  const permissionKey = provider ? providerPermissionOption(provider)?.key : null;
  const advancedOptions = provider
    ? Object.entries(provider.options).filter(([key]) => key !== effortKey && key !== permissionKey)
    : [];

  return (
    <div className="loop-mode-setup">
      <header className="loop-mode-intro">
        <strong>Ready when you are</strong>
        <span>Review the goal, loop and parameters, then start. The run walks the stages until your approval.</span>
      </header>

      <SetupSection label="Goal">
        <textarea rows={2} value={goal} onChange={(event) => onGoal(event.target.value)} />
      </SetupSection>

      <SetupSection label="Loop">
        {definition ? (
          <LoopDefinitionSummaryCard definition={definition} />
        ) : (
          <button className="loop-mode-empty-definition" onClick={onChangeLoop}>
            <LoopIcon size={15} />
            <span><strong>{definitions ? "Choose a loop" : "Loading loops…"}</strong><small>Select reusable stages for this goal.</small></span>
          </button>
        )}
        <div className="loop-mode-definition-actions">
          <button onClick={onChangeLoop}><ReturnIcon size={11} /> change loop</button>
          <button disabled={!definition} onClick={onEditLoop}><EditIcon size={11} /> edit structure</button>
        </div>
      </SetupSection>

      <SetupSection label="Parameters" note="this run only, the loop is not modified">
        <PlanningAgentControls value={agentConfig} onChange={onAgentConfig} />
        {(advancedOptions.length > 0 || Object.keys(provider?.text_options ?? {}).length > 0) && (
          <>
            <button className={"loop-mode-advanced-toggle" + (advanced ? " open" : "")} onClick={onAdvanced}>
              advanced <span>›</span>
            </button>
            {advanced && provider && agentConfig && (
              <div className="loop-mode-advanced-grid">
                {advancedOptions.map(([key, field]) => {
                  const effective = optionFieldForModel(provider, agentConfig.model, key, field);
                  return (
                    <label key={key}>
                      <span>{effective.label}</span>
                      <select value={agentConfig.options[key] ?? effective.default} onChange={(event) => onAdvancedOption(key, event.target.value)}>
                        {effective.values.map((value) => <option key={value} value={value}>{optionLabel(effective, value)}</option>)}
                      </select>
                    </label>
                  );
                })}
                {Object.entries(provider.text_options ?? {}).map(([key, field]) => (
                  <label key={key}>
                    <span>{field.label}</span>
                    <input value={agentConfig.options[key] ?? field.default} placeholder={field.placeholder ?? ""} onChange={(event) => onAdvancedOption(key, event.target.value)} />
                  </label>
                ))}
              </div>
            )}
          </>
        )}
      </SetupSection>

      <SetupSection label="Workdir">
        <div className="loop-mode-workdir">
          <input value={folder} readOnly placeholder="Choose a repository or project root" />
          <button className="btn icon" onClick={onChooseFolder} title="Choose work folder"><FolderIcon size={12} /></button>
          <span>isolated worktree</span>
        </div>
      </SetupSection>

      {error && <div className="form-error">{error}</div>}
      <footer className="loop-mode-start-row">
        <button className="btn primary" disabled={busy || !goal.trim() || !folder.trim() || !definition || !agentConfig} onClick={onStart}>
          <PlayIcon size={12} /> {busy ? "Starting…" : "Start loop"}
        </button>
      </footer>
    </div>
  );
}

function SetupSection({
  label,
  note,
  children,
}: {
  label: string;
  note?: string;
  children: React.ReactNode;
}) {
  return (
    <section className="loop-mode-setup-section">
      <header><strong>{label}</strong>{note && <span>— {note}</span>}</header>
      {children}
    </section>
  );
}

function LoopModeRunView({
  run,
  busy,
  error,
  onResume,
  onRequestChanges,
  onAccept,
  onCreatePullRequest,
  onOpenEditor,
  onDiscuss,
  onRerun,
}: {
  run: WorkLoopRun;
  busy: boolean;
  error: string | null;
  onResume: (note: string) => Promise<void>;
  onRequestChanges: (note: string) => Promise<void>;
  onAccept: () => Promise<void>;
  onCreatePullRequest: () => Promise<void>;
  onOpenEditor: () => void;
  onDiscuss: () => void;
  onRerun: () => Promise<void>;
}) {
  const [resolution, setResolution] = useState("");
  const [requestingChanges, setRequestingChanges] = useState(false);
  const [changeNote, setChangeNote] = useState("");
  const resultReady = run.status === "completed" || run.status === "awaiting_approval" || run.status === "accepted";
  const accepted = run.status === "accepted";
  const changedFiles = collectChangedFiles(run);
  const evidence = collectEvidence(run);
  const summary = run.summary || [...run.stages].reverse().find((stage) => stage.summary)?.summary || "The loop completed without a summary.";
  const attempt = Math.max(1, ...run.stages.map((stage) => stage.attempt));
  const outputStage = run.stages.find((stage) => stage.id === run.current_stage_id)
    ?? [...run.stages].reverse().find((stage) => stage.status !== "pending")
    ?? null;

  return (
    <div className="loop-mode-run">
      <header className="loop-mode-run-head">
        <strong>run {run.number} · {run.loop_definition_name || run.loop_definition_id}</strong>
        <span>rev {run.loop_definition_revision || "—"} · {compactPath(run.workspace_path)}</span>
        <em className={statusTone(run.status)}>{statusLabel(run.status)}</em>
      </header>
      <LoopRunStageSpine
        stages={run.stages}
        attempt={attempt}
        trailing={<>{formatElapsed(run.elapsed_seconds)}{run.cost_usd !== null && <> · ${run.cost_usd.toFixed(2)}</>}</>}
      />

      {run.status === "blocked_user" && (
        <div className="loop-mode-blocker">
          <AlertIcon size={15} />
          <div>
            <strong>The loop needs your input</strong>
            <p>{run.status_reason || currentBlocker(run) || "Resolve the blocker, then resume the current stage."}</p>
            <input value={resolution} onChange={(event) => setResolution(event.target.value)} placeholder="What changed or was decided?" />
            <div className="loop-mode-blocker-actions">
              <button className="btn primary sm" disabled={busy} onClick={() => void onResume(resolution.trim())}><CheckIcon size={11} /> Resume with answer</button>
              <button className="btn sm" disabled={busy} onClick={() => void onResume("Fold this into the current scope and continue.")}>Fold it in</button>
              <button className="btn sm" disabled={busy} onClick={() => void onResume("Keep this out of scope and continue.")}>Keep it out</button>
              <button className="btn chat sm" onClick={onDiscuss}><ChatIcon size={11} /> Answer in chat</button>
            </div>
          </div>
        </div>
      )}

      {run.status === "failed" && (
        <div className="loop-mode-blocker failed"><AlertIcon size={15} /><div><strong>Run failed</strong><p>{run.status_reason || "The backend stopped this run."}</p></div></div>
      )}

      {resultReady ? (
        <div className="loop-mode-result">
          <div className="loop-mode-result-summary doc">
            <CheckIcon size={16} />
            <div><strong>{accepted ? "Result approved" : "Result ready for approval"}</strong><p>{summary}</p></div>
          </div>
          <section className="loop-mode-result-card">
            <header><strong>Changed files</strong><span>{changedFiles.length}</span><button className="btn ghost icon sm" onClick={onOpenEditor} title="Open changes in editor"><DocIcon size={12} /></button></header>
            {changedFiles.map((file) => <div className="loop-mode-file" key={file.path}><span>{file.path}</span><em className="add">+{file.additions}</em><em className="del">−{file.deletions}</em></div>)}
            {changedFiles.length === 0 && <p className="loop-mode-result-empty">No changed files reported.</p>}
          </section>
          <section className="loop-mode-result-card">
            <header><strong>Validation evidence</strong></header>
            <div className="loop-mode-evidence">
              {evidence.map((item) => <span key={item}><CheckIcon size={9} /> {item}</span>)}
              {evidence.length === 0 && <p className="loop-mode-result-empty">No validation evidence reported.</p>}
            </div>
          </section>
          {requestingChanges && !accepted && (
            <div className="loop-mode-change-request">
              <strong>Request changes</strong>
              <textarea value={changeNote} onChange={(event) => setChangeNote(event.target.value)} placeholder="Describe what needs to change…" autoFocus />
              <div><button className="btn sm" onClick={() => setRequestingChanges(false)}>Cancel</button><button className="btn warn sm" disabled={busy || !changeNote.trim()} onClick={() => void onRequestChanges(changeNote.trim())}><ReturnIcon size={11} /> Send &amp; re-run</button></div>
            </div>
          )}
        </div>
      ) : (
        <div className="loop-mode-stage-detail">
          <header className="loop-mode-stage-output-head"><span>Step output</span><strong>{outputStage?.name ?? "Waiting for the first stage"}</strong></header>
          {ACTIVE_RUN_STATUSES.has(run.status) && <div className="loop-mode-live"><span className="matz-mini" /> {run.status === "assessing" ? "Assessing the latest stage report…" : "The loop is working through the current stage…"}</div>}
          {outputStage && <LoopRunStageTimeline stages={[outputStage]} />}
          {outputStage?.status === "changes_requested" && <div className="loop-mode-return-note"><ReturnIcon size={11} /> Changes requested · returning to the configured stage</div>}
        </div>
      )}

      {error && <div className="form-error loop-mode-run-error">{error}</div>}
      {resultReady && (
        <footer className="loop-mode-result-actions">
          {accepted ? (
            <>
              <button className="btn primary" disabled={busy} onClick={() => void onCreatePullRequest()}><BranchIcon size={12} /> Create pull request</button>
              <button className="btn" onClick={onOpenEditor}><DocIcon size={12} /> Open in editor</button>
              <button className="btn chat" onClick={onDiscuss}><ChatIcon size={12} /> Discuss in chat</button>
              <span />
              <button className="btn ghost" disabled={busy} onClick={() => void onRerun()}><LoopIcon size={12} /> Re-run from current state</button>
            </>
          ) : (
            <>
              <span />
              <button className="btn" disabled={busy} onClick={() => setRequestingChanges((value) => !value)}><ReturnIcon size={12} /> Request changes</button>
              <button className="btn approve" disabled={busy} onClick={() => void onAccept()}><CheckIcon size={12} /> Approve result</button>
            </>
          )}
        </footer>
      )}
    </div>
  );
}

function collectChangedFiles(run: WorkLoopRun) {
  if (run.changed_files.length > 0) return run.changed_files;
  const byPath = new Map<string, { path: string; additions: number; deletions: number }>();
  for (const stage of run.stages) {
    for (const file of stage.changed_files) byPath.set(file.path, file);
  }
  return [...byPath.values()];
}

function collectEvidence(run: WorkLoopRun): string[] {
  if (run.evidence.length > 0) return run.evidence;
  return run.stages
    .map((stage) => stage.validation_evidence.trim())
    .filter((value, index, values) => value && values.indexOf(value) === index);
}

function currentBlocker(run: WorkLoopRun): string {
  return run.stages.find((stage) => stage.id === run.current_stage_id)?.blocker ?? "";
}

function orderRuns(runs: WorkLoopRun[]): WorkLoopRun[] {
  return [...runs].sort((left, right) => right.number - left.number);
}

function statusLabel(status: LoopStatus): string {
  if (status === "blocked_user") return "blocked · user";
  if (status === "awaiting_approval" || status === "completed") return "awaiting approval";
  if (status === "accepted") return "accepted";
  if (status === "waiting_report") return "waiting report";
  if (status === "needs_agent") return "starting agent";
  return status.replaceAll("_", " ");
}

function statusTone(status: LoopStatus): string {
  if (status === "accepted" || status === "cleaned") return "good";
  if (status === "blocked_user" || status === "failed" || status === "cancelled") return "danger";
  if (status === "awaiting_approval" || status === "completed") return "info";
  return "warn";
}

function compactPath(path: string): string {
  if (!path) return "work folder not selected";
  const home = "/Users/";
  if (path.startsWith(home)) {
    const parts = path.split("/");
    return `~/${parts.slice(3).join("/")}`;
  }
  return path;
}

function formatElapsed(seconds: number | null): string {
  if (seconds === null) return "elapsed —";
  if (seconds < 60) return `${Math.round(seconds)} sec`;
  return `${Math.round(seconds / 60)} min`;
}

function errorMessage(reason: unknown): string {
  return reason instanceof Error ? reason.message : String(reason);
}
