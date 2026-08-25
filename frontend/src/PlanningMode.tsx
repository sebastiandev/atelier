import {
  Fragment,
  type CSSProperties,
  type ReactNode,
  useCallback,
  useEffect,
  useMemo,
  useState,
} from "react";

import {
  type ProviderField,
  type ChatSummary,
  type LoopStatus,
  type PlanArtifact,
  type PlanArtifactDetail,
  type PlanArtifactRun,
  type PlanMaterializationStatus,
  type LoopBrief,
  type LoopDefinition,
  type PrConfig,
  type PrLifecycle,
  type PrFeedbackPayload,
  type ProviderDescriptor,
  type ProjectSummary,
  type RetryStageOverride,
  type WorkDetail,
  type WorkPlan,
  type WorkSummary,
  resolveWorkPlanMaterializationPermission,
  listLoopDefinitions,
  revealWork,
} from "./api";
import { ChatTile } from "./Chat";
import { LoopStructureEditor } from "./LoopUI";
import { LoopBriefSetup } from "./LoopBriefSetup";
import {
  AgentIcon,
  BoltIcon,
  BugIcon,
  ChatIcon,
  CheckIcon,
  ChevronRightIcon,
  DocIcon,
  EyeIcon,
  LoopIcon,
  SparkIcon,
  FolderIcon,
} from "./Icons";
import { PaneResizeHandle } from "./PaneResizeHandle";
import { PermissionApprovalDialog } from "./PermissionApprovalDialog";
import {
  type RunDock,
  LoopRunView,
  RunRail,
  planningRunData,
  prStatusTone,
} from "./LoopRunView";
import {
  coerceProviderOptionsForModel,
  modelPickerOptions,
  optionLabel,
  providerDefaults,
  providerEffortOption,
  providerFastOption,
  providerPermissionOption,
  useProviderDescriptors,
} from "./providerDescriptors";
import { resolvePlanLink } from "./editedPaths";
import { RichMarkdownEditor } from "./RichMarkdownEditor";
import { ShellTopbar, type ShellTopbarCrumb } from "./ShellTopbar";
import {
  PLANNING_DOCK_MAX,
  PLANNING_DOCK_MIN,
  PLANNING_RAIL_MAX,
  PLANNING_RAIL_MIN,
  useLayoutStore,
} from "./state/layout";
import {
  type PlanningAgentConfig,
  type PlanningFrameworkId,
  planningFrameworkDefinition,
} from "./planningSetup";
import type {
  PendingPermission,
  PermissionDecision,
} from "./useAgentStream";

export type PlanningView =
  | { kind: "overview" }
  | { kind: "epic"; id: string }
  | { kind: "artifact"; id: string }
  | { kind: "run"; id: string; runId?: string }
  | { kind: "setup"; id: string; sourceRunId?: string }
  | { kind: "source"; id: string }
  | { kind: "accept" };

export type PlanOverviewTab = "summary" | "blocking" | "completed" | "pending";

type PlanningModeProps = {
  work: WorkDetail;
  workAction?: ReactNode;
  project: ProjectSummary | null;
  plan: WorkPlan | null;
  materializationStatus: PlanMaterializationStatus | null;
  planningChatSlug: string | null;
  planningChatSummary: ChatSummary | null;
  planningChatProjects: ProjectSummary[];
  planningChatWorks: WorkSummary[];
  selectedDetail: PlanArtifactDetail | null;
  /** Live PR status by URL, from the polled artifact rows. A run's own PR
   *  snapshot is only refreshed on demand, so it is the wrong thing to read
   *  a current status from. */
  prStatusByUrl: Record<string, string>;
  draft: string;
  error: string | null;
  saving: boolean;
  view: PlanningView;
  overviewTab: PlanOverviewTab;
  chatOpen: boolean;
  framework: PlanningFrameworkId;
  onOverviewTab: (tab: PlanOverviewTab) => void;
  onView: (view: PlanningView) => void;
  onCreateSourcePlan: () => void;
  onFinishConversation: () => void;
  onDraftChange: (value: string) => void;
  onSave: () => void;
  onReset: () => void;
  onApprovePlan: () => void;
  onCreateBug: (artifactId: string, title: string, description: string) => Promise<void>;
  onLaunch: (detail: PlanArtifactDetail) => void;
  onStartRun: (
    detail: PlanArtifactDetail,
    definition: LoopDefinition,
    brief: LoopBrief,
  ) => Promise<void>;
  onResolveLoopBlocker: (
    artifact: PlanArtifact,
    runId: string,
    agentSlug: string,
    resolutionNote?: string,
    gateDecision?: "send_back" | "approve_as_is",
    enforcedFindings?: number[],
  ) => void;
  onRetryRunStage: (
    artifact: PlanArtifact,
    runId: string,
    override?: RetryStageOverride,
  ) => Promise<void>;
  onApproveRun: (artifact: PlanArtifact, runId: string) => Promise<void>;
  onCancelRun: (artifact: PlanArtifact, runId: string) => Promise<void>;
  onStopRunStage: (artifact: PlanArtifact, runId: string) => Promise<void>;
  onRequestRunChanges: (
    artifact: PlanArtifact,
    runId: string,
    note: string,
  ) => Promise<void>;
  onFollowUpRun: (
    artifact: PlanArtifact,
    runId: string,
    kind: "amend" | "verify",
    note: string,
  ) => Promise<void>;
  onCreateRunPr: (
    artifact: PlanArtifact,
    runId: string,
    setup: PrConfig,
  ) => Promise<void>;
  onSendRunPrFeedback: (
    artifact: PlanArtifact,
    runId: string,
    payload: PrFeedbackPayload,
  ) => Promise<void>;
  onRefreshRunPr: (
    artifact: PlanArtifact,
    runId: string,
    force?: boolean,
  ) => Promise<void>;
  onChatOpen: (open: boolean) => void;
  onPlanningChatUpdated: (chat: ChatSummary) => void;
  onPlanningFilesEdited: (paths: string[]) => void;
};

type PlanUiStatus =
  | "draft"
  | "ready"
  | "blocked"
  | "done"
  | "review"
  | "running"
  | "gated";

type PlanTree = {
  sources: PlanArtifact[];
  executable: PlanArtifact[];
};

type PlanEpic = {
  id: string;
  title: string;
  description: string;
  children: PlanArtifact[];
  counts: PlanCounts;
};

const PRIMARY_EPIC_ID = "EPIC-01";

