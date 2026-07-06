import {
  type CSSProperties,
  type ReactNode,
  useEffect,
  useId,
  useMemo,
  useState,
} from "react";

import {
  type ChatSummary,
  type LoopStatus,
  type PlanArtifact,
  type PlanArtifactDetail,
  type PlanMaterializationStatus,
  type PlanningProfile,
  type ProviderDescriptor,
  type ProjectSummary,
  type WorkDetail,
  type WorkPlan,
  type WorkSummary,
} from "./api";
import { ChatTile } from "./Chat";
import {
  AgentIcon,
  BoltIcon,
  BranchIcon,
  BugIcon,
  ChatIcon,
  CheckIcon,
  ChevronRightIcon,
  DocIcon,
  EyeIcon,
  FolderIcon,
  MoveIcon,
  SlidersIcon,
  SparkIcon,
} from "./Icons";
import { PaneResizeHandle } from "./PaneResizeHandle";
import {
  coerceProviderOptionsForModel,
  modelPickerOptions,
  optionLabel,
  providerDefaults,
  providerEffortOption,
  providerPermissionOption,
  useProviderDescriptors,
} from "./providerDescriptors";
import { RichMarkdownEditor } from "./RichMarkdownEditor";
import { ShellCrown } from "./ShellCrown";
import {
  PLANNING_DOCK_MAX,
  PLANNING_DOCK_MIN,
  PLANNING_RAIL_MAX,
  PLANNING_RAIL_MIN,
  useLayoutStore,
} from "./state/layout";
import {
  PLANNING_FRAMEWORKS,
  PLANNING_PROFILES,
  type PlanningAgentConfig,
  type PlanningFrameworkId,
  planningFrameworkDefinition,
  planningProfileDefinition,
} from "./planningSetup";

export type PlanningView =
  | { kind: "overview" }
  | { kind: "epic"; id: string }
  | { kind: "artifact"; id: string }
  | { kind: "source"; id: string }
  | { kind: "accept" };

export type PlanOverviewTab = "summary" | "blocking" | "completed" | "pending";

