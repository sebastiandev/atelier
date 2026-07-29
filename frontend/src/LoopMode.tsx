import { useEffect, useMemo, useRef, useState } from "react";

import {
  type ArtifactSummary,
  type LoopBrief,
  type LoopDefinition,
  type LoopDefinitionSnapshot,
  type LoopStatus,
  type ProjectSummary,
  type WorkDetail,
  type WorkLoopRun,
  acceptWorkLoopRun,
  cancelWorkLoopRun,
  createWorkLoopRunPrStage,
  getWorkLoopRun,
  getWorkLoopBrief,
  listLoopDefinitions,
  listWorkLoopRuns,
  patchWork,
  refreshWorkLoopRunPr,
  requestWorkLoopRunChanges,
  retryWorkLoopRunStage,
  rerunWorkLoopRun,
  saveWorkLoopBrief,
  sendWorkLoopRunPrFeedback,
  resumeWorkLoopRun,
  startWorkLoopRun,
} from "./api";
import { CompleteWorkDialog } from "./CompleteWorkDialog";
import { FolderPickerDialog } from "./FolderPickerDialog";
import { CheckIcon, LoopIcon } from "./Icons";
import { LoopBriefSetup } from "./LoopBriefSetup";
import {
  LoopStructureEditor,
} from "./LoopUI";
import {
  type RunDock,
  type RunSurfaceData,
  RunRail,
  RunSurface,
} from "./LoopRunView";
import { PaneResizeHandle } from "./PaneResizeHandle";
import { type LoopStartSeed, loopStartStorageKey } from "./loopSetup";
import { type PlanningAgentConfig } from "./planningSetup";
import {
  providerOptionsPayload,
  useProviderDescriptors,
} from "./providerDescriptors";
import { ShellTopbar } from "./ShellTopbar";
import {
  useLayoutStore,
  WORK_RAIL_MAX,
  WORK_RAIL_MIN,
} from "./state/layout";