export function PlanningMode({
  work,
  workAction,
  project,
  plan,
  materializationStatus,
  planningChatSlug,
  planningChatSummary,
  planningChatProjects,
  planningChatWorks,
  selectedDetail,
  prStatusByUrl,
  draft,
  error,
  saving,
  view,
  overviewTab,
  chatOpen,
  framework,
  onOverviewTab,
  onView,
  onCreateSourcePlan,
  onFinishConversation,
  onDraftChange,
  onSave,
  onReset,
  onApprovePlan,
  onCreateBug,
  onLaunch,
  onStartRun,
  onResolveLoopBlocker,
  onRetryRunStage,
  onApproveRun,
  onCancelRun,
  onStopRunStage,
  onRequestRunChanges,
  onCreateRunPr,
  onFollowUpRun,
  onSendRunPrFeedback,
  onRefreshRunPr,
  onChatOpen,
  onPlanningChatUpdated,
  onPlanningFilesEdited,
}: PlanningModeProps) {
  const readOnly = work.status !== "active";
  const tree = useMemo(() => splitPlan(plan), [plan]);
  const counts = useMemo(() => planCounts(tree.executable), [tree.executable]);
  const activeId = "id" in view ? view.id : null;
  const source = view.kind === "source" ? findArtifact(plan, view.id) : null;
  const artifact =
    view.kind === "artifact" || view.kind === "run" || view.kind === "setup"
      ? findArtifact(plan, view.id)
      : null;
  const epic = plan ? buildPlanEpic(work, plan, tree, counts) : null;
  const planningRailWidth = useLayoutStore((s) => s.planningRailWidth);
  const planningDockWidth = useLayoutStore((s) => s.planningDockWidth);
  const setPlanningDockWidth = useLayoutStore((s) => s.setPlanningDockWidth);
  const [bugDialogOpen, setBugDialogOpen] = useState(false);
  const [runDock, setRunDock] = useState<RunDock>(null);
  const [setupDefinitions, setSetupDefinitions] = useState<LoopDefinition[] | null>(null);
  const [setupDefinition, setSetupDefinition] = useState<LoopDefinition | null>(null);
  const [setupBrief, setSetupBrief] = useState<LoopBrief | null>(null);
  // The provider the entry stage runs on. Null until chosen, which is what
  // keeps Start disabled: a story has no parent agent to inherit from.
  const [setupEditorOpen, setSetupEditorOpen] = useState(false);
  const selectedRun = view.kind === "run"
    ? selectedDetail?.artifact.runs.find((run) => run.id === view.runId)
      ?? selectedDetail?.artifact.runs.at(-1)
      ?? null
    : null;
  // The run a "Start new run" was launched from: its loop and brief seed the
  // setup screen, exactly as Loop mode seeds from its previous run.
  const setupSource = view.kind === "setup" && view.sourceRunId
    ? selectedDetail?.artifact.runs.find((run) => run.id === view.sourceRunId) ?? null
    : null;
  const selectedRunData = selectedRun && selectedDetail
    ? planningRunData(selectedDetail.artifact, selectedRun)
    : null;
  useEffect(() => {
    if (view.kind !== "setup") return;
    let cancelled = false;
    listLoopDefinitions(work.slug, plan?.root_path ?? null)
      .then((rows) => {
        if (cancelled) return;
        setSetupDefinitions(rows);
        // Only a previous run pre-selects. A fresh story starts with nothing
        // chosen, so the picker is the obvious first decision.
        setSetupDefinition((current) => {
          if (current) return current;
          const pinned = setupSource?.loop_definition_id;
          return rows.find((row) => row.id === pinned && row.valid) ?? null;
        });
      })
      .catch(() => {
        if (!cancelled) setSetupDefinitions([]);
      });
    return () => {
      cancelled = true;
    };
  }, [view.kind, work.slug, plan?.root_path, setupSource?.loop_definition_id]);

  useEffect(() => {
    if (view.kind !== "setup") {
      setSetupDefinition(null);
      setSetupBrief(null);
      return;
    }
    const title = selectedDetail?.artifact.title ?? "";
    const seeded: LoopBrief = { ...(setupSource?.brief ?? { stages: [] }), goal: title };
    setSetupBrief((current) => current ?? seeded);
  }, [view.kind, setupSource, selectedDetail?.artifact.title]);

  const planningStyle: CSSProperties = {
    ["--pm-rail-width" as string]: `${planningRailWidth}px`,
    ["--pm-dock-width" as string]: `${planningDockWidth}px`,
    ["--shell-left-width" as string]: `${planningRailWidth}px`,
  };
  const hasPlanningChat = planningChatSlug !== null;
  const showChatDock =
    !readOnly
    && chatOpen
    && hasPlanningChat
    && view.kind !== "run"
    && view.kind !== "setup";
  const planningReady = Boolean(planningChatSummary?.planning_readiness?.ready);
  // Stable across plan polls that return the same documents: a fresh array
  // each tick would rebuild `makePlanLinkTo`, defeat `MarkdownText`'s memo and
  // re-parse every document -- the code-block flash that memo exists to stop.
  const artifacts = plan?.artifacts;
  const planReferences = useMemo(() => artifacts ?? [], [artifacts]);
  // Plan documents link to each other with relative paths. The browser would
  // resolve those against the app URL, leave the route table and land on the
  // home screen; resolving against the linking document instead opens the
  // document the author meant, which also selects it in the rail.
  const makePlanLinkTo = useCallback(
    (fromPath: string) => (href: string) => {
      const target = resolvePlanLink(href, fromPath);
      if (!target) return null;
      const match = planReferences.find((item) => item.path === target);
      // A relative link to something the plan does not own stays inert rather
      // than navigating away from the plan.
      if (!match) return null;
      return () => onView({ kind: "artifact", id: match.id });
    },
    [planReferences, onView],
  );
  const materializerActive = materializationStatus?.state === "running";
  const materializerVisible =
    materializationStatus?.state === "running" ||
    materializationStatus?.state === "waiting_permission" ||
    materializationStatus?.state === "stalled" ||
    materializationStatus?.state === "failed";
  const materializerRetryable =
    materializationStatus?.state === "waiting_permission" ||
    materializationStatus?.state === "stalled" ||
    materializationStatus?.state === "failed";
  const sourcePlanBusy = saving || materializerActive;
  const showSourcePlanAction =
    !sourcePlanBusy && (planningReady || materializerRetryable);
  const sourcePlanLabel = sourcePlanActionLabel({
    loading: sourcePlanBusy,
    status: materializationStatus,
  });
  const artifactIndex = artifact?.title.match(/^([^:]+):/)?.[1] ?? artifact?.id ?? "item";
  const planningCrumbs: ShellTopbarCrumb[] = [
    ...(project
      ? [{ href: `/projects/${project.slug}`, hue: project.color, label: project.name }]
      : []),
    { href: `/works/${work.slug}`, label: work.slug },
  ];
  if (!plan || plan.phase === "conversing") {
    planningCrumbs.push({ label: "planning" });
  } else if (view.kind === "overview") {
    planningCrumbs.push({ label: "plan overview" });
  } else {
    planningCrumbs.push({ label: "plan overview", onClick: () => onView({ kind: "overview" }) });
    if (view.kind === "epic") planningCrumbs.push({ label: epic?.id ?? "epic" });
    if (view.kind === "source") planningCrumbs.push({ label: source?.id ?? "source" });
    if (view.kind === "artifact") planningCrumbs.push({ label: artifactIndex });
    if (view.kind === "run") {
      planningCrumbs.push({ label: artifactIndex, onClick: () => onView({ kind: "artifact", id: view.id }) });
      planningCrumbs.push({ label: `run ${selectedRunData?.number ?? ""}`.trim() });
    }
    if (view.kind === "setup") {
      planningCrumbs.push({ label: artifactIndex, onClick: () => onView({ kind: "artifact", id: view.id }) });
      planningCrumbs.push({ label: "run setup" });
    }
    if (view.kind === "accept") planningCrumbs.push({ label: "approve plan" });
  }
  const topbarView =
    plan && plan.phase !== "conversing" && view.kind === "artifact" && artifact
      ? {
          inline: true,
          title: artifact.title,
          detail: <TreeStatus status={uiStatus(artifact)} />,
        }
      : undefined;
  const topbar = (
    <ShellTopbar
      crumbs={planningCrumbs}
      primaryAction={workAction}
      view={topbarView}
    />
  );

  if (!plan && hasPlanningChat) {
    return (
      <div className="planning-mode conversation-only has-topbar" style={planningStyle}>
        {topbar}
        <PlanningStarterRail
          work={work}
          readiness={planningReady}
          materializing={sourcePlanBusy}
          materializationStatus={materializationStatus}
          framework={planningFrameworkDefinition(framework).name}
          railWidth={planningRailWidth}
        />
        <main className="pm-main pm-conversation-main">
          {error && <div className="pm-error">{error}</div>}
          {materializerVisible && materializationStatus ? (
            <PlanningMaterializationStage
              framework={planningFrameworkDefinition(framework).name}
              workSlug={work.slug}
              status={materializationStatus}
              actionDisabled={sourcePlanBusy || readOnly}
              actionLabel={sourcePlanLabel}
              onAction={onCreateSourcePlan}
            />
          ) : (
            <PlanningChatCanvas
              chatSlug={planningChatSlug}
              chatSummary={planningChatSummary}
              projects={planningChatProjects}
              works={planningChatWorks}
              planReferences={planReferences}
              onOpenPlan={!readOnly && showSourcePlanAction ? onCreateSourcePlan : undefined}
              openPlanLabel={sourcePlanLabel}
              openPlanDisabled={sourcePlanBusy}
              onChatUpdated={onPlanningChatUpdated}
              onFilesEdited={onPlanningFilesEdited}
            />
          )}
        </main>
      </div>
    );
  }

  if (plan?.phase === "conversing") {
    return (
      <div className="planning-mode conversation-only has-topbar" style={planningStyle}>
        {topbar}
        <PlanningStarterRail
          work={work}
          readiness={planningReady}
          materializing={saving}
          materializationStatus={null}
          framework={planningFrameworkDefinition(framework).name}
          railWidth={planningRailWidth}
        />
        <main className="pm-main pm-conversation-main">
          {error && <div className="pm-error">{error}</div>}
          <PlanningChatCanvas
            chatSlug={planningChatSlug}
            chatSummary={planningChatSummary}
            projects={planningChatProjects}
            works={planningChatWorks}
            planReferences={planReferences}
            onOpenPlan={readOnly ? undefined : onFinishConversation}
            openPlanLabel={saving ? "Opening plan..." : "Open plan overview"}
            openPlanDisabled={saving || readOnly}
            onChatUpdated={onPlanningChatUpdated}
            onFilesEdited={onPlanningFilesEdited}
          />
        </main>
      </div>
    );
  }

  if (!plan) return null;

  return (
    <div className={"planning-mode has-topbar" + (showChatDock ? " with-dock" : "")} style={planningStyle}>
      {topbar}
      {view.kind === "run" && selectedDetail && selectedRun && selectedRunData ? (
        <PlanningRunRail
          artifact={selectedDetail.artifact}
          prStatusByUrl={prStatusByUrl}
          railWidth={planningRailWidth}
          run={selectedRun}
          onBack={() => onView({ kind: "artifact", id: selectedDetail.artifact.id })}
          onRun={(runId) => onView({ kind: "run", id: selectedDetail.artifact.id, runId })}
          onViewLoop={() => setRunDock({ kind: "loop", stageId: selectedRun.loop_current_stage_id || null })}
        />
      ) : view.kind === "run" ? (
        // Heading to a run whose detail is still in flight. Rendering
        // the plan rail here would flash the whole plan tree for a beat
        // before the run rail replaces it — hold the column instead.
        <aside className="pm-rail" style={{ width: planningRailWidth }} />
      ) : (
        <PlanRail
          work={work}
          plan={plan}
          tree={tree}
          counts={counts}
          activeId={activeId}
          railWidth={planningRailWidth}
          onEpic={(id) => onView({ kind: "epic", id })}
          onArtifact={(id) => onView({ kind: "artifact", id })}
          onSource={(id) => onView({ kind: "source", id })}
          readOnly={readOnly}
          onCreateBug={() => setBugDialogOpen(true)}
        />
      )}
      <main className={`pm-main${view.kind === "run" ? " run-host" : ""}`}>
        {error && <div className="pm-error">{error}</div>}
        {view.kind === "overview" && (
          <PlanOverview
            plan={plan}
            tree={tree}
            counts={counts}
            tab={overviewTab}
            onTab={onOverviewTab}
            onArtifact={(id) => onView({ kind: "artifact", id })}
            onSource={(id) => onView({ kind: "source", id })}
            onAccept={() => onView({ kind: "accept" })}
          />
        )}
        {view.kind === "epic" && (
          <EpicDetail
            epic={epic}
            plan={plan}
            saving={saving || readOnly}
            onArtifact={(id) => onView({ kind: "artifact", id })}
            onApprovePlan={onApprovePlan}
          />
        )}
        {view.kind === "artifact" && (
          <ArtifactDetail
            key={artifact?.id ?? "artifact"}
            artifact={artifact}
            detail={selectedDetail}
            draft={draft}
            saving={saving || readOnly}
            readOnly={readOnly}
            makeLinkTo={makePlanLinkTo}
            onDraftChange={onDraftChange}
            onSave={onSave}
            onReset={onReset}
            onResolveLoopBlocker={onResolveLoopBlocker}
            onOpenRun={(runId) => {
              if (artifact) onView({ kind: "run", id: artifact.id, runId });
            }}
            onLaunch={onLaunch}
            onEpic={() => onView({ kind: "epic", id: PRIMARY_EPIC_ID })}
            onApprovePlan={onApprovePlan}
          />
        )}
        {view.kind === "run" && !selectedDetail && (
          // Deep-linked from search: the artifact detail is still in
          // flight. Without this the artifact view renders for a frame
          // and the jump reads as two screens instead of one.
          <div className="pm-loading">Loading run…</div>
        )}
        {view.kind === "run" && artifact && selectedDetail && (
          selectedRun ? (
            <LoopRunView
              artifact={selectedDetail.artifact}
              run={selectedRun}
              busy={saving || readOnly}
              chatProjects={planningChatProjects}
              chatWorks={planningChatWorks}
              readOnly={readOnly}
              dock={runDock}
              workSlug={work.slug}
              onBack={() => onView({ kind: "artifact", id: selectedDetail.artifact.id })}
              onDock={setRunDock}
              onResolveBlocker={(note, agentSlug) =>
                onResolveLoopBlocker(
                  selectedDetail.artifact,
                  selectedRun.id,
                  agentSlug,
                  note,
                )
              }
              onRetry={(override) =>
                onRetryRunStage(selectedDetail.artifact, selectedRun.id, override)
              }
              onResolveReviewGate={(decision, enforcedFindings, instruction) =>
                onResolveLoopBlocker(
                  selectedDetail.artifact,
                  selectedRun.id,
                  selectedRun.agent_slug,
                  instruction,
                  decision,
                  enforcedFindings,
                )
              }
              onRequestChanges={(note) =>
                onRequestRunChanges(
                  selectedDetail.artifact,
                  selectedRun.id,
                  note,
                )
              }
              onRerun={() =>
                onView({
                  kind: "setup",
                  id: selectedDetail.artifact.id,
                  sourceRunId: selectedRun.id,
                })
              }
              onApprove={() =>
                onApproveRun(
                  selectedDetail.artifact,
                  selectedRun.id,
                )
              }
              onCancel={() =>
                onCancelRun(
                  selectedDetail.artifact,
                  selectedRun.id,
                )
              }
              onStopStage={() =>
                onStopRunStage(
                  selectedDetail.artifact,
                  selectedRun.id,
                )
              }
              onCreatePr={(setup) =>
                onCreateRunPr(
                  selectedDetail.artifact,
                  selectedRun.id,
                  setup,
                )
              }
              onFollowUp={(kind, note) =>
                onFollowUpRun(
                  selectedDetail.artifact,
                  selectedRun.id,
                  kind,
                  note,
                )
              }
              onSendPrFeedback={(comments, instruction) =>
                onSendRunPrFeedback(
                  selectedDetail.artifact,
                  selectedRun.id,
                  { comments, instruction },
                )
              }
              onRefreshPr={(force) => onRefreshRunPr(
                selectedDetail.artifact,
                selectedRun.id,
                force,
              )}
            />
          ) : <div className="pm-loading">No loop run recorded yet.</div>
        )}
        {view.kind === "source" && (
          <SourceDoc
            artifact={source}
            detail={selectedDetail}
            draft={draft}
            saving={saving || readOnly}
            readOnly={readOnly}
            makeLinkTo={makePlanLinkTo}
            onDraftChange={onDraftChange}
            onSave={onSave}
            onReset={onReset}
            onApprovePlan={onApprovePlan}
          />
        )}
        {view.kind === "setup" && !selectedDetail && (
          <div className="pm-loading">Loading story…</div>
        )}
        {view.kind === "setup" && selectedDetail && (
          <div className="pm-setup-body themed-scrollbar">
            <LoopBriefSetup
              brief={setupBrief ?? { goal: selectedDetail.artifact.title, stages: [] }}
              busy={saving || readOnly}
              definition={setupDefinition}
              definitions={setupDefinitions}
              error={error}
              folder={plan?.root_path ?? ""}
              goalLabel="Story"
              workSlug={work.slug}
              onBrief={setSetupBrief}
              onSelectDefinition={setSetupDefinition}
              onEditLoop={() => setSetupEditorOpen(true)}
              onStart={() => {
                if (!setupDefinition || !setupBrief) return;
                void onStartRun(selectedDetail, setupDefinition, setupBrief);
              }}
            />
          </div>
        )}
        {setupEditorOpen && (
          <LoopStructureEditor
            workSlug={work.slug}
            rootPath={plan?.root_path ?? null}
            definition={setupDefinition ?? undefined}
            onClose={() => setSetupEditorOpen(false)}
            onSaved={(definition) => {
              setSetupDefinition(definition);
              setSetupDefinitions((rows) => [
                definition,
                ...(rows ?? []).filter((row) => row.id !== definition.id),
              ]);
              setSetupEditorOpen(false);
            }}
          />
        )}
        {view.kind === "accept" && (
          <ApprovePlan plan={plan} counts={counts} saving={saving || readOnly} onBack={() => onView({ kind: "overview" })} onApprove={onApprovePlan} />
        )}
      </main>
      {showChatDock ? (
        <aside className="pm-chat-dock">
          <PaneResizeHandle
            defaultValue={420}
            edge="left"
            label="Resize plan chat dock"
            max={PLANNING_DOCK_MAX}
            min={PLANNING_DOCK_MIN}
            value={planningDockWidth}
            onChange={setPlanningDockWidth}
          />
          <ChatTile
            chatSlug={planningChatSlug!}
            chatSummary={planningChatSummary ?? undefined}
            projects={planningChatProjects}
            works={planningChatWorks}
            planReferences={planReferences}
            collapseHistoryByDefault
            presentation="planning"
            planningPlacement="dock"
            onClose={() => onChatOpen(false)}
            onUpdated={onPlanningChatUpdated}
            onFilesEdited={onPlanningFilesEdited}
          />
        </aside>
      ) : !readOnly && hasPlanningChat && view.kind !== "run" ? (
        <button className="pm-float-chat" onClick={() => onChatOpen(true)} title="Open plan chat" aria-label="Open plan chat">
          <ChatIcon size={18} />
        </button>
      ) : null}
      {!readOnly && bugDialogOpen && (
        <PlanBugDialog
          targets={tree.executable}
          defaultArtifactId={
            artifact?.executable ? artifact.id : tree.executable[0]?.id ?? ""
          }
          onClose={() => setBugDialogOpen(false)}
          onCreate={async (artifactId, title, description) => {
            await onCreateBug(artifactId, title, description);
            setBugDialogOpen(false);
          }}
        />
      )}
    </div>
  );
}