type PlanningModeProps = {
  work: WorkDetail;
  project: ProjectSummary | null;
  plan: WorkPlan | null;
  materializationStatus: PlanMaterializationStatus | null;
  planningChatSlug: string | null;
  planningChatSummary: ChatSummary | null;
  planningChatProjects: ProjectSummary[];
  planningChatWorks: WorkSummary[];
  selectedDetail: PlanArtifactDetail | null;
  draft: string;
  error: string | null;
  loading: boolean;
  saving: boolean;
  view: PlanningView;
  overviewTab: PlanOverviewTab;
  chatOpen: boolean;
  initialRoot: string | null;
  prompt: string;
  profile: PlanningProfile;
  framework: PlanningFrameworkId;
  agentConfig: PlanningAgentConfig | null;
  onPromptChange: (value: string) => void;
  onProfileChange: (value: PlanningProfile) => void;
  onFrameworkChange: (value: PlanningFrameworkId) => void;
  onAgentConfigChange: (value: PlanningAgentConfig) => void;
  onOverviewTab: (tab: PlanOverviewTab) => void;
  onView: (view: PlanningView) => void;
  onChooseRoot: () => void;
  onClearRoot: () => void;
  onStart: () => void;
  onCreateSourcePlan: () => void;
  onFinishConversation: () => void;
  onManual: () => void;
  onDraftChange: (value: string) => void;
  onSave: () => void;
  onReset: () => void;
  onApprovePlan: () => void;
  onCreateBug: (artifactId: string, title: string, description: string) => Promise<void>;
  onLaunch: (detail: PlanArtifactDetail) => void;
  onIngestReport: (artifact: PlanArtifact, agentSlug: string) => void;
  onCleanupRun: (artifact: PlanArtifact, agentSlug: string) => void;
  onChatOpen: (open: boolean) => void;
  onPlanningChatUpdated: (chat: ChatSummary) => void;
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

const MODE_CARDS = [
  {
    id: "manual",
    glyph: "agent",
    tag: "current",
    title: "Manual",
    desc: "Launch and steer agents by hand on a shared canvas.",
  },
  {
    id: "planning",
    glyph: "planning",
    tag: "BMAD",
    title: "Planning",
    desc: "Describe the idea; BMAD writes source docs and a plan you launch agents from.",
  },
  {
    id: "loop",
    glyph: "loop",
    tag: "soon",
    title: "Loop",
    desc: "Run toward a verifiable goal with memory until it is met.",
  },
] as const;

export function PlanningMode({
  work,
  project,
  plan,
  materializationStatus,
  planningChatSlug,
  planningChatSummary,
  planningChatProjects,
  planningChatWorks,
  selectedDetail,
  draft,
  error,
  loading,
  saving,
  view,
  overviewTab,
  chatOpen,
  initialRoot,
  prompt,
  profile,
  framework,
  agentConfig,
  onPromptChange,
  onProfileChange,
  onFrameworkChange,
  onAgentConfigChange,
  onOverviewTab,
  onView,
  onChooseRoot,
  onClearRoot,
  onStart,
  onCreateSourcePlan,
  onFinishConversation,
  onManual,
  onDraftChange,
  onSave,
  onReset,
  onApprovePlan,
  onCreateBug,
  onLaunch,
  onIngestReport,
  onCleanupRun,
  onChatOpen,
  onPlanningChatUpdated,
}: PlanningModeProps) {
  const tree = useMemo(() => splitPlan(plan), [plan]);
  const counts = useMemo(() => planCounts(tree.executable), [tree.executable]);
  const activeId = "id" in view ? view.id : null;
  const source = view.kind === "source" ? findArtifact(plan, view.id) : null;
  const artifact = view.kind === "artifact" ? findArtifact(plan, view.id) : null;
  const epic = plan ? buildPlanEpic(work, plan, tree, counts) : null;
  const planningRailWidth = useLayoutStore((s) => s.planningRailWidth);
  const planningDockWidth = useLayoutStore((s) => s.planningDockWidth);
  const setPlanningDockWidth = useLayoutStore((s) => s.setPlanningDockWidth);
  const [bugDialogOpen, setBugDialogOpen] = useState(false);
  const planningStyle: CSSProperties = {
    ["--pm-rail-width" as string]: `${planningRailWidth}px`,
    ["--pm-dock-width" as string]: `${planningDockWidth}px`,
  };
  const hasPlanningChat = planningChatSlug !== null;
  const showChatDock = chatOpen && hasPlanningChat;
  const planningReady = Boolean(planningChatSummary?.planning_readiness?.ready);
  const planReferences = plan?.artifacts ?? [];
  const materializerActive = materializationStatus?.state === "running";
  const materializerRetryable =
    materializationStatus?.state === "waiting_permission" ||
    materializationStatus?.state === "stalled" ||
    materializationStatus?.state === "failed";
  const sourcePlanBusy = saving || materializerActive;
  const showSourcePlanAction =
    planningReady || sourcePlanBusy || materializerRetryable;
  const sourcePlanLabel = sourcePlanActionLabel({
    loading: sourcePlanBusy,
    status: materializationStatus,
  });

  if (!plan && hasPlanningChat) {
    return (
      <div className="planning-mode conversation-only" style={planningStyle}>
        <PlanningStarterRail
          work={work}
          project={project}
          readiness={planningReady}
          materializing={sourcePlanBusy}
          materializationStatus={materializationStatus}
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
            onOpenPlan={showSourcePlanAction ? onCreateSourcePlan : undefined}
            openPlanLabel={sourcePlanLabel}
            openPlanDisabled={sourcePlanBusy}
            onChatUpdated={onPlanningChatUpdated}
          />
        </main>
      </div>
    );
  }

  if (plan?.phase === "conversing") {
    return (
      <div className="planning-mode conversation-only" style={planningStyle}>
        <PlanningStarterRail
          work={work}
          project={project}
          readiness={planningReady}
          materializing={saving}
          materializationStatus={null}
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
            onOpenPlan={onFinishConversation}
            openPlanLabel={saving ? "Opening plan..." : "Open plan overview"}
            openPlanDisabled={saving}
            onChatUpdated={onPlanningChatUpdated}
          />
        </main>
      </div>
    );
  }

  if (!plan) {
    return (
      <div className="planning-mode empty-only">
        <PlanEmpty
          work={work}
          loading={loading}
          error={error}
          initialRoot={initialRoot}
          prompt={prompt}
          profile={profile}
          framework={framework}
          agentConfig={agentConfig}
          onPromptChange={onPromptChange}
          onProfileChange={onProfileChange}
          onFrameworkChange={onFrameworkChange}
          onAgentConfigChange={onAgentConfigChange}
          onChooseRoot={onChooseRoot}
          onClearRoot={onClearRoot}
          onStart={onStart}
          onManual={onManual}
        />
      </div>
    );
  }

  return (
    <div className={"planning-mode" + (showChatDock ? " with-dock" : "")} style={planningStyle}>
      <PlanRail
        work={work}
        project={project}
        plan={plan}
        tree={tree}
        counts={counts}
        activeId={activeId}
        railWidth={planningRailWidth}
        onManual={onManual}
        onEpic={(id) => onView({ kind: "epic", id })}
        onArtifact={(id) => onView({ kind: "artifact", id })}
        onSource={(id) => onView({ kind: "source", id })}
        onCreateBug={() => setBugDialogOpen(true)}
      />
      <main className="pm-main">
        <PlanHeader
          work={work}
          plan={plan}
          view={view}
          activeArtifact={artifact ?? source}
          activeEpic={epic}
          onBack={() => onView({ kind: "overview" })}
        />
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
            saving={saving}
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
            saving={saving}
            onDraftChange={onDraftChange}
            onSave={onSave}
            onReset={onReset}
            onLaunch={onLaunch}
            onIngestReport={onIngestReport}
            onCleanupRun={onCleanupRun}
            onEpic={() => onView({ kind: "epic", id: PRIMARY_EPIC_ID })}
            onApprovePlan={onApprovePlan}
          />
        )}
        {view.kind === "source" && (
          <SourceDoc
            artifact={source}
            detail={selectedDetail}
            draft={draft}
            saving={saving}
            onDraftChange={onDraftChange}
            onSave={onSave}
            onReset={onReset}
            onApprovePlan={onApprovePlan}
          />
        )}
        {view.kind === "accept" && (
          <ApprovePlan plan={plan} counts={counts} saving={saving} onBack={() => onView({ kind: "overview" })} onApprove={onApprovePlan} />
        )}
      </main>
      {showChatDock ? (
        <aside className="pm-chat-dock">
          <PaneResizeHandle
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
          />
        </aside>
      ) : hasPlanningChat ? (
        <button className="pm-float-chat" onClick={() => onChatOpen(true)} title="Open plan chat" aria-label="Open plan chat">
          <ChatIcon size={18} />
        </button>
      ) : null}
      {bugDialogOpen && (
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

function PlanEmpty({
  work,
  loading,
  error,
  initialRoot,
  prompt,
  profile,
  framework,
  agentConfig,
  onPromptChange,
  onProfileChange,
  onFrameworkChange,
  onAgentConfigChange,
  onChooseRoot,
  onClearRoot,
  onStart,
  onManual,
}: {
  work: WorkDetail;
  loading: boolean;
  error: string | null;
  initialRoot: string | null;
  prompt: string;
  profile: PlanningProfile;
  framework: PlanningFrameworkId;
  agentConfig: PlanningAgentConfig | null;
  onPromptChange: (value: string) => void;
  onProfileChange: (value: PlanningProfile) => void;
  onFrameworkChange: (value: PlanningFrameworkId) => void;
  onAgentConfigChange: (value: PlanningAgentConfig) => void;
  onChooseRoot: () => void;
  onClearRoot: () => void;
  onStart: () => void;
  onManual: () => void;
}) {
  const [mode, setMode] = useState<(typeof MODE_CARDS)[number]["id"]>("planning");
  const promptId = useId();
  const selectedFramework = planningFrameworkDefinition(framework);
  const selectedProfile = planningProfileDefinition(profile);
  return (
    <div className="pm-empty-stage themed-scrollbar">
      <section className="pm-empty-card">
        <div className="pm-empty-kicker">
          <SparkIcon size={13} /> {work.slug} · new work · nothing here yet
        </div>
        <h1>How do you want to work on this?</h1>
        <p>
          An empty work is not an execution workspace yet. Pick how it should run: plan it, loop it toward a goal,
          or launch agents by hand.
        </p>
        <div className="pm-mode-cards">
          {MODE_CARDS.map((card) => (
            <button
              key={card.id}
              className={"pm-mode-card" + (mode === card.id ? " active" : "") + (card.id === "loop" ? " soon" : "")}
              onClick={() => setMode(card.id)}
              type="button"
            >
              <span className="pm-mode-check"><CheckIcon size={14} /></span>
              <span className="pm-mode-top">
                <span className="pm-mode-glyph">{renderModeIcon(card.glyph, 17)}</span>
                <span className="pm-mode-tag">{card.tag}</span>
              </span>
              <span className="pm-mode-name">{card.title}</span>
              <span className="pm-mode-desc">{card.desc}</span>
            </button>
          ))}
        </div>
        {mode === "planning" && (
          <div className="pm-empty-prompt">
            <div className="pm-empty-prompt-head">
              <div className="pm-empty-prompt-title">
                <DocIcon size={12} /> Plan setup
              </div>
              <label className="pm-framework-select" title={selectedFramework.desc}>
                <span>Framework</span>
                <select
                  value={framework}
                  onChange={(event) =>
                    onFrameworkChange(event.target.value as PlanningFrameworkId)
                  }
                >
                  {PLANNING_FRAMEWORKS.map((item) => (
                    <option key={item.id} value={item.id}>
                      {item.name}
                    </option>
                  ))}
                </select>
              </label>
            </div>

            <div className="pm-profile-pick">
              <div className="pm-profile-grid">
                {PLANNING_PROFILES.map((item) => (
                  <button
                    key={item.id}
                    type="button"
                    className={"pm-profile-chip" + (profile === item.id ? " active" : "")}
                    onClick={() => onProfileChange(item.id)}
                  >
                    <span className="pm-profile-ico">
                      {renderProfileIcon(item.icon, 14)}
                    </span>
                    <span className="pm-profile-name">{item.name}</span>
                  </button>
                ))}
              </div>
              <div className="pm-profile-detail">
                <span className={`pm-profile-depth d-${selectedProfile.depth}`}>
                  {selectedProfile.depth}
                </span>
                <span className="pm-profile-desc">{selectedProfile.desc}</span>
                <span className="pm-profile-artifacts">
                  {selectedProfile.artifacts}
                </span>
              </div>
            </div>

            <div className={"pm-root-row" + (initialRoot ? " set" : "")}>
              <span className="pm-root-icon">
                <FolderIcon size={15} />
              </span>
              <span className="pm-root-label">Work folder</span>
              <span className={"pm-root-value" + (initialRoot ? "" : " is-empty")}>
                {initialRoot ?? "Choose a repository or project root"}
              </span>
              {initialRoot && (
                <button
                  className="pm-root-clear"
                  type="button"
                  onClick={onClearRoot}
                  disabled={loading}
                  title="Clear"
                  aria-label="Clear work folder"
                >
                  ×
                </button>
              )}
              <button
                className="btn sm"
                type="button"
                onClick={onChooseRoot}
                disabled={loading}
              >
                {initialRoot ? "Change" : "Choose"}
              </button>
            </div>

            <div className="pm-empty-divider" />

            <div className="pm-agent-cfg">
              <span className="pm-agent-cfg-label">Plan chat</span>
              <PlanningAgentControls
                value={agentConfig}
                onChange={onAgentConfigChange}
              />
            </div>

            <div className="pm-empty-divider" />

            <label className="pm-empty-prompt-title" htmlFor={promptId}>
              <ChatIcon size={12} /> Describe the idea, problem, or outcome
            </label>
            <textarea
              id={promptId}
              value={prompt}
              onChange={(event) => onPromptChange(event.target.value)}
              placeholder="Describe the idea, problem, constraints, or outcome you want BMAD to plan…"
            />
            <div className="pm-empty-row">
              <span className="pm-empty-hint">
                {!initialRoot ? (
                  <>
                    <FolderIcon size={11} /> Choose a work folder to continue.
                  </>
                ) : (
                  <>
                    {selectedFramework.name} will draft a{" "}
                    <b>{selectedProfile.depth}</b> plan: {selectedProfile.artifacts}.
                  </>
                )}
              </span>
              <span className="pm-empty-spacer" />
              <button className="btn primary lg" onClick={onStart} disabled={loading || !initialRoot}>
                <SparkIcon size={12} /> {loading ? "Starting…" : "Plan this work"}
              </button>
            </div>
          </div>
        )}
        {mode === "manual" && (
          <div className="pm-empty-prompt">
            <div className="pm-empty-row">
              <span className="pm-empty-hint">Open the normal agent canvas and steer agents directly.</span>
              <button className="btn" onClick={onManual}>Open canvas <ChevronRightIcon size={12} /></button>
            </div>
          </div>
        )}
        {mode === "loop" && (
          <div className="pm-empty-prompt disabled">
            <div className="pm-empty-row">
              <span className="pm-empty-hint">Loop mode needs verifiable success criteria and persistent memory. It builds on Planning.</span>
              <button className="btn" disabled>Not yet</button>
            </div>
          </div>
        )}
        {error && <div className="pm-error">{error}</div>}
      </section>
    </div>
  );
}

function PlanningAgentControls({
  value,
  onChange,
}: {
  value: PlanningAgentConfig | null;
  onChange: (value: PlanningAgentConfig) => void;
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
    onChange({
      provider: activeProvider.name,
      model,
      options: {
        ...currentOptions,
        [key]: nextValue,
      },
    });
  }

  return (
    <div className="pm-agent-controls">
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
      {effortOption && (
        <label className="pm-mini-select" title={effortOption.field.label}>
          <BranchIcon size={10} />
          <select
            value={currentOptions[effortOption.key] ?? effortOption.field.default}
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
      )}
      {permissionOption && (
        <label className="pm-mini-select" title={permissionOption.field.label}>
          <EyeIcon size={10} />
          <select
            value={currentOptions[permissionOption.key] ?? permissionOption.field.default}
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

function renderModeIcon(
  icon: (typeof MODE_CARDS)[number]["glyph"],
  size: number,
): ReactNode {
  if (icon === "agent") return <AgentIcon size={size} />;
  if (icon === "loop") return <BranchIcon size={size} />;
  return <DocIcon size={size} />;
}

function renderProfileIcon(
  icon: (typeof PLANNING_PROFILES)[number]["icon"],
  size: number,
): ReactNode {
  if (icon === "bug") return <BugIcon size={size} />;
  if (icon === "bolt") return <BoltIcon size={size} />;
  if (icon === "branch") return <BranchIcon size={size} />;
  if (icon === "move") return <MoveIcon size={size} />;
  if (icon === "artifact") return <DocIcon size={size} />;
  if (icon === "settings") return <SlidersIcon size={size} />;
  return <SparkIcon size={size} />;
}

function PlanningStarterRail({
  work,
  project,
  readiness,
  materializing,
  materializationStatus,
  railWidth,
}: {
  work: WorkDetail;
  project: ProjectSummary | null;
  readiness: boolean;
  materializing: boolean;
  materializationStatus: PlanMaterializationStatus | null;
  railWidth: number;
}) {
  const setPlanningRailWidth = useLayoutStore((s) => s.setPlanningRailWidth);
  return (
    <aside className="pm-rail pm-starter-rail">
      <ShellCrown />
      <div className="crumbs-v3 pm-starter-crumbs">
        <a className="crumb" href="/">
          ← workspace
        </a>
        {project && (
          <>
            <span className="sep">/</span>
            <a className="crumb" href={`/projects/${project.slug}`}>
              {project.name}
            </a>
          </>
        )}
        <span className="sep">/</span>
        <span className="now">{work.slug}</span>
      </div>
      <div className="pm-work-hero">
        <span>{work.slug} · {formatShortDate(work.created_at)}</span>
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
      <MaterializationStatusNote status={materializationStatus} />
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
  if (status?.state === "waiting_permission") return "Resume source plan";
  if (loading || status?.state === "running") return "Creating plan...";
  if (status?.state === "failed" || status?.state === "stalled") {
    return "Retry source plan";
  }
  return "Create source plan";
}

function MaterializationStatusNote({
  status,
}: {
  status: PlanMaterializationStatus | null;
}) {
  if (!status || status.state === "idle" || status.state === "complete") {
    return null;
  }
  const copy = materializationStatusCopy(status);
  const activity = materializationActivityCopy(status);
  return (
    <div className={`pm-materialization-note ${status.state}`}>
      <span className="pm-materialization-icon">{copy.icon}</span>
      <div>
        <strong>{copy.title}</strong>
        <span>{copy.message}</span>
        {activity ? <span>{activity}</span> : null}
      </div>
    </div>
  );
}

function materializationStatusCopy(status: PlanMaterializationStatus): {
  icon: ReactNode;
  title: string;
  message: string;
} {
  const detail = status.tool_name
    ? `${status.tool_name}: ${status.message}`
    : status.message;
  if (status.state === "waiting_permission") {
    return {
      icon: <SlidersIcon size={13} />,
      title: "Waiting for permission",
      message: detail || "The materializer paused for an approval.",
    };
  }
  if (status.state === "stalled") {
    return {
      icon: <BoltIcon size={13} />,
      title: "Materializer stopped",
      message: detail || "No source-plan report was found yet.",
    };
  }
  if (status.state === "failed") {
    return {
      icon: <BugIcon size={13} />,
      title: "Materializer failed",
      message: detail || "The materializer returned an error.",
    };
  }
  return {
    icon: <SparkIcon size={13} />,
    title: "Materializer running",
    message: detail || "Writing source-plan files.",
  };
}

function materializationActivityCopy(status: PlanMaterializationStatus): string {
  if (!status.last_event_type) return "";
  const summary = status.last_event_summary
    ? ` · ${status.last_event_summary}`
    : "";
  return `Last transcript event: ${status.last_event_type}${summary}`;
}

function PlanningProgressTimeline({ stages }: { stages: PlanningTimelineStage[] }) {
  return (
    <div className="plt">
      <div className="plt-hd mono-lbl">Planning progress</div>
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

function PlanRail({
  work,
  project,
  plan,
  tree,
  counts,
  activeId,
  railWidth,
  onManual,
  onEpic,
  onArtifact,
  onSource,
  onCreateBug,
}: {
  work: WorkDetail;
  project: ProjectSummary | null;
  plan: WorkPlan;
  tree: PlanTree;
  counts: PlanCounts;
  activeId: string | null;
  railWidth: number;
  onManual: () => void;
  onEpic: (id: string) => void;
  onArtifact: (id: string) => void;
  onSource: (id: string) => void;
  onCreateBug: () => void;
}) {
  const [open, setOpen] = useState(true);
  const setPlanningRailWidth = useLayoutStore((s) => s.setPlanningRailWidth);
  const activeProgress = counts.running + counts.review + counts.ready + counts.draft;
  return (
    <aside className="pm-rail">
      <ShellCrown />
      <div className="pm-crumbs">
        <button onClick={onManual}>← canvas</button>
        <span>/</span>
        <span>{project?.name ?? "Loose"}</span>
        <span>/</span>
        <span>{work.slug}</span>
      </div>
      <div className="pm-mode-static">
        <span className="pm-mode-static-glyph">PL</span>
        <span>
          <span>Work mode</span>
          <strong>Planning</strong>
        </span>
        <em><CheckIcon size={11} /> set</em>
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
          <button
            type="button"
            title={tree.executable.length === 0 ? "No executable plan item yet" : "Create a bug"}
            disabled={tree.executable.length === 0}
            onClick={onCreateBug}
          >
            + bug
          </button>
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
                      {source.title}
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
                    {item.kind !== "story" && <em>{item.kind}</em>}
                    {item.title}
                  </span>
                  <TreeStatus status={uiStatus(item)} />
                </button>
              ))}
            </div>
          )}
        </div>
        <div className="pm-section-hd">
          <span>Tracking</span>
          <span>{plan.artifacts.reduce((total, item) => total + item.tracking.length, 0)}</span>
        </div>
        <div className="pm-rail-tracking">
          {plan.artifacts.flatMap((item) =>
            item.tracking.map((link) => (
              <button
                key={`${item.id}:${link.id}`}
                className="pm-track-row rail"
                onClick={() => onArtifact(item.id)}
              >
                <span className={"pm-track-kind " + link.kind}>{link.kind}</span>
                <strong>{link.title}</strong>
                <small>{item.id}</small>
              </button>
            )),
          )}
          {plan.artifacts.every((item) => item.tracking.length === 0) && (
            <div className="pm-empty-state">No tracking links.</div>
          )}
        </div>
      </div>
      <div className="pm-footstrip">
        <span><i /> {counts.running} running</span>
        <span>{counts.blocked + counts.ready + counts.draft} to do</span>
      </div>
      <PaneResizeHandle
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
            className="btn-icon"
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

function PlanHeader({
  work,
  plan,
  view,
  activeArtifact,
  activeEpic,
  onBack,
}: {
  work: WorkDetail;
  plan: WorkPlan;
  view: PlanningView;
  activeArtifact: PlanArtifact | null;
  activeEpic: PlanEpic | null;
  onBack: () => void;
}) {
  const title =
    view.kind === "overview"
      ? "Plan overview"
      : view.kind === "accept"
        ? "Approve plan"
        : view.kind === "epic"
          ? activeEpic?.title ?? "Epic"
          : activeArtifact?.title ?? "Planning";
  const detail =
    view.kind === "overview"
      ? `${work.name} · ${plan.framework.toUpperCase()} · ${plan.depth}`
      : view.kind === "epic"
        ? `${activeEpic?.id ?? PRIMARY_EPIC_ID} · epic`
      : view.kind === "source"
        ? "source document · editable"
        : view.kind === "artifact"
          ? `${activeArtifact?.id ?? ""} · source-backed detail`
          : "applies to the current source index";
  return (
    <div className="pm-main-hd">
      <div>
        <h2>
          {view.kind !== "overview" && (
            <button className="btn ghost icon sm" onClick={onBack} aria-label="Back to overview">←</button>
          )}
          {title}
        </h2>
        <p>{detail}</p>
      </div>
      <span />
      {view.kind !== "accept" && plan.approved_at && (
        <span className="pm-edit-state editable">plan approved</span>
      )}
    </div>
  );
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
              <PlanProgress plan={plan} counts={counts} />
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
                    <small>draft · against current source index</small>
                    <StatusPill status="draft" />
                    <ChevronRightIcon className="pm-row-chevron" size={15} />
                  </button>
                )}
                {tree.sources.filter((s) => s.status === "changed").map((source) => (
                  <button key={source.id} className="pm-list-row" onClick={() => onSource(source.id)}>
                    <span className="glyph"><DocIcon size={12} /></span>
                    <strong>Review {source.title}</strong>
                    <small>{source.path} · source changed</small>
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
  artifact,
  detail,
  draft,
  saving,
  onDraftChange,
  onSave,
  onReset,
  onLaunch,
  onIngestReport,
  onCleanupRun,
  onEpic,
  onApprovePlan,
}: {
  artifact: PlanArtifact | null;
  detail: PlanArtifactDetail | null;
  draft: string;
  saving: boolean;
  onDraftChange: (value: string) => void;
  onSave: () => void;
  onReset: () => void;
  onLaunch: (detail: PlanArtifactDetail) => void;
  onIngestReport: (artifact: PlanArtifact, agentSlug: string) => void;
  onCleanupRun: (artifact: PlanArtifact, agentSlug: string) => void;
  onEpic: () => void;
  onApprovePlan: () => void;
}) {
  if (!artifact) return <div className="pm-loading">Artifact not found.</div>;
  if (!detail) return <div className="pm-loading">Loading source…</div>;
  const status = uiStatus(detail.artifact);
  const editable = status === "draft" || status === "ready" || status === "blocked";
  const dirty = draft !== detail.content;
  const launchable = detail.artifact.launchable;
  const latestRun = detail.artifact.runs.at(-1) ?? null;
  const latestLoopStatus = latestRun ? loopStatus(latestRun) : null;
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
            readOnly={!editable}
          />
          <div className="pm-doc-actions">
            <button className="btn primary" onClick={onSave} disabled={!dirty || saving || !editable}>Save source</button>
            <button className="btn" onClick={onReset} disabled={!dirty || saving}>Reset</button>
          </div>
        </article>
        <aside className="pm-art-aside">
          <InspectorPanel title="Readiness">
            <Kv label="Criteria" value={detail.artifact.readiness === "ready" ? "defined" : "missing"} tone={detail.artifact.readiness === "ready" ? "good" : "danger"} />
            <Kv label="Estimate" value={detail.artifact.readiness === "ready" ? "lightweight" : "missing"} />
            <Kv label="Deps" value={detail.artifact.dependencies.length > 0 ? detail.artifact.dependencies.join(", ") : "clear"} tone={detail.artifact.launch_blockers.length > 0 ? "warn" : "good"} />
          </InspectorPanel>
          <InspectorPanel title="Workspace">
            <div className="pm-panel-note">
              <FolderIcon size={12} /> Agent worktrees launch from the selected source repo.
            </div>
            <button className="btn sm" disabled={!launchable} onClick={() => onLaunch(detail)}>
              <SparkIcon size={12} /> Launch agent from {detail.artifact.id}
            </button>
            {!launchable && <div className="pm-panel-note">{detail.artifact.launch_blockers[0] ?? "Resolve readiness before launch."}</div>}
          </InspectorPanel>
          <InspectorPanel title="Run state">
            {latestRun ? (
              <>
                <Kv label="Agent" value={latestRun.agent_slug} />
                <Kv
                  label="Loop"
                  value={loopStatusLabel(latestLoopStatus)}
                  tone={loopStatusTone(latestLoopStatus)}
                />
                {latestRun.loop_status_reason && (
                  <div className="pm-panel-note">{latestRun.loop_status_reason}</div>
                )}
                {latestRun.loop_latest_assessment.length > 0 && (
                  <div className="pm-panel-note">
                    {latestRun.loop_latest_assessment.join(" ")}
                  </div>
                )}
                {latestRun.cleanup_at ? (
                  <Kv label="Cleanup" value="done" tone="good" />
                ) : (
                  <Kv label="Cleanup" value="pending" />
                )}
                <div className="pm-inline-actions">
                  <button
                    className="btn sm"
                    disabled={saving}
                    onClick={() => onIngestReport(detail.artifact, latestRun.agent_slug)}
                  >
                    {collectReportLabel(latestLoopStatus)}
                  </button>
                  <button
                    className="btn sm"
                    disabled={
                      saving ||
                      latestRun.status !== "accepted" ||
                      latestRun.cleanup_at !== null
                    }
                    onClick={() => onCleanupRun(detail.artifact, latestRun.agent_slug)}
                  >
                    Clean up
                  </button>
                </div>
              </>
            ) : (
              <div className="pm-panel-note">No agent run recorded yet.</div>
            )}
          </InspectorPanel>
          {detail.artifact.tracking.length > 0 && (
            <InspectorPanel title="Tracking">
              <div className="pm-track-list">
                {detail.artifact.tracking.map((link) => (
                  <div key={link.id} className="pm-track-row">
                    <span className={"pm-track-kind " + link.kind}>{link.kind}</span>
                    <strong>{link.title}</strong>
                    <small>{link.ref || link.status || link.url || "linked"}</small>
                  </div>
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
  artifact,
  detail,
  draft,
  saving,
  onDraftChange,
  onSave,
  onReset,
  onApprovePlan,
}: {
  artifact: PlanArtifact | null;
  detail: PlanArtifactDetail | null;
  draft: string;
  saving: boolean;
  onDraftChange: (value: string) => void;
  onSave: () => void;
  onReset: () => void;
  onApprovePlan: () => void;
}) {
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
          <span className="pm-edit-state editable">editable</span>
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
          <span className="glyph">{kindGlyph(item.kind)}</span>
          <strong>{item.title}</strong>
          <small>{artifactListSubtitle(item)}</small>
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

function Kv({ label, value, tone }: { label: string; value: string; tone?: "good" | "warn" | "danger" }) {
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

function loopStatusTone(status: LoopStatus | null): "good" | "warn" | "danger" {
  if (status === "completed") return "good";
  if (status === "blocked_user" || status === "failed" || status === "cancelled") {
    return "danger";
  }
  return "warn";
}

function collectReportLabel(status: LoopStatus | null): string {
  if (status === "needs_agent") return "Collect updated report";
  if (status === "completed") return "Refresh report";
  return "Collect report";
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

function artifactListSubtitle(artifact: PlanArtifact): string {
  const id = artifact.id.toUpperCase();
  const latestRun = artifact.runs.at(-1);
  const status = uiStatus(artifact);
  if (latestRun) {
    const loop = loopStatus(latestRun);
    if (status === "review") return `${id} · run done · awaiting review`;
    if (status === "running") return `${id} · ${latestRun.agent_slug} · ${loopStatusLabel(loop)}`;
    if (status === "done") return `${id} · accepted`;
    if (status === "blocked") return `${id} · ${loopStatusLabel(loop)}`;
  }
  if (artifact.launch_blockers.length > 0) {
    return `${id} · ${artifact.launch_blockers[0]}`;
  }
  if (status === "ready") return `${id} · ready to launch`;
  if (artifact.readiness === "needs_detail") return `${id} · needs detail`;
  if (artifact.status === "changed") return `${id} · source changed`;
  return `${id} · draft`;
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

function kindGlyph(kind: PlanArtifact["kind"]): string {
  return {
    brief: "BR",
    architecture: "AR",
    spec: "SP",
    scenario: "SC",
    acceptance: "AC",
    story: "ST",
    task: "TK",
    spike: "SK",
    bug: "BG",
    hotfix: "HF",
    note: "NT",
  }[kind];
}

function formatShortDate(value: string): string {
  const date = new Date(value);
  if (Number.isNaN(date.valueOf())) return value;
  return date.toLocaleDateString(undefined, { month: "short", day: "numeric" });
}