type Props = {
  artifacts: ArtifactSummary[];
  initialSeed: LoopStartSeed | null;
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
  initialSeed,
  project,
  work,
}: Props) {
  const defaultGoal = initialSeed?.goal || work.description || work.name;
  const [folder, setFolder] = useState(initialSeed?.folder ?? "");
  const [definitions, setDefinitions] = useState<LoopDefinition[] | null>(null);
  const [selectedDefinition, setSelectedDefinition] =
    useState<LoopDefinition | null>(null);
  const [brief, setBrief] = useState<LoopBrief>({ goal: defaultGoal, stages: [] });
  const [briefPersistenceReady, setBriefPersistenceReady] = useState(false);
  const [folderPickerOpen, setFolderPickerOpen] = useState(false);
  const [editorOpen, setEditorOpen] = useState(initialSeed?.createDefinition ?? false);
  const [editorDefinition, setEditorDefinition] = useState<LoopDefinition | null>(null);
  const [editReturnsToSetup, setEditReturnsToSetup] = useState(false);
  const [preparingRun, setPreparingRun] = useState(false);
  const [creatingDefinition, setCreatingDefinition] = useState(
    initialSeed?.createDefinition ?? false,
  );
  const [runs, setRuns] = useState<WorkLoopRun[]>([]);
  const [selectedRunId, setSelectedRunId] = useState<string | null>(null);
  const [workStatus, setWorkStatus] = useState(work.status);
  const [completeOpen, setCompleteOpen] = useState(false);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [runDock, setRunDock] = useState<RunDock>(null);
  const railWidth = useLayoutStore((state) => state.workRailWidth);
  const setRailWidth = useLayoutStore((state) => state.setWorkRailWidth);
  const { descriptors } = useProviderDescriptors();
  const requestSequence = useRef(0);

  // The base execution config lives on the brief's first agent stage. It is
  // not mirrored in local state: the setup screen renders from the brief and
  // starts from the brief, so the two cannot disagree.
  const agentConfig = baseAgent(brief, selectedDefinition);

  const activeRun = useMemo(
    () => preparingRun ? null : runs.find((run) => run.id === selectedRunId) ?? runs[0] ?? null,
    [preparingRun, runs, selectedRunId],
  );
  const canActOnRun = workStatus === "active" && activeRun?.id === runs[0]?.id;
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
      .catch((reason) => {
        if (!cancelled) setError(errorMessage(reason));
      });
    return () => {
      cancelled = true;
    };
  }, [folder, initialSeed?.definitionId, work.slug]);

  useEffect(() => {
    let cancelled = false;
    getWorkLoopBrief(work.slug)
      .then((saved) => {
        if (cancelled) return;
        setBriefPersistenceReady(true);
        if (saved) setBrief(saved.goal.trim() ? saved : { ...saved, goal: defaultGoal });
      })
      .catch(() => {
        // Older backends and works without a saved draft keep the local defaults.
      });
    return () => {
      cancelled = true;
    };
  }, [work.slug]);

  useEffect(() => {
    if (!briefPersistenceReady) return;
    const timer = window.setTimeout(() => {
      void saveWorkLoopBrief(work.slug, brief).catch(() => {
        // Starting the run performs a final save and surfaces actionable errors.
      });
    }, 600);
    return () => window.clearTimeout(timer);
  }, [brief, briefPersistenceReady, work.slug]);

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
    setPreparingRun(false);
  }

  async function start() {
    if (!selectedDefinition || !agentConfig || !brief.goal.trim() || !folder.trim()) return;
    setBusy(true);
    setError(null);
    try {
      const runBrief = normalizedBrief(brief, selectedDefinition, agentConfig);
      await saveWorkLoopBrief(work.slug, runBrief).catch(() => runBrief);
      const created = await startWorkLoopRun(work.slug, {
        goal: brief.goal.trim(),
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
        brief: runBrief,
        source_run_id: preparingRun ? selectedRunId ?? undefined : undefined,
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

  async function refreshActivePr(force = false) {
    if (!activeRun) return;
    if (force) {
      await act(() => refreshWorkLoopRunPr(work.slug, activeRun.id, true));
      return;
    }
    try {
      replaceRun(await refreshWorkLoopRunPr(work.slug, activeRun.id));
    } catch {
      // Background polling is best-effort; the explicit refresh surfaces errors.
    }
  }

  async function prepareNewRun(run: WorkLoopRun) {
    const snapshot = run.loop_definition;
    setFolder(run.root_path);
    setBrief(
      withBaseAgent(
        { ...(run.brief ?? { stages: [] }), goal: run.goal },
        run.loop_definition ? definitionFromSnapshot(run.loop_definition) : null,
        { provider: run.provider, model: run.model, options: run.options },
      ),
    );
    setSelectedDefinition(
      definitions?.find((definition) => definition.id === run.loop_definition_id && definition.valid)
        ?? (snapshot ? definitionFromSnapshot(snapshot) : null),
    );
    setRunDock(null);
    setPreparingRun(true);
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
          { href: `/works/${work.slug}`, label: work.slug },
          { label: "loop" },
        ]}
        primaryAction={workStatus === "active" ? (
          <button className="btn sm" disabled={busy} onClick={() => setCompleteOpen(true)}>
            <CheckIcon size={11} /> Mark done
          </button>
        ) : (
          <button
            className="btn sm"
            disabled={busy}
            onClick={() => {
              setBusy(true);
              setError(null);
              patchWork(work.slug, { status: "active" })
                .then((updated) => setWorkStatus(updated.status))
                .catch((reason) => setError(errorMessage(reason)))
                .finally(() => setBusy(false));
            }}
          >
            Reopen
          </button>
        )}
      />
      <div className="loop-mode-shell">
        <LoopModeRail
          work={work}
          goal={brief.goal}
          folder={folder}
          definition={selectedDefinition}
          runs={runs}
          selectedRunId={activeRun?.id ?? null}
          pullRequests={pullRequests}
          onRun={(runId) => {
            setPreparingRun(false);
            setSelectedRunId(runId);
          }}
          onViewLoop={() => {
            if (activeRun) setRunDock({ kind: "loop", stageId: activeRun.current_stage_id });
          }}
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
        <main className={`loop-mode-main${activeRun ? " run-host" : ""} themed-scrollbar`}>
          {activeRun ? (
            <RunSurface
              data={workLoopRunData(activeRun)}
              busy={busy}
              chatProjects={project ? [project] : []}
              chatWorks={[work]}
              dock={runDock}
              error={error}
              readOnly={!canActOnRun}
              variant="loop"
              workSlug={work.slug}
              onDock={setRunDock}
              onResolveBlocker={canActOnRun ? (note) => act(() => resumeWorkLoopRun(work.slug, activeRun.id, { resolution_note: note })) : undefined}
              onResolveReviewGate={canActOnRun ? (decision, enforcedFindings, instruction) => act(() => resumeWorkLoopRun(work.slug, activeRun.id, {
                gate_decision: decision,
                enforced_findings: enforcedFindings,
                resolution_note: instruction || undefined,
              })) : undefined}
              onRequestChanges={canActOnRun ? (note) => act(() => requestWorkLoopRunChanges(work.slug, activeRun.id, note)) : undefined}
              onRefreshPr={canActOnRun ? refreshActivePr : undefined}
              onApprove={canActOnRun ? () => act(() => acceptWorkLoopRun(work.slug, activeRun.id)) : undefined}
              onCancel={canActOnRun ? () => act(() => cancelWorkLoopRun(work.slug, activeRun.id)) : undefined}
              onCreatePr={canActOnRun ? (setup) => act(() => createWorkLoopRunPrStage(work.slug, activeRun.id, setup)) : undefined}
              onChangeLoop={canActOnRun ? () => prepareNewRun(activeRun) : undefined}
              onFollowUp={canActOnRun ? (kind, note) => act(() => rerunWorkLoopRun(work.slug, activeRun.id, { kind, note: note || undefined })) : undefined}
              onRerun={canActOnRun ? () => prepareNewRun(activeRun) : undefined}
              onRetry={canActOnRun && activeRun.status === "failed" ? () => act(() => retryWorkLoopRunStage(work.slug, activeRun.id)) : undefined}
              onSendPrFeedback={canActOnRun ? (comments, instruction) => act(() => sendWorkLoopRunPrFeedback(work.slug, activeRun.id, { comments, instruction })) : undefined}
              onEditLoop={canActOnRun ? () => {
                const snapshot = activeRun.loop_definition;
                setFolder(activeRun.root_path);
                setBrief(
                  withBaseAgent(
                    { ...(activeRun.brief ?? { stages: [] }), goal: activeRun.goal },
                    snapshot ? definitionFromSnapshot(snapshot) : null,
                    { provider: activeRun.provider, model: activeRun.model, options: activeRun.options },
                  ),
                );
                setEditorDefinition(
                  (snapshot
                    ? definitions?.find((definition) => definition.id === snapshot.id)
                      ?? definitionFromSnapshot(snapshot)
                    : null),
                );
                setEditReturnsToSetup(true);
                setCreatingDefinition(false);
                setEditorOpen(true);
              } : undefined}
            />
          ) : (
            <LoopBriefSetup
              workSlug={work.slug}
              folder={folder}
              definitions={definitions}
              definition={selectedDefinition}
              brief={brief}
              busy={busy || workStatus !== "active"}
              error={workStatus === "active" ? error : "Reopen this work to start another run."}
              goalEditable
              onBrief={setBrief}
              onChooseFolder={() => setFolderPickerOpen(true)}
              onSelectDefinition={setSelectedDefinition}
              onEditLoop={() => {
                if (!selectedDefinition) return;
                setEditorDefinition(selectedDefinition);
                setEditReturnsToSetup(false);
                setCreatingDefinition(false);
                setEditorOpen(true);
              }}
              onStart={() => void start()}
            />
          )}
        </main>
      </div>

      {completeOpen && (
        <CompleteWorkDialog
          work={work}
          onClose={() => setCompleteOpen(false)}
          onCompleted={() => window.location.assign("/")}
        />
      )}

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
      {editorOpen && (creatingDefinition || editorDefinition || selectedDefinition) && (
        <LoopStructureEditor
          workSlug={work.slug}
          rootPath={folder}
          definition={creatingDefinition ? undefined : editorDefinition ?? selectedDefinition ?? undefined}
          onClose={() => {
            setEditorDefinition(null);
            setEditReturnsToSetup(false);
            setEditorOpen(false);
          }}
          onSaved={(definition) => {
            setSelectedDefinition(definition);
            setEditorDefinition(null);
            setCreatingDefinition(false);
            setEditorOpen(false);
            setPreparingRun(editReturnsToSetup);
            setEditReturnsToSetup(false);
            setDefinitions((current) => current
              ? [definition, ...current.filter((item) => item.id !== definition.id)]
              : [definition]);
          }}
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
  onRun,
  onViewLoop,
}: {
  work: WorkDetail;
  goal: string;
  folder: string;
  definition: LoopDefinition | null;
  runs: WorkLoopRun[];
  selectedRunId: string | null;
  pullRequests: ArtifactSummary[];
  onRun: (runId: string) => void;
  onViewLoop: () => void;
}) {
  const selectedRun = runs.find((run) => run.id === selectedRunId) ?? null;
  return (
    <RunRail
      anchor={(
        <>
          <div className="loop-mode-label">
            <span className="loop-mode-pip"><LoopIcon size={13} /></span>
            <span>Loop</span>
          </div>
          <div className="loop-mode-work">
            <strong>{work.name}</strong>
            <span>{work.slug} · {compactPath(folder)}</span>
          </div>
          <p className="loop-mode-goal">{goal || "Describe the goal before starting."}</p>
        </>
      )}
      definition={selectedRun ? selectedRun.loop_definition ?? null : definition}
      definitionMeta={selectedRun ? "pinned for selected run" : undefined}
      runs={runs.map(workLoopRunData)}
      selectedRunId={selectedRunId}
      onRun={onRun}
      onViewLoop={onViewLoop}
    >
      {runs.length > 0 ? (
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
      ) : (
        <span className="loop-mode-rail-foot">runs and pull requests appear here once the loop starts</span>
      )}
    </RunRail>
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

function workLoopRunData(run: WorkLoopRun): RunSurfaceData {
  const changedFiles = run.changed_files.length > 0 ? run.changed_files : (() => {
  const byPath = new Map<string, { path: string; additions: number; deletions: number }>();
  for (const stage of run.stages) {
    for (const file of stage.changed_files) byPath.set(file.path, file);
  }
  return [...byPath.values()];
  })();
  const evidence = run.evidence.length > 0 ? run.evidence : run.stages
    .map((stage) => stage.validation_evidence.trim())
    .filter((value, index, values) => value && values.indexOf(value) === index);
  return {
    accepted: run.status === "accepted" || run.accepted_at !== null,
    changedFiles,
    cleanupAt: run.cleanup_at,
    completedAt: run.completed_at,
    costUsd: run.cost_usd,
    currentStageId: run.current_stage_id,
    definition: run.loop_definition ?? null,
    elapsedSeconds: run.elapsed_seconds,
    evidence,
    goal: run.goal,
    id: run.id,
    loopName: run.loop_definition_name || run.loop_definition_id,
    number: run.number,
    passNumber: run.pass_number ?? 1,
    passes: run.passes ?? [],
    pr: run.pr ?? null,
    prComments: run.pr_comments ?? [],
    revision: run.loop_definition_revision,
    stages: run.stages,
    startedAt: run.started_at,
    status: run.status,
    statusReason: run.status_reason,
    summary: run.summary || [...run.stages].reverse().find((stage) => stage.summary)?.summary || "The loop completed without a summary.",
    targetId: "objective",
    workspacePath: run.workspace_path,
    brief: run.brief ?? null,
    reviewGate: run.review_gate ?? null,
    waivedFindingsCount: run.waived_findings_count ?? 0,
    runKind: run.run_kind ?? "initial",
    seedLabel: run.seed_label ?? "",
  };
}

function normalizedBrief(
  brief: LoopBrief,
  definition: LoopDefinition,
  baseAgent: PlanningAgentConfig,
): LoopBrief {
  const firstAgentId = definition.stages.find((stage) => stage.agent !== null)?.id;
  return {
    goal: brief.goal.trim(),
    stages: definition.stages
      .filter((stage) => stage.agent !== null)
      .map((stage) => {
        const saved = brief.stages.find((item) => item.stage_id === stage.id);
        return {
          stage_id: stage.id,
          note: saved?.note.trim() ?? "",
          context: (saved?.context ?? []).filter((item) => item.value.trim()).map((item) => ({ ...item, value: item.value.trim() })),
          agent: stage.id === firstAgentId ? baseAgent : saved?.agent ?? null,
          review_gate: saved?.review_gate ?? null,
          approved_command_prefixes: saved?.approved_command_prefixes ?? null,
        };
      }),
  };
}

/** The brief's entry-stage agent, completed into a launch config. */
function baseAgent(
  brief: LoopBrief,
  definition: LoopDefinition | null,
): PlanningAgentConfig | null {
  const entry = definition?.stages.find((stage) => stage.agent !== null);
  const saved = brief.stages.find((stage) => stage.stage_id === entry?.id)?.agent;
  return saved?.provider && saved.model
    ? { provider: saved.provider, model: saved.model, options: saved.options ?? {} }
    : null;
}

/** Pin ``agent`` to the brief's entry stage, the one place it is read from. */
function withBaseAgent(
  brief: LoopBrief,
  definition: LoopDefinition | null,
  agent: PlanningAgentConfig,
): LoopBrief {
  const entry = definition?.stages.find((stage) => stage.agent !== null);
  if (!entry) return brief;
  const current = brief.stages.find((stage) => stage.stage_id === entry.id);
  return {
    ...brief,
    stages: [
      ...brief.stages.filter((stage) => stage.stage_id !== entry.id),
      {
        stage_id: entry.id,
        note: current?.note ?? "",
        context: current?.context ?? [],
        review_gate: current?.review_gate ?? null,
        approved_command_prefixes: current?.approved_command_prefixes ?? null,
        agent,
      },
    ],
  };
}

function orderRuns(runs: WorkLoopRun[]): WorkLoopRun[] {
  return [...runs].sort((left, right) => right.number - left.number);
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

function definitionFromSnapshot(snapshot: LoopDefinitionSnapshot): LoopDefinition {
  return {
    ...snapshot,
    errors: [],
    forked_from: null,
    is_default: false,
    valid: true,
  };
}

function errorMessage(reason: unknown): string {
  return reason instanceof Error ? reason.message : String(reason);
}