function SegmentedOption({
  icon,
  option,
  value,
  onPick,
}: {
  icon: ReactNode;
  option: { key: string; field: ProviderField };
  value: string;
  onPick: (next: string) => void;
}) {
  return (
    <div className="pm-agent-segmented" role="radiogroup" aria-label={option.field.label}>
      {icon}
      {option.field.values.map((item) => (
        <button
          type="button"
          key={item}
          role="radio"
          aria-checked={item === value}
          className={item === value ? "selected" : ""}
          onClick={() => onPick(item)}
        >
          {optionLabel(option.field, item)}
        </button>
      ))}
    </div>
  );
}

export function PlanningAgentControls({
  value,
  onChange,
  pinned,
  layout = "inline",
}: {
  value: PlanningAgentConfig | null;
  /** ``touchedKey`` names the option a person just set. Provider and model
   *  switches, and the normalising effect, emit provider defaults and report
   *  no key — a default that arrives by itself is not a choice, and callers
   *  that persist an override need to tell the two apart. */
  onChange: (value: PlanningAgentConfig, touchedKey?: string) => void;
  /** What the loop or stage already pins. Shown as the effective value
   *  when this run overrides nothing, because that is what the backend
   *  resolves to -- the provider's own default would be a fiction. */
  pinned?: { effort?: string | null; fast?: boolean | null; permissions?: string | null };
  /** `"rows"` labels each control and stacks it -- Agent, Effort,
   *  Permissions -- as run setup shows them, where these values decide what
   *  the run costs and what it may touch. The default inline row is right for
   *  a dialog that is mostly about something else. */
  layout?: "inline" | "rows";
}) {
  const { descriptors, loading, error } = useProviderDescriptors();
  const providers = descriptors ?? [];
  const selectedProvider =
    providers.find((provider) => provider.name === value?.provider) ?? null;
  const provider =
    selectedProvider ?? (providers.length > 0 ? preferredPlanningProvider(providers) : null);
  const model =
    provider && value?.provider === provider.name
      ? value.model
      : provider?.primary_field.default ?? "";
  const currentOptions =
    provider && value?.provider === provider.name
      ? value.options
      : provider && model
        ? providerDefaults(provider, model)
        : {};
  const permissionOption = provider ? providerPermissionOption(provider) : null;
  const effortOption = provider ? providerEffortOption(provider, model) : null;
  const fastOption = provider ? providerFastOption(provider) : null;

  useEffect(() => {
    if (!providers.length) return;
    const nextProvider =
      providers.find((item) => item.name === value?.provider) ??
      preferredPlanningProvider(providers);
    if (!nextProvider) return;
    const nextModel =
      value?.provider === nextProvider.name &&
      nextProvider.primary_field.values.includes(value.model)
        ? value.model
        : nextProvider.primary_field.default;
    const nextOptions =
      value?.provider === nextProvider.name
        ? coerceProviderOptionsForModel(nextProvider, nextModel, value.options)
        : providerDefaults(nextProvider, nextModel);
    if (
      value?.provider === nextProvider.name &&
      value.model === nextModel &&
      value.options === nextOptions
    ) {
      return;
    }
    if (
      value?.provider === nextProvider.name &&
      value.model === nextModel &&
      shallowEqualRecord(value.options, nextOptions)
    ) {
      return;
    }
    onChange({
      provider: nextProvider.name,
      model: nextModel,
      options: nextOptions,
    });
  }, [onChange, providers, value]);

  if (loading && providers.length === 0) {
    return <span className="pm-agent-cfg-note">Loading providers…</span>;
  }
  if (error && providers.length === 0) {
    return <span className="pm-agent-cfg-note warn">Provider options unavailable</span>;
  }
  if (!provider) {
    return <span className="pm-agent-cfg-note">No providers configured</span>;
  }
  const activeProvider = provider;

  function changeProvider(providerName: string) {
    const nextProvider =
      providers.find((item) => item.name === providerName) ?? activeProvider;
    const nextModel = nextProvider.primary_field.default;
    onChange({
      provider: nextProvider.name,
      model: nextModel,
      options: providerDefaults(nextProvider, nextModel),
    });
  }

  function changeModel(nextModel: string) {
    onChange({
      provider: activeProvider.name,
      model: nextModel,
      options: coerceProviderOptionsForModel(activeProvider, nextModel, currentOptions),
    });
  }

  function changeOption(key: string, nextValue: string) {
    onChange(
      {
        provider: activeProvider.name,
        model,
        options: {
          ...currentOptions,
          [key]: nextValue,
        },
      },
      key,
    );
  }

  const row = (label: string, children: ReactNode) =>
    layout === "rows" ? (
      <div className="pm-agent-row" key={label}>
        <span>{label}</span>
        <div>{children}</div>
      </div>
    ) : (
      <Fragment key={label}>{children}</Fragment>
    );

  return (
    <div className={`pm-agent-controls${layout === "rows" ? " rows" : ""}`}>
      {row("Agent", <>
      <label className="pm-mini-select" title="Provider">
        <SparkIcon size={10} />
        <select
          value={provider.name}
          onChange={(event) => changeProvider(event.target.value)}
        >
          {providers.map((item) => (
            <option key={item.name} value={item.name}>
              {item.label}
            </option>
          ))}
        </select>
        <span aria-hidden>▾</span>
      </label>
      <label className="pm-mini-select wide" title="Model">
        <select
          value={model}
          onChange={(event) => changeModel(event.target.value)}
        >
          {modelPickerOptions(activeProvider).map((item) => (
            <option key={item.value} value={item.value}>
              {item.label}
            </option>
          ))}
        </select>
        <span aria-hidden>▾</span>
      </label>
      {fastOption && (
        <label className="pm-fast-toggle" title={fastOption.field.label}>
          <input
            type="checkbox"
            aria-label={fastOption.field.label}
            checked={
              (currentOptions[fastOption.key]
                ?? (pinned?.fast === true ? "on" : pinned?.fast === false ? "off" : null)
                ?? fastOption.field.default) === "on"
            }
            onChange={(event) =>
              changeOption(fastOption.key, event.target.checked ? "on" : "off")
            }
          />
          <span aria-hidden />
          Fast
        </label>
      )}
      </>)}
      {row("Effort", <>
      {effortOption && (layout === "rows" ? (
        // Segmented rather than a dropdown: the choices are few, and what
        // effort costs is the reason to look at this row -- a closed select
        // hides both the range and where you sit in it. The options are
        // whatever this provider and model publish, not a fixed three.
        <SegmentedOption
          icon={<BoltIcon size={10} />}
          option={effortOption}
          value={
            currentOptions[effortOption.key]
            ?? pinned?.effort
            ?? effortOption.field.default
          }
          onPick={(next) => changeOption(effortOption.key, next)}
        />
      ) : (
        <label className="pm-mini-select" title={effortOption.field.label}>
          <BoltIcon size={10} />
          <select
            value={
              currentOptions[effortOption.key]
              ?? pinned?.effort
              ?? effortOption.field.default
            }
            onChange={(event) => changeOption(effortOption.key, event.target.value)}
          >
            {effortOption.field.values.map((item) => (
              <option key={item} value={item}>
                {optionLabel(effortOption.field, item)}
              </option>
            ))}
          </select>
          <span aria-hidden>▾</span>
        </label>
      ))}
      </>)}
      {row("Permissions", <>
      {permissionOption && (layout === "rows" ? (
        <SegmentedOption
          icon={<EyeIcon size={10} />}
          option={permissionOption}
          value={
            currentOptions[permissionOption.key]
            ?? pinned?.permissions
            ?? permissionOption.field.default
          }
          onPick={(next) => changeOption(permissionOption.key, next)}
        />
      ) : (
        <label className="pm-mini-select" title={permissionOption.field.label}>
          <EyeIcon size={10} />
          <select
            value={
              currentOptions[permissionOption.key]
              ?? pinned?.permissions
              ?? permissionOption.field.default
            }
            onChange={(event) =>
              changeOption(permissionOption.key, event.target.value)
            }
          >
            {permissionOption.field.values.map((item) => (
              <option key={item} value={item}>
                {optionLabel(permissionOption.field, item)}
              </option>
            ))}
          </select>
          <span aria-hidden>▾</span>
        </label>
      ))}
      </>)}
      {layout === "rows" && permissionOption && (
        <div className="pm-agent-row note">
          <span />
          <em>⌁ shell always asks — $ commands surface the approval card whatever the list says</em>
        </div>
      )}
    </div>
  );
}

function preferredPlanningProvider(providers: ProviderDescriptor[]): ProviderDescriptor {
  return providers.find((provider) => provider.name === "claude-acp") ?? providers[0];
}

function shallowEqualRecord(
  left: Record<string, string>,
  right: Record<string, string>,
): boolean {
  const leftKeys = Object.keys(left);
  const rightKeys = Object.keys(right);
  if (leftKeys.length !== rightKeys.length) return false;
  return leftKeys.every((key) => left[key] === right[key]);
}

function PlanningStarterRail({
  work,
  readiness,
  materializing,
  materializationStatus,
  framework,
  railWidth,
}: {
  work: WorkDetail;
  readiness: boolean;
  materializing: boolean;
  materializationStatus: PlanMaterializationStatus | null;
  framework: string;
  railWidth: number;
}) {
  const setPlanningRailWidth = useLayoutStore((s) => s.setPlanningRailWidth);
  return (
    <aside className="pm-rail pm-starter-rail">
      <div className="pm-mode-static">
        <span className="pm-mode-static-glyph">PL</span>
        <strong>Planning</strong>
        <em>{framework}</em>
      </div>
      <div className="pm-work-hero">
        <div className="pm-work-hero-id">
          <span>{work.slug} · {formatShortDate(work.created_at)}</span>
          <button
            className="btn icon sm work-hero-folder"
            title={`Open ${work.atelier_path} in the file browser`}
            onClick={() => {
              revealWork(work.slug).catch(() => {
                navigator.clipboard?.writeText(work.atelier_path).catch(() => {});
              });
            }}
            aria-label="Reveal work folder"
          >
            <FolderIcon size={12} />
          </button>
        </div>
        <strong>{work.name}</strong>
      </div>
      <div className="pm-starter-rail-fill themed-scrollbar">
        <PlanSetupSummary
          readiness={readiness}
          materializing={materializing}
          materializationStatus={materializationStatus}
        />
      </div>
      <PaneResizeHandle
        defaultValue={296}
        edge="right"
        label="Resize planning rail"
        max={PLANNING_RAIL_MAX}
        min={PLANNING_RAIL_MIN}
        value={railWidth}
        onChange={setPlanningRailWidth}
      />
    </aside>
  );
}

type PlanningTimelineStatus = "done" | "active" | "blocked" | "todo";

type PlanningTimelineStage = {
  id: string;
  label: string;
  note: string;
  status: PlanningTimelineStatus;
};

function planningTimelineStages({
  readiness,
  materializing,
  materializationStatus,
  planned,
}: {
  readiness: boolean;
  materializing: boolean;
  materializationStatus: PlanMaterializationStatus | null;
  planned: boolean;
}): PlanningTimelineStage[] {
  const state = materializationStatus?.state ?? "idle";
  const sourcePlanRequested =
    materializing ||
    planned ||
    state === "running" ||
    state === "waiting_permission" ||
    state === "stalled" ||
    state === "failed" ||
    state === "complete";
  const planningGatePassed = readiness || sourcePlanRequested;
  const materializingStatus =
    state === "failed" || state === "stalled"
      ? "blocked"
      : state === "waiting_permission"
        ? "active"
      : materializing
        ? "active"
        : planned || state === "complete"
          ? "done"
          : "todo";
  const materializingNote =
    state === "waiting_permission"
      ? "Waiting for permission"
      : state === "stalled"
        ? "Stopped before source plan"
        : state === "failed"
          ? "Materializer failed"
          : materializing
            ? "Building source plan..."
            : "Runs on Create source plan";
  return [
    {
      id: "setup",
      label: "Setup",
      status: "done",
      note: "Root · framework · profile · model",
    },
    {
      id: "discovery",
      label: "Discovery",
      status: planningGatePassed ? "done" : "active",
      note: planningGatePassed ? "Requirements captured" : "Planning chat in progress",
    },
    {
      id: "ready",
      label: "Ready",
      status: planningGatePassed ? "done" : "todo",
      note: readiness
        ? "Readiness signal received"
        : sourcePlanRequested
          ? "Source plan requested"
          : "Awaiting readiness signal",
    },
    {
      id: "materializing",
      label: "Materializing",
      status: materializingStatus,
      note: materializingNote,
    },
    {
      id: "source-plan",
      label: "Source plan",
      status: planned || state === "complete" ? "done" : "todo",
      note:
        planned || state === "complete"
          ? "WorkPlan · artifacts ready"
          : "Awaiting materializer",
    },
  ];
}

function PlanSetupSummary({
  readiness,
  materializing,
  materializationStatus,
}: {
  readiness: boolean;
  materializing: boolean;
  materializationStatus: PlanMaterializationStatus | null;
}) {
  const stages = planningTimelineStages({
    readiness,
    materializing,
    materializationStatus,
    planned: false,
  });

  return (
    <div className="plan-setup">
      <PlanningProgressTimeline stages={stages} />
    </div>
  );
}

function sourcePlanActionLabel({
  loading,
  status,
}: {
  loading: boolean;
  status: PlanMaterializationStatus | null;
}) {
  if (status?.state === "waiting_permission") return "Approve write access";
  if (loading || status?.state === "running") return "Creating plan...";
  if (status?.state === "failed" || status?.state === "stalled") {
    return "Retry source plan";
  }
  return "Create source plan";
}

function PlanningMaterializationStage({
  framework,
  workSlug,
  status,
  actionDisabled,
  actionLabel,
  onAction,
}: {
  framework: string;
  workSlug: string;
  status: PlanMaterializationStatus;
  actionDisabled: boolean;
  actionLabel: string;
  onAction: () => void;
}) {
  const [elapsed, setElapsed] = useState(0);
  const slow = elapsed >= 15;
  const waitingPermission = status.state === "waiting_permission";
  const blocked = status.state === "stalled" || status.state === "failed";
  const activity = materializationActivityCopy(status);
  const recentActivity = status.recent_activity ?? [];
  const pendingPermissions = (status.pending_permissions ?? []) as PendingPermission[];
  const [permissionError, setPermissionError] = useState<string | null>(null);
  const [permissionAttempt, setPermissionAttempt] = useState(0);

  useEffect(() => {
    const timer = window.setInterval(() => {
      setElapsed((current) => current + 1);
    }, 1000);
    return () => window.clearInterval(timer);
  }, []);

  function decidePermission(requestId: string, decision: PermissionDecision) {
    setPermissionError(null);
    void resolveWorkPlanMaterializationPermission(
      workSlug,
      requestId,
      decision,
    ).catch((reason) => {
      setPermissionError(reason instanceof Error ? reason.message : String(reason));
      setPermissionAttempt((current) => current + 1);
    });
    return true;
  }

  return (
    <div className="pm-materialize-stage themed-scrollbar">
      <div className={`pm-matz ${status.state}`}>
        <div className="pm-matz-head">
          <div className="pm-matz-htext">
            <div className="pm-matz-title">Materializing source plan</div>
            <div className="pm-matz-sub">
              {framework} is turning your discovery into source docs and an
              executable plan.
            </div>
          </div>
          <div className="pm-matz-clock">
            <span className="pm-matz-dot running" aria-hidden />
            {formatElapsed(elapsed)}
          </div>
        </div>

        <div className="pm-matz-bar" aria-hidden>
          <span />
        </div>

        {activity && (
          <div className="pm-matz-activity">
            <span className="pm-matz-mini" aria-hidden />
            <span className="pm-matz-act-text">
              {activity}
              <span className="pm-matz-ell">...</span>
            </span>
          </div>
        )}

        {recentActivity.length > 0 && (
          <div className="pm-matz-feed" aria-live="polite">
            <div className="pm-matz-feed-title">Latest activity</div>
            {recentActivity.map((item, index) => (
              <div
                className="pm-matz-feed-row"
                key={`${item.ts ?? "activity"}:${item.kind}:${index}`}
              >
                <span aria-hidden />
                <div>
                  <strong>{materializationActivityLabel(item.kind)}</strong>
                  <p>{item.text}</p>
                </div>
              </div>
            ))}
          </div>
        )}

        {waitingPermission && pendingPermissions.length > 0 && (
          <>
            <PermissionApprovalDialog
              key={`${pendingPermissions.map((item) => item.request_id).join(":")}:${permissionAttempt}`}
              pendingPermissions={pendingPermissions}
              onDecide={decidePermission}
            />
            {permissionError && <div className="pm-error">{permissionError}</div>}
          </>
        )}

        {blocked && (
          <div className="pm-matz-callout">
            <strong>
              {status.state === "failed" ? "Materializer failed" : "No recent activity"}
            </strong>
            <span>{status.message || "No source-plan report was found yet."}</span>
            <button
              type="button"
              className="btn primary sm"
              disabled={actionDisabled}
              onClick={onAction}
            >
              <SparkIcon size={12} /> {actionLabel}
            </button>
          </div>
        )}

        <div className={`pm-matz-foot${slow ? " slow" : ""}`}>
          {slow
            ? "This can take a minute for larger scopes - still working. The plan opens automatically when it's ready."
            : "Working on it. The plan opens automatically when it's ready."}
        </div>
      </div>
    </div>
  );
}

function materializationActivityCopy(status: PlanMaterializationStatus): string {
  if (status.last_event_summary) return status.last_event_summary;
  if (status.message && status.state === "running") return status.message;
  return "";
}

function materializationActivityLabel(kind: string): string {
  if (kind === "tool_call") return "Tool";
  if (kind === "tool_result") return "Tool completed";
  if (kind === "thinking_complete") return "Reasoning";
  if (kind === "message_complete") return "Update";
  if (kind === "permission_request") return "Approval needed";
  if (kind === "permission_decision") return "Approval";
  if (kind === "user_input") return "Supervisor";
  if (kind === "error") return "Error";
  return "Activity";
}

function formatElapsed(seconds: number): string {
  const minutes = String(Math.floor(seconds / 60)).padStart(2, "0");
  const remainder = String(seconds % 60).padStart(2, "0");
  return `${minutes}:${remainder}`;
}

function PlanningProgressTimeline({ stages }: { stages: PlanningTimelineStage[] }) {
  return (
    <div className="plt">
      <div className="plt-hd mono-lbl">Planning</div>
      <ol className="plt-track">
        {stages.map((stage) => (
          <li key={stage.id} className={`plt-step ${stage.status}`}>
            <span className="plt-node">
              {stage.status === "done" ? <CheckIcon size={9} /> : null}
            </span>
            <div className="plt-body">
              <div className="plt-label">{stage.label}</div>
              <div className="plt-note">{stage.note}</div>
            </div>
          </li>
        ))}
      </ol>
    </div>
  );
}

function PlanningChatCanvas({
  chatSlug,
  chatSummary,
  projects,
  works,
  planReferences,
  onOpenPlan,
  openPlanLabel,
  openPlanDisabled,
  onChatUpdated,
  onFilesEdited,
}: {
  chatSlug: string | null;
  chatSummary: ChatSummary | null;
  projects: ProjectSummary[];
  works: WorkSummary[];
  planReferences: PlanArtifact[];
  onOpenPlan?: () => void;
  openPlanLabel: string;
  openPlanDisabled: boolean;
  onChatUpdated: (chat: ChatSummary) => void;
  onFilesEdited: (paths: string[]) => void;
}) {
  return (
    <div className="pm-conversation-canvas">
      {chatSlug ? (
        <ChatTile
          chatSlug={chatSlug}
          chatSummary={chatSummary ?? undefined}
          projects={projects}
          works={works}
          planReferences={planReferences}
          presentation="planning"
          planningPlacement="center"
          onOpenPlan={onOpenPlan}
          openPlanLabel={openPlanLabel}
          openPlanDisabled={openPlanDisabled}
          onUpdated={onChatUpdated}
          onFilesEdited={onFilesEdited}
        />
      ) : (
        <div className="pm-conversation-fallback">
          <SparkIcon size={18} />
          <strong>Planning chat not found</strong>
          <span>Open the plan overview to continue with the source-backed plan.</span>
        </div>
      )}
    </div>
  );
}

function PlanningRunRail({
  artifact,
  prStatusByUrl,
  railWidth,
  run,
  onBack,
  onRun,
  onViewLoop,
}: {
  artifact: PlanArtifact;
  prStatusByUrl: Record<string, string>;
  railWidth: number;
  run: PlanArtifactRun;
  onBack: () => void;
  onRun: (runId: string) => void;
  onViewLoop: () => void;
}) {
  const setPlanningRailWidth = useLayoutStore((state) => state.setPlanningRailWidth);
  const storyPullRequests = useMemo(() => {
    const seen = new Set<string>();
    const rows: PrLifecycle[] = [];
    for (const item of [...artifact.runs].reverse()) {
      const pr = item.pr;
      if (!pr) continue;
      const key = pr.url ?? String(pr.number ?? "");
      if (!key || seen.has(key)) continue;
      seen.add(key);
      // The run snapshot identifies the PR; the polled artifact row says what
      // it is now. A run only refreshes its own copy when someone asks it to,
      // so reading the status from there shows a PR as open long after it
      // merged. Fall back to the snapshot when no artifact row matches.
      const live = pr.url ? prStatusByUrl[pr.url] : undefined;
      rows.push(live ? { ...pr, status: live as PrLifecycle["status"] } : pr);
    }
    return rows;
  }, [artifact.runs, prStatusByUrl]);
  return (
    <div className="pm-run-rail-host">
      <RunRail
        anchor={(
          <button type="button" className="pm-run-story-anchor" onClick={onBack}>
            <span>← Story · {artifact.id}</span>
            <strong>{artifact.title}</strong>
            <em>{artifact.kind} · {artifact.readiness.replaceAll("_", " ")}</em>
          </button>
        )}
        definition={run.loop_definition ?? null}
        definitionMeta="pinned for selected run"
        runs={artifact.runs.map((item) => planningRunData(artifact, item))}
        selectedRunId={run.id}
        onRun={onRun}
        onViewLoop={onViewLoop}
      >
        {storyPullRequests.length > 0 && (
          <section className="loop-mode-rail-section">
            <header><span>Pull requests</span><em>{storyPullRequests.length}</em></header>
            <div className="loop-mode-rail-section-body themed-scrollbar">
              {storyPullRequests.map((pr) => (
                <a
                  className="loop-mode-rail-row"
                  href={pr.url ?? undefined}
                  key={pr.url ?? String(pr.number)}
                  target={pr.url ? "_blank" : undefined}
                  rel={pr.url ? "noreferrer" : undefined}
                >
                  <span>PR</span>
                  <strong>{pr.number ? `#${pr.number} ` : ""}{pr.title}</strong>
                  <em className={prStatusTone(pr.status)}>{pr.status}</em>
                </a>
              ))}
            </div>
          </section>
        )}
      </RunRail>
      <PaneResizeHandle
        defaultValue={296}
        edge="right"
        label="Resize run rail"
        max={PLANNING_RAIL_MAX}
        min={PLANNING_RAIL_MIN}
        value={railWidth}
        onChange={setPlanningRailWidth}
      />
    </div>
  );
}

function PlanRail({
  work,
  plan,
  tree,
  counts,
  activeId,
  railWidth,
  onEpic,
  onArtifact,
  onSource,
  readOnly,
  onCreateBug,
}: {
  work: WorkDetail;
  plan: WorkPlan;
  tree: PlanTree;
  counts: PlanCounts;
  activeId: string | null;
  railWidth: number;
  onEpic: (id: string) => void;
  onArtifact: (id: string) => void;
  onSource: (id: string) => void;
  readOnly: boolean;
  onCreateBug: () => void;
}) {
  const [open, setOpen] = useState(true);
  const setPlanningRailWidth = useLayoutStore((s) => s.setPlanningRailWidth);
  const activeProgress = counts.running + counts.review + counts.ready + counts.draft;
  // One entry per story, from its latest run that opened a PR -- same rule as
  // the story panel. Earlier runs' pull requests are history: their state is
  // never refreshed again, so listing them shows stale "open" rows for work
  // that has long since merged or been abandoned.
  const pullRequests = plan.artifacts.flatMap((item) => {
    const run = [...item.runs].reverse().find((entry) => entry.pr);
    return run?.pr ? [{ artifactId: item.id, runId: run.id, pr: run.pr }] : [];
  });
  return (
    <aside className="pm-rail">
      <div className="pm-mode-static">
        <span className="pm-mode-static-glyph">PL</span>
        <strong>Planning</strong>
        <em>{plan.framework.toUpperCase()}</em>
      </div>
      <div className="pm-work-hero">
        <span>{work.slug} · {formatShortDate(work.created_at)}</span>
        <strong>{work.name}</strong>
      </div>
      <div className="pm-meta">
        <div><span>Framework</span><strong>{plan.framework.toUpperCase()} · {plan.depth}</strong></div>
        <div className="pm-progress">
          <div className="pm-bar">
            <i style={{ width: `${counts.total === 0 ? 0 : (counts.done / counts.total) * 100}%` }} />
            <b style={{ width: `${counts.total === 0 ? 0 : (activeProgress / counts.total) * 100}%` }} />
          </div>
          <div className="pm-bar-cap">
            <span><strong>{counts.done}</strong> done</span>
            <span>{activeProgress} in progress</span>
            <span>{counts.total} items</span>
          </div>
        </div>
      </div>
      <div className="pm-rail-scroll themed-scrollbar">
        <div className="pm-section-hd">
          <span>Plan outline</span>
        </div>
        <div className="pm-tree">
          {tree.sources.length > 0 && (
            <>
              <div className="pm-epic pm-doc-group">
                <span className="pm-tree-icon"><DocIcon size={12} /></span>
                <div className="pm-epic-main">
                  <span>DOCS</span>
                  <strong>Source documents</strong>
                  <em>{tree.sources.length}</em>
                </div>
              </div>
              <div className="pm-tree-kids pm-source-kids">
                {tree.sources.map((source) => (
                  <button
                    key={source.id}
                    className={"pm-tree-item source" + (activeId === source.id ? " selected" : "")}
                    onClick={() => onSource(source.id)}
                    title={source.source_ref}
                  >
                    <span>
                      <em>{source.kind}</em>
                      {source.path.split(/[\\/]/).pop() || source.title}
                    </span>
                    <SourceTreeStatus source={source} />
                  </button>
                ))}
              </div>
            </>
          )}
          <div className={"pm-epic" + (activeId === PRIMARY_EPIC_ID ? " selected" : "")}>
            <button className={"pm-twist" + (open ? " open" : "")} onClick={() => setOpen((v) => !v)} aria-label="Toggle epic">
              <ChevronRightIcon size={12} />
            </button>
            <button className="pm-epic-main" onClick={() => onEpic(PRIMARY_EPIC_ID)}>
              <span>{PRIMARY_EPIC_ID}</span>
              <strong>{work.name}</strong>
              <em>{counts.done}/{counts.total}</em>
            </button>
          </div>
          {open && (
            <div className="pm-tree-kids">
              {tree.executable.map((item) => (
                <button
                  key={item.id}
                  className={"pm-tree-item" + (activeId === item.id ? " selected" : "")}
                  onClick={() => onArtifact(item.id)}
                >
                  <span>
                    <em>{item.kind}</em>
                    {item.path.split(/[\\/]/).pop() || item.title}
                  </span>
                  <TreeStatus status={uiStatus(item)} />
                </button>
              ))}
            </div>
          )}
        </div>
        <div className="pm-section-hd">
          <span>Pull requests</span>
          <span>{pullRequests.length}</span>
        </div>
        <div className="pm-rail-tracking">
          {pullRequests.map(({ artifactId, runId, pr }) => (
            <button
              key={`${artifactId}:${runId}`}
              className="pm-track-row rail"
              onClick={() => onArtifact(artifactId)}
            >
              <span className={`pm-track-kind pr ${pr.status}`}>{pr.status}</span>
              <strong>{pr.number ? `#${pr.number} ` : ""}{pr.title}</strong>
              <small>{artifactId}</small>
            </button>
          ))}
          {pullRequests.length === 0 && (
            <div className="pm-empty-state">No pull requests.</div>
          )}
        </div>
      </div>
      <div className="pm-footstrip">
        <button
          className="btn ghost sm"
          type="button"
          disabled={readOnly || tree.executable.length === 0}
          onClick={onCreateBug}
        >
          <BugIcon size={11} /> Add bug
        </button>
      </div>
      <PaneResizeHandle
        defaultValue={296}
        edge="right"
        label="Resize planning rail"
        max={PLANNING_RAIL_MAX}
        min={PLANNING_RAIL_MIN}
        value={railWidth}
        onChange={setPlanningRailWidth}
      />
    </aside>
  );
}

function PlanBugDialog({
  targets,
  defaultArtifactId,
  onClose,
  onCreate,
}: {
  targets: PlanArtifact[];
  defaultArtifactId: string;
  onClose: () => void;
  onCreate: (artifactId: string, title: string, description: string) => Promise<void>;
}) {
  const relatedStories = targets.filter((target) => {
    const status = uiStatus(target);
    return target.kind === "story" && (status === "done" || status === "review");
  });
  const initialArtifactId = relatedStories.some((story) => story.id === defaultArtifactId)
    ? defaultArtifactId
    : relatedStories[0]?.id ?? "";
  const [artifactId, setArtifactId] = useState(initialArtifactId);
  const [problem, setProblem] = useState("");
  const [storyFilter, setStoryFilter] = useState("");
  const [sentryLink, setSentryLink] = useState("");
  const [contextUrl, setContextUrl] = useState("");
  const [submitting, setSubmitting] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const selectedStory = relatedStories.find((story) => story.id === artifactId) ?? null;
  const targetArtifactId = artifactId || targets[0]?.id || "";
  const visibleStories = relatedStories.filter((story) =>
    `${story.id} ${story.title}`.toLowerCase().includes(storyFilter.trim().toLowerCase()),
  );
  const canSubmit = Boolean(targetArtifactId && problem.trim() && !submitting);

  useEffect(() => {
    const onKey = (event: KeyboardEvent) => {
      if (event.key === "Escape" && !submitting) onClose();
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [onClose, submitting]);

  async function submit() {
    if (!canSubmit) return;
    setSubmitting(true);
    setError(null);
    try {
      await onCreate(
        targetArtifactId,
        bugTitleFromProblem(problem),
        bugDescriptionFromInputs(problem, sentryLink, contextUrl),
      );
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err));
      setSubmitting(false);
    }
  }

  return (
    <div className="scrim planning-bug-scrim" onClick={() => !submitting && onClose()}>
      <form
        className="modal planning-bug-modal"
        role="dialog"
        aria-modal="true"
        onClick={(event) => event.stopPropagation()}
        onSubmit={(event) => {
          event.preventDefault();
          void submit();
        }}
      >
        <div className="modal-hd">
          <div>
            <h3>New bug</h3>
            <div className="sub">Lightweight item — launches without full planning</div>
          </div>
          <button
            className="btn icon"
            type="button"
            onClick={onClose}
            aria-label="Close"
            disabled={submitting}
          >
            ×
          </button>
        </div>
        <div className="modal-bd">
          <label className="field planning-bug-field">
            <span className="label">What's broken?</span>
            <textarea
              className="textarea planning-bug-problem"
              autoFocus
              rows={4}
              placeholder="e.g. Login 500s on Safari 16.3 when no passkey is enrolled..."
              value={problem}
              onChange={(event) => setProblem(event.target.value)}
              disabled={submitting}
            />
          </label>

          <div className="field planning-bug-field">
            <span className="label">
              Related story <span className="hint">— completed or in-review</span>
            </span>
            {selectedStory ? (
              <div className="planning-bug-related-chip">
                <span className="planning-bug-related-glyph">ST</span>
                <span className="planning-bug-related-title">{selectedStory.title}</span>
                <span className="planning-bug-related-id">{selectedStory.id}</span>
                <button
                  className="btn ghost icon sm"
                  type="button"
                  onClick={() => setArtifactId("")}
                  disabled={submitting}
                  aria-label="Clear related story"
                >
                  ×
                </button>
              </div>
            ) : (
              <>
                <input
                  className="input planning-bug-filter"
                  placeholder="Filter stories..."
                  value={storyFilter}
                  onChange={(event) => setStoryFilter(event.target.value)}
                  disabled={submitting}
                />
                <div className="planning-bug-story-list themed-scrollbar">
                  {visibleStories.map((story) => {
                    const status = uiStatus(story);
                    return (
                      <button
                        key={story.id}
                        type="button"
                        className="planning-bug-story"
                        onClick={() => setArtifactId(story.id)}
                        disabled={submitting}
                      >
                        <span className="planning-bug-story-id">{story.id}</span>
                        <span className="planning-bug-story-title">{story.title}</span>
                        <span className="planning-bug-story-status">{bugStoryStatusLabel(status)}</span>
                      </button>
                    );
                  })}
                  {relatedStories.length === 0 && (
                    <div className="planning-bug-empty">No completed or in-review stories yet.</div>
                  )}
                  {relatedStories.length > 0 && visibleStories.length === 0 && (
                    <div className="planning-bug-empty">No matching stories.</div>
                  )}
                </div>
              </>
            )}
          </div>

          <label className="field planning-bug-field">
            <span className="label">
              Sentry link <span className="hint">— optional</span>
            </span>
            <span className="planning-bug-prefixed-input" data-source="sentry">
              <span>SN</span>
              <input
                placeholder="sentry.io/issues/..."
                value={sentryLink}
                onChange={(event) => setSentryLink(event.target.value)}
                disabled={submitting}
              />
            </span>
          </label>

          <label className="field planning-bug-field">
            <span className="label">
              Context URL <span className="hint">— optional</span>
            </span>
            <input
              className="input planning-bug-filter"
              placeholder="Slack thread, doc, dashboard..."
              value={contextUrl}
              onChange={(event) => setContextUrl(event.target.value)}
              disabled={submitting}
            />
          </label>
          {error && <div className="form-error">{error}</div>}
        </div>
        <div className="modal-ft">
          <button className="btn" type="button" onClick={onClose} disabled={submitting}>
            Cancel
          </button>
          <span className="spacer" />
          <button className="btn primary" type="submit" disabled={!canSubmit}>
            {submitting ? "Creating…" : "+ Create bug"}
          </button>
        </div>
      </form>
    </div>
  );
}

function bugTitleFromProblem(problem: string): string {
  const firstLine = problem.trim().split(/\r?\n/)[0]?.trim() ?? "";
  if (firstLine.length <= 88) return firstLine;
  return `${firstLine.slice(0, 85).trimEnd()}...`;
}

function bugDescriptionFromInputs(
  problem: string,
  sentryLink: string,
  contextUrl: string,
): string {
  const references = [
    sentryLink.trim() ? `- Sentry: ${sentryLink.trim()}` : "",
    contextUrl.trim() ? `- Context: ${contextUrl.trim()}` : "",
  ].filter(Boolean);
  if (references.length === 0) return problem.trim();
  return [problem.trim(), "", "Additional references:", ...references].join("\n");
}

function bugStoryStatusLabel(status: PlanUiStatus): string {
  if (status === "done") return "DONE";
  if (status === "review") return "REVIEW";
  return status.toUpperCase();
}

function PlanOverview({
  plan,
  tree,
  counts,
  tab,
  onTab,
  onArtifact,
  onSource,
  onAccept,
}: {
  plan: WorkPlan;
  tree: PlanTree;
  counts: PlanCounts;
  tab: PlanOverviewTab;
  onTab: (tab: PlanOverviewTab) => void;
  onArtifact: (id: string) => void;
  onSource: (id: string) => void;
  onAccept: () => void;
}) {
  const attention = attentionItems(plan, tree);
  const blocked = tree.executable.filter((a) => uiStatus(a) === "blocked" || uiStatus(a) === "gated");
  const completed = tree.executable.filter((a) => uiStatus(a) === "done");
  const pending = tree.executable.filter((a) => uiStatus(a) === "draft");
  const launchable = tree.executable.filter((a) => a.launchable);
  return (
    <>
      <div className="pm-tabs">
        {[
          ["summary", "Summary", undefined],
          ["blocking", "Blocking", counts.blocked],
          ["completed", "Completed", counts.done],
          ["pending", "Pending", counts.ready + counts.draft],
        ].map(([id, label, count]) => (
          <button key={id} className={tab === id ? "active" : ""} onClick={() => onTab(id as PlanOverviewTab)}>
            {label}{typeof count === "number" && count > 0 && <span>{count}</span>}
          </button>
        ))}
      </div>
      <div className="pm-overview themed-scrollbar">
        <div className="pm-overview-wrap">
          {tab === "summary" && (
            <>
              <SectionTitle label="Planning progress" />
              <div className="pm-overview-progress">
                <PlanProgress plan={plan} counts={counts} />
                <InspectorPanel title="Plan readiness">
                  <Kv label="Source docs" value={`${tree.sources.filter((source) => source.status !== "changed").length}/${tree.sources.length} reviewed`} tone={tree.sources.some((source) => source.status === "changed") ? "warn" : "good"} />
                  <Kv label="Executable" value={`${counts.total} items`} />
                  <Kv label="Blocked" value={String(counts.blocked)} tone={counts.blocked > 0 ? "danger" : "good"} />
                  <Kv label="Approval" value={plan.approved_at ? "approved" : "pending"} tone={plan.approved_at ? "good" : "warn"} />
                </InspectorPanel>
              </div>
              <SectionTitle tone="alert" icon={<SparkIcon size={12} />} label="Needs your attention" count={attention.length} />
              <div className="pm-attn-list">
                {attention.length === 0 ? (
                  <div className="pm-empty-state"><CheckIcon size={18} /> Nothing needs you right now.</div>
                ) : attention.map((item) => (
                  <button
                    key={item.id}
                    className="pm-attn-card"
                    data-kind={item.kind}
                    onClick={() => {
                      if (item.target.kind === "source") onSource(item.target.id);
                      else if (item.target.kind === "artifact") onArtifact(item.target.id);
                      else onAccept();
                    }}
                  >
                    <span><AttentionIcon icon={item.icon} /></span>
                    <strong>{item.title}</strong>
                    <small>{item.sub}</small>
                    <em>{item.action} <ChevronRightIcon size={12} /></em>
                  </button>
                ))}
              </div>
              <SectionTitle label="Active & recent work" count={tree.executable.length} />
              <ArtifactList items={tree.executable.slice(0, 5)} onOpen={onArtifact} />
            </>
          )}
          {tab === "blocking" && (
            <>
              <SectionTitle tone="alert" label="Blocked artifacts" count={blocked.length} />
              <ArtifactList items={blocked} onOpen={onArtifact} empty="No blocked artifacts." />
            </>
          )}
          {tab === "completed" && (
            <>
              <SectionTitle label="Accepted artifacts" count={completed.length} />
              <ArtifactList items={completed} onOpen={onArtifact} empty="Nothing accepted yet." />
            </>
          )}
          {tab === "pending" && (
            <>
              <SectionTitle label="Decisions waiting on you" />
              <div className="pm-list">
                {!plan.approved_at && (
                  <button className="pm-list-row" onClick={onAccept}>
                    <span className="glyph">PL</span>
                    <strong>Approve the latest plan</strong>
                    <StatusPill status="draft" />
                    <ChevronRightIcon className="pm-row-chevron" size={15} />
                  </button>
                )}
                {tree.sources.filter((s) => s.status === "changed").map((source) => (
                  <button key={source.id} className="pm-list-row" onClick={() => onSource(source.id)}>
                    <span className="glyph">{source.id.toUpperCase()}</span>
                    <strong>Review {source.title}</strong>
                    <StatusPill status="review" />
                    <ChevronRightIcon className="pm-row-chevron" size={15} />
                  </button>
                ))}
              </div>
              <SectionTitle label="Ready to launch" count={launchable.length} />
              <ArtifactList items={launchable} onOpen={onArtifact} empty="No launchable artifacts yet." />
              <SectionTitle label="Pending detail" count={pending.length} />
              <ArtifactList items={pending} onOpen={onArtifact} empty="No pending artifacts." />
            </>
          )}
        </div>
      </div>
    </>
  );
}

function EpicDetail({
  epic,
  plan,
  saving,
  onArtifact,
  onApprovePlan,
}: {
  epic: PlanEpic | null;
  plan: WorkPlan;
  saving: boolean;
  onArtifact: (id: string) => void;
  onApprovePlan: () => void;
}) {
  if (!epic) return <div className="pm-loading">Epic not found.</div>;
  const sectionLabel = epic.children.every((item) => item.kind === "story")
    ? "Stories"
    : "Plan items";
  return (
    <div className="pm-art-body themed-scrollbar">
      <div className="pm-epic-wrap">
        <div className="pm-kicker">
          <span>plan</span>
          <span>/</span>
          <span>{epic.id}</span>
        </div>
        <h1>{epic.title}</h1>
        <div className="pm-meta-row">
          <span className="chip info">epic</span>
          <span>{epic.counts.done}/{epic.counts.total} items done</span>
        </div>
        <p className="pm-epic-desc">{epic.description}</p>
        {!plan.approved_at && (
          <div className="pm-doc-banner">
            <CheckIcon size={14} />
            <div>
              <strong>Plan needs approval before launch</strong>
              <span>Approve the current source snapshot so ready items can launch agents.</span>
            </div>
            <button className="btn primary sm" onClick={onApprovePlan} disabled={saving}>
              <CheckIcon size={12} /> Approve plan
            </button>
          </div>
        )}
        <SectionTitle label={sectionLabel} count={epic.children.length} />
        <ArtifactList
          items={epic.children}
          onOpen={onArtifact}
          empty="No executable artifacts yet."
        />
      </div>
    </div>
  );
}

function ArtifactDetail({
  makeLinkTo,
  artifact,
  detail,
  draft,
  readOnly,
  saving,
  onDraftChange,
  onSave,
  onReset,
  onResolveLoopBlocker,
  onOpenRun,
  onLaunch,
  onEpic,
  onApprovePlan,
}: {
  artifact: PlanArtifact | null;
  detail: PlanArtifactDetail | null;
  draft: string;
  makeLinkTo: (fromPath: string) => (href: string) => (() => void) | null;
  readOnly: boolean;
  saving: boolean;
  onDraftChange: (value: string) => void;
  onSave: () => void;
  onReset: () => void;
  onResolveLoopBlocker: (
    artifact: PlanArtifact,
    runId: string,
    agentSlug: string,
  ) => void;
  onOpenRun: (runId: string) => void;
  onLaunch: (detail: PlanArtifactDetail) => void;
  onEpic: () => void;
  onApprovePlan: () => void;
}) {
  // Above the guards: a hook after an early return runs a different number of
  // times once `detail` arrives, which is a hooks-order crash on the ordinary
  // loading path. Memoized because `MarkdownText` is memo'd to avoid
  // re-parsing markdown on every keystroke elsewhere in the tree.
  const linkTo = useMemo(
    () => makeLinkTo(detail?.artifact.path ?? artifact?.path ?? ""),
    [makeLinkTo, detail?.artifact.path, artifact?.path],
  );
  if (!artifact) return <div className="pm-loading">Artifact not found.</div>;
  if (!detail) return <div className="pm-loading">Loading source…</div>;
  const status = uiStatus(detail.artifact);
  const editable = !readOnly && (status === "draft" || status === "ready" || status === "blocked");
  const dirty = draft !== detail.content;
  const latestRun = detail.artifact.runs.at(-1) ?? null;
  const latestPrRun = [...detail.artifact.runs].reverse().find((run) => run.pr) ?? null;
  const latestPr = latestPrRun?.pr ?? null;
  const latestLoopStatus = latestRun ? loopStatus(latestRun) : null;
  const latestRunData = latestRun ? planningRunData(detail.artifact, latestRun) : null;
  const latestRunReviewable = latestLoopStatus === "completed" || latestLoopStatus === "awaiting_approval";
  // The latest run's PR only. Listing every run's meant a cancelled or
  // superseded run kept its pull request on screen forever: nothing refreshes
  // a finished run's PR state, so it stayed frozen at whatever it last said --
  // a story whose current PR was merged still showed an "open" one from a run
  // abandoned weeks earlier.
  const pullRequests = latestPr
    ? [{ runId: latestPrRun!.id, pr: latestPr }]
    : [];
  const approvalBlocker = detail.artifact.launch_blockers.find((blocker) =>
    blocker.toLowerCase().startsWith("approve "),
  );
  return (
    <div className="pm-art-body themed-scrollbar">
      <div className="pm-art-wrap">
        <article className="pm-art-doc">
          <div className="pm-kicker">
            <span>plan</span>
            <span>/</span>
            <button type="button" onClick={onEpic}>{PRIMARY_EPIC_ID}</button>
            <span>/</span>
            <span>{detail.artifact.id}</span>
          </div>
          <h1>{detail.artifact.title}</h1>
          <div className="pm-meta-row">
            <span className="chip info">{detail.artifact.kind}</span>
            <StatusPill status={status} />
            <span>source · {detail.artifact.path}</span>
            <span className={editable ? "pm-edit-state editable" : "pm-edit-state"}>{editable ? "editable" : "read-only"}</span>
          </div>
          {!editable && (
            <div className="pm-gate-note">
              <CheckIcon size={12} /> This item is locked because it is {status}.
            </div>
          )}
          {detail.artifact.readiness === "needs_detail" && (
            <div className="pm-missing">
              <SparkIcon size={15} />
              <div><strong>Needs more detail</strong><span>Add acceptance criteria or clarify scope before launch.</span></div>
            </div>
          )}
          {detail.artifact.launch_blockers.length > 0 && (
            <div className="pm-missing">
              <SparkIcon size={15} />
              <div>
                <strong>Launch blockers</strong>
                <span>{detail.artifact.launch_blockers.join(" ")}</span>
                {approvalBlocker && (
                  <button className="btn primary sm" onClick={onApprovePlan} disabled={saving}>
                    <CheckIcon size={12} /> Approve plan
                  </button>
                )}
              </div>
            </div>
          )}
          <RichMarkdownEditor
            className="pm-doc-editor"
            value={draft}
            onChange={onDraftChange}
            onLinkTo={linkTo}
            readOnly={!editable}
          />
          <div className="pm-doc-actions">
            <button className="btn primary" onClick={onSave} disabled={!dirty || saving || !editable}>Save source</button>
            <button className="btn" onClick={onReset} disabled={!dirty || saving}>Reset</button>
          </div>
        </article>
        <aside className="pm-art-aside">
          {detail.artifact.executable && (
            <InspectorPanel title="Start work">
              <button
                className={`btn sm pm-start-work${latestRun ? ` status ${loopStatusTone(latestLoopStatus)}` : " primary"}`}
                disabled={readOnly || !detail.artifact.launchable || latestRun !== null}
                onClick={() => onLaunch(detail)}
              >
                <LoopIcon size={12} /> {latestRun ? loopStatusLabel(latestLoopStatus) : "Start work"}
              </button>
            </InspectorPanel>
          )}
          <InspectorPanel title="Readiness">
            <Kv label="Criteria" value={detail.artifact.readiness === "ready" ? "defined" : "missing"} tone={detail.artifact.readiness === "ready" ? "good" : "danger"} />
            <Kv label="Estimate" value={detail.artifact.readiness === "ready" ? "lightweight" : "missing"} />
            <Kv label="Deps" value={detail.artifact.dependencies.length > 0 ? detail.artifact.dependencies.join(", ") : "clear"} tone={detail.artifact.launch_blockers.length > 0 ? "warn" : "good"} />
            {latestPr && (
              <Kv
                label="PR"
                value={`${latestPr.number ? `#${latestPr.number} ` : ""}${latestPr.status}`}
                tone={prReadinessTone(latestPr.status)}
              />
            )}
          </InspectorPanel>
          <InspectorPanel title="Latest run">
            {latestRun ? (
              <>
                <button className="pm-latest-run-card" type="button" onClick={() => onOpenRun(latestRun.id)}>
                  <span className="pm-latest-run-head">
                    <span className={`pm-latest-run-status ${loopStatusTone(latestLoopStatus)}`}>
                      {loopStatusActive(latestLoopStatus) && <i />}
                      {loopStatusLabel(latestLoopStatus)}
                    </span>
                    <em>open run ▸</em>
                  </span>
                  <strong>run {latestRunData?.number ?? 1} · {latestRun.loop_definition_name || latestRun.loop_definition_id || "Loop"}</strong>
                  <span className="pm-latest-run-meta">rev {latestRun.loop_definition_revision || "legacy"} · {latestRun.agent_slug}</span>
                  {(latestRun.loop_status_reason || latestRun.summary) && (
                    <span className="pm-latest-run-note">{latestRun.loop_status_reason || latestRun.summary}</span>
                  )}
                </button>
                {latestRun.loop_latest_assessment.length > 0 && (
                  <div className="pm-panel-note">
                    {latestRun.loop_latest_assessment.join(" ")}
                  </div>
                )}
                <div className="pm-inline-actions">
                  {latestLoopStatus === "blocked_user" && (
                    <button
                      className="btn primary sm"
                      disabled={saving}
                      onClick={() => latestRun.loop_review_gate ? onOpenRun(latestRun.id) : onResolveLoopBlocker(detail.artifact, latestRun.id, latestRun.agent_slug)}
                    >
                      {latestRun.loop_review_gate ? "Review findings" : "Mark resolved"}
                    </button>
                  )}
                  {latestRunReviewable && (
                    <button className="btn primary sm" disabled={saving} onClick={() => onOpenRun(latestRun.id)}>Review result</button>
                  )}
                </div>
              </>
            ) : (
              <div className="pm-panel-note">No agent run recorded yet.</div>
            )}
          </InspectorPanel>
          {pullRequests.length > 0 && (
            <InspectorPanel title="Pull requests">
              <div className="pm-track-list">
                {pullRequests.map(({ runId, pr }) => (
                  <a
                    key={runId}
                    className="pm-track-row"
                    href={pr.url}
                    target="_blank"
                    rel="noreferrer"
                  >
                    <span className={`pm-track-kind pr ${pr.status}`}>{pr.status}</span>
                    <strong>{pr.number ? `#${pr.number} ` : ""}{pr.title}</strong>
                    <small>{pr.branch} → {pr.base}</small>
                  </a>
                ))}
              </div>
            </InspectorPanel>
          )}
        </aside>
      </div>
    </div>
  );
}

function SourceDoc({
  makeLinkTo,
  artifact,
  detail,
  draft,
  readOnly,
  saving,
  onDraftChange,
  onSave,
  onReset,
  onApprovePlan,
}: {
  artifact: PlanArtifact | null;
  detail: PlanArtifactDetail | null;
  draft: string;
  makeLinkTo: (fromPath: string) => (href: string) => (() => void) | null;
  readOnly: boolean;
  saving: boolean;
  onDraftChange: (value: string) => void;
  onSave: () => void;
  onReset: () => void;
  onApprovePlan: () => void;
}) {
  const linkTo = useMemo(
    () => makeLinkTo(artifact?.path ?? ""),
    [makeLinkTo, artifact?.path],
  );
  if (!artifact) return <div className="pm-loading">Source document not found.</div>;
  if (!detail) return <div className="pm-loading">Loading source…</div>;
  const dirty = draft !== detail.content;
  return (
    <div className="pm-art-body themed-scrollbar">
      <article className="pm-source-doc">
        <div className="pm-kicker">plan / sources / {artifact.id}</div>
        <h1>{artifact.title}</h1>
        <div className="pm-meta-row">
          <span>{artifact.source_ref}</span>
          <span className={readOnly ? "pm-edit-state" : "pm-edit-state editable"}>
            {readOnly ? "read-only" : "editable"}
          </span>
        </div>
        {artifact.status === "changed" && (
          <div className="pm-doc-banner">
            <SparkIcon size={14} />
            <div><strong>Source changed</strong><span>Save edits to approve this file, or approve the latest plan snapshot.</span></div>
            <button className="btn primary sm" onClick={onApprovePlan} disabled={saving}>
              <CheckIcon size={12} /> Approve plan
            </button>
          </div>
        )}
        <RichMarkdownEditor
          className="pm-doc-editor source"
          value={draft}
          onChange={onDraftChange}
          onLinkTo={linkTo}
          readOnly={readOnly}
        />
        <div className="pm-doc-actions">
          <button className="btn primary" onClick={onSave} disabled={!dirty || saving}>Save source</button>
          <button className="btn" onClick={onReset} disabled={!dirty || saving}>Reset</button>
        </div>
      </article>
    </div>
  );
}

function ApprovePlan({
  plan,
  counts,
  saving,
  onBack,
  onApprove,
}: {
  plan: WorkPlan;
  counts: PlanCounts;
  saving: boolean;
  onBack: () => void;
  onApprove: () => void;
}) {
  return (
    <div className="pm-art-body themed-scrollbar">
      <article className="pm-source-doc narrow">
        <div className="pm-kicker">plan / approve</div>
        <h1>Approve the latest plan</h1>
        <p className="pm-copy">
          Approval marks this source index as the launch baseline. Ready items can then launch agents scoped to one artifact.
        </p>
        <PlanProgress plan={plan} counts={counts} />
        <div className="pm-doc-actions">
          <button className="btn" onClick={onBack}>Not yet</button>
          <button className="btn primary" onClick={onApprove} disabled={saving}>
            <CheckIcon size={12} /> Approve plan
          </button>
        </div>
      </article>
    </div>
  );
}

function SectionTitle({
  label,
  count,
  tone,
  icon,
}: {
  label: string;
  count?: number;
  tone?: "alert";
  icon?: ReactNode;
}) {
  return (
    <div className={"pm-sec-title" + (tone ? " " + tone : "")}>
      {icon}
      <span>{label}</span>
      {count !== undefined && <em>{count}</em>}
    </div>
  );
}

type AttentionKind = "diff" | "accept" | "blocked" | "launch" | "review" | "readiness";
type AttentionIconKind = "doc" | "spark" | "agent" | "eye" | "check";

function AttentionIcon({ icon }: { icon: AttentionIconKind }) {
  if (icon === "doc") return <DocIcon size={14} />;
  if (icon === "agent") return <AgentIcon size={14} />;
  if (icon === "eye") return <EyeIcon size={14} />;
  if (icon === "check") return <CheckIcon size={14} />;
  return <SparkIcon size={14} />;
}

function PlanProgress({ plan, counts }: { plan: WorkPlan; counts: PlanCounts }) {
  const active = counts.running + counts.review + counts.ready + counts.draft;
  const donePct = counts.total === 0 ? 0 : (counts.done / counts.total) * 100;
  const activePct = counts.total === 0 ? 0 : (active / counts.total) * 100;
  return (
    <div className="pm-plan-progress">
      <div className="pm-plan-progress-head">
        <span>Framework</span>
        <strong>{plan.framework.toUpperCase()} · {plan.depth}</strong>
      </div>
      <div className="pm-plan-progress-bar">
        <i style={{ width: `${donePct}%` }} />
        <b style={{ width: `${activePct}%` }} />
      </div>
      <div className="pm-plan-progress-caption">
        <span><strong>{counts.done}</strong> done</span>
        <span><strong>{active}</strong> in progress</span>
        <span><strong>{counts.total}</strong> items</span>
      </div>
    </div>
  );
}

function ArtifactList({
  items,
  onOpen,
  empty,
}: {
  items: PlanArtifact[];
  onOpen: (id: string) => void;
  empty?: string;
}) {
  if (items.length === 0) {
    return <div className="pm-empty-state">{empty ?? "No artifacts."}</div>;
  }
  return (
    <div className="pm-list">
      {items.map((item) => (
        <button key={item.id} className="pm-list-row" data-kind={item.kind} onClick={() => onOpen(item.id)}>
          <span className="glyph">{item.kind.toUpperCase()}</span>
          <strong>{item.path.split(/[\\/]/).pop() || item.title}</strong>
          <StatusPill status={uiStatus(item)} />
          <ChevronRightIcon className="pm-row-chevron" size={15} />
        </button>
      ))}
    </div>
  );
}

function InspectorPanel({ title, children }: { title: string; children: ReactNode }) {
  return (
    <section className="pm-panel">
      <h3>{title}</h3>
      <div>{children}</div>
    </section>
  );
}

/* Readiness answers "does this story still need work?", so a merged PR reads
   as good and a closed one as a problem. */
function prReadinessTone(status: string): "merged" | "warn" | "danger" | undefined {
  if (status === "merged") return "merged";
  if (status === "closed") return "danger";
  return undefined;
}

function Kv({ label, value, tone }: { label: string; value: string; tone?: "good" | "warn" | "danger" | "merged" }) {
  return (
    <div className="pm-kv">
      <span>{label}</span>
      <strong className={tone ?? ""}>{value}</strong>
    </div>
  );
}

function loopStatusLabel(status: LoopStatus | null): string {
  if (!status) return "unknown";
  return status.replaceAll("_", " ");
}

function loopStatusTone(status: LoopStatus | null): "good" | "info" | "warn" | "danger" {
  if (status === "accepted" || status === "cleaned") return "good";
  if (
    status === "completed" ||
    status === "awaiting_approval" ||
    status === "pending" ||
    status === "running" ||
    status === "waiting_report" ||
    status === "assessing" ||
    status === "needs_agent"
  ) return "info";
  if (status === "blocked_user" || status === "failed" || status === "cancelled") {
    return "danger";
  }
  return "warn";
}

function loopStatusActive(status: LoopStatus | null): boolean {
  return status === "pending" ||
    status === "running" ||
    status === "waiting_report" ||
    status === "assessing" ||
    status === "needs_agent";
}

function TreeStatus({ status }: { status: PlanUiStatus }) {
  return (
    <span className={"pm-tree-status " + status}>
      {status === "running" && <i />}
      {status === "done" ? <CheckIcon size={10} /> : status === "gated" ? "waiting" : status}
    </span>
  );
}

function SourceTreeStatus({ source }: { source: PlanArtifact }) {
  if (source.status !== "changed") return null;
  return (
    <span className="pm-tree-status source changed">
      <i />
      changed
    </span>
  );
}

function StatusPill({ status }: { status: PlanUiStatus }) {
  return (
    <span className={"pm-spill " + status}>
      {status === "running" && <i />}
      {status === "gated" ? "gated" : status}
    </span>
  );
}

type PlanCounts = {
  ready: number;
  blocked: number;
  review: number;
  running: number;
  done: number;
  draft: number;
  total: number;
};

function splitPlan(plan: WorkPlan | null): PlanTree {
  if (!plan) return { sources: [], executable: [] };
  return {
    sources: plan.artifacts.filter((artifact) => !artifact.executable),
    executable: plan.artifacts.filter((artifact) => artifact.executable),
  };
}

function planCounts(items: PlanArtifact[]): PlanCounts {
  const count = (status: PlanUiStatus) => items.filter((item) => uiStatus(item) === status).length;
  return {
    ready: count("ready"),
    blocked: count("blocked") + count("gated"),
    review: count("review"),
    running: count("running"),
    done: count("done"),
    draft: count("draft"),
    total: items.length,
  };
}

function uiStatus(artifact: PlanArtifact): PlanUiStatus {
  const latestRun = artifact.runs.at(-1);
  const runStatus = latestRun?.status;
  const loop = latestRun ? loopStatus(latestRun) : null;
  if (runStatus === "accepted") return "done";
  if (loop === "pending" || loop === "running" || loop === "waiting_report" || loop === "assessing") return "running";
  if (loop === "needs_agent" || loop === "blocked_user" || loop === "failed" || loop === "cancelled") return "blocked";
  if (loop === "completed") return "review";
  if (runStatus === "running") return "running";
  if (runStatus === "needs_attention" || runStatus === "blocked") return "blocked";
  if (runStatus === "waiting_approval" || runStatus === "completed_pending_review") return "review";
  if (artifact.status === "accepted") return "done";
  if (artifact.status === "changed") return "blocked";
  if (artifact.launch_blockers.length > 0 && artifact.status === "approved") return "gated";
  if (artifact.status === "approved") return artifact.readiness === "ready" ? "ready" : "blocked";
  return artifact.readiness === "ready" ? "draft" : "blocked";
}

function loopStatus(run: PlanArtifact["runs"][number]): LoopStatus {
  if (run.loop_status) return run.loop_status;
  if (run.status === "running") return "running";
  if (run.status === "needs_attention") return "needs_agent";
  if (run.status === "blocked") return "blocked_user";
  return "completed";
}

function attentionItems(plan: WorkPlan, tree: PlanTree) {
  const out: {
    id: string;
    kind: AttentionKind;
    icon: AttentionIconKind;
    title: string;
    sub: string;
    action: string;
    target: PlanningView;
  }[] = [];
  const changedSource = tree.sources.find((source) => source.status === "changed");
  const blockedArtifact = tree.executable.find((artifact) => uiStatus(artifact) === "blocked" && artifact.readiness !== "needs_detail");
  const reviewArtifact = tree.executable.find((artifact) => uiStatus(artifact) === "review");
  const readinessArtifact = tree.executable.find((artifact) => uiStatus(artifact) === "blocked" && artifact.readiness === "needs_detail");
  const readyArtifact = tree.executable.find((artifact) => artifact.launchable);
  if (changedSource) {
    out.push({
      id: "source-change",
      kind: "diff",
      icon: "doc",
      title: `Review ${changedSource.title}`,
      sub: "A source document changed since the plan was accepted.",
      action: "Review",
      target: { kind: "source", id: changedSource.id },
    });
  }
  if (blockedArtifact) {
    out.push({
      id: "blocked-artifact",
      kind: "blocked",
      icon: "agent",
      title: `${blockedArtifact.id} run needs a decision`,
      sub: blockedArtifact.launch_blockers[0] ?? "Agent run needs a decision before continuing.",
      action: "Reply",
      target: { kind: "artifact", id: blockedArtifact.id },
    });
  }
  if (reviewArtifact) {
    out.push({
      id: "review-artifact",
      kind: "review",
      icon: "eye",
      title: `Review ${reviewArtifact.id} run`,
      sub: reviewArtifact.runs.at(-1)?.summary ?? "Run is done and awaiting your review.",
      action: "Open",
      target: { kind: "artifact", id: reviewArtifact.id },
    });
  }
  if (readinessArtifact) {
    out.push({
      id: "readiness-artifact",
      kind: "readiness",
      icon: "spark",
      title: `Fix readiness for ${readinessArtifact.id}`,
      sub: readinessArtifact.launch_blockers[0] ?? "Missing detail before launch.",
      action: "Fix",
      target: { kind: "artifact", id: readinessArtifact.id },
    });
  }
  if (!plan.approved_at) {
    out.push({
      id: "accept-plan",
      kind: "accept",
      icon: "check",
      title: "Approve the latest plan",
      sub: "Plan is draft against the current source index.",
      action: "Review & approve",
      target: { kind: "accept" },
    });
  }
  if (readyArtifact) {
    out.push({
      id: "launch-ready",
      kind: "launch",
      icon: "spark",
      title: `${readyArtifact.id} is ready to launch`,
      sub: "Launch a scoped agent from the artifact.",
      action: "Launch",
      target: { kind: "artifact", id: readyArtifact.id },
    });
  }
  return out;
}

function buildPlanEpic(
  work: WorkDetail,
  plan: WorkPlan,
  tree: PlanTree,
  counts: PlanCounts,
): PlanEpic {
  const description = work.description.trim()
    ? work.description.trim()
    : `Review and execute the ${plan.framework.toUpperCase()} ${plan.depth} plan for ${work.name}.`;
  return {
    id: PRIMARY_EPIC_ID,
    title: work.name,
    description,
    children: tree.executable,
    counts,
  };
}

function findArtifact(plan: WorkPlan | null, id: string): PlanArtifact | null {
  return plan?.artifacts.find((artifact) => artifact.id === id) ?? null;
}

function formatShortDate(value: string): string {
  const date = new Date(value);
  if (Number.isNaN(date.valueOf())) return value;
  return date.toLocaleDateString(undefined, { month: "short", day: "numeric" });
}
