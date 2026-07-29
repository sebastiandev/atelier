import { useEffect, useMemo, useRef, useState } from "react";

import {
  DndContext,
  type DragEndEvent,
  PointerSensor,
  useSensor,
  useSensors,
} from "@dnd-kit/core";
import {
  SortableContext,
  rectSortingStrategy,
} from "@dnd-kit/sortable";

import { AgentTile } from "./AgentTile";
import {
  type AgentSummary,
  type ArtifactSummary,
  type ChatGrounding,
  type ChatDetail,
  type ChatSummary,
  type CompletionWorkspace,
  type ContextEntry,
  type CreateAgentPayload,
  type HandoffSummary,
  type LoopBrief,
  type LoopDefinition,
  type PlanArtifact,
  type PlanArtifactDetail,
  type PlanMaterializationStatus,
  type PrConfig,
  type PrFeedbackPayload,
  type PlanningProfile,
  type ProviderDescriptor,
  type ProjectSummary,
  type SharedFolderSummary,
  type WorkPlan,
  type WorkDetail,
  type WorkSummary,
  PERSONA_GLYPH,
  acceptPlanArtifactRun,
  approveWorkPlan,
  cancelPlanArtifactRun,
  checkPlanningFrameworkStatus,
  createAgent,
  createPlanBug,
  createPlanArtifactRunPrStage,
  detachAgent,
  ensureWorkChatContext,
  finishWorkPlan,
  getPlanArtifact,
  getProject,
  getWork,
  getWorkCompletion,
  getWorkPlan,
  getWorkPlanMaterializationStatus,
  listChats,
  listAgents,
  listArtifacts,
  refreshPrStatuses,
  refreshPlanArtifactRunPr,
  rerunPlanArtifactRun,
  listProjectShares,
  listProjects,
  listWorks,
  openAgentInConsole,
  patchWork,
  patchAgent,
  revealAgent,
  revealArtifact,
  revealWork,
  resumePlanArtifactRun,
  requestPlanArtifactRunChanges,
  sendPlanArtifactRunPrFeedback,
  startPlanningChat,
  startPlanningSetupChat,
  startPlanArtifactRun,
  startWorkPlan,
  updatePlanArtifact,
} from "./api";
import {
  ChatComposer,
  DeleteChatDialog,
  ChatRow,
  ChatTile,
  ContextDocModal,
  chatSummaryFromDetail,
} from "./Chat";
import { CompleteWorkDialog } from "./CompleteWorkDialog";
import { DeleteAgentDialog } from "./DeleteAgentDialog";
import { HandoffDialog } from "./HandoffDialog";
import {
  anchoredMenuPosition,
  type FloatingMenuPosition,
} from "./floatingMenu";
import {
  ChatIcon,
  CheckIcon,
  DocIcon,
  FolderIcon,
  MoveIcon,
  SearchIcon,
  SparkIcon,
} from "./Icons";
import { MoveWorkDialog } from "./MoveWorkDialog";
import { NewAgentDialog } from "./NewAgentDialog";
import { NewWorkDialog } from "./NewWorkDialog";
import { LoopMode } from "./LoopMode";
import {
  type LoopStartSeed,
  loopStartStorageKey,
} from "./loopSetup";
import { PaneResizeHandle } from "./PaneResizeHandle";
import {
  PlanningMode,
  type PlanOverviewTab,
  type PlanningView,
} from "./PlanningMode";
import {
  defaultPlanningArtifactRoot,
  type PlanningAgentConfig,
  type PlanningFrameworkId,
  type PlanningStartSeed,
  planningStartStorageKey,
} from "./planningSetup";
import {
  coerceProviderOptionsForModel,
  getProviderDescriptors,
  providerDefaults,
  providerOptionsPayload,
} from "./providerDescriptors";
import { SearchModal } from "./SearchModal";
import { ShellTopbar } from "./ShellTopbar";
import { SortableCanvasCell } from "./SortableCanvasCell";
import { Switcher, type SwitcherItem } from "./Switcher";
import {
  applyAgentOrder,
  useAgentOrderStore,
} from "./state/agentOrder";
import {
  selectWorkRevision,
  useArtifactsRefresh,
} from "./state/artifactsRefresh";
import { useClosedStore } from "./state/closed";
import {
  useLayoutStore,
  WORK_RAIL_MAX,
  WORK_RAIL_MIN,
} from "./state/layout";
import { editorUrl, useSettingsStore } from "./state/settings";

// Stable singleton so the selector below doesn't return a fresh ref on
// every render — Zustand's default Object.is snapshot check would
// otherwise treat each `[]` as a change and re-render in a loop.
const NO_CLOSED: readonly string[] = [];

type PlanningSetupPrompt = {
  rootPath: string;
  message: string;
};

type PlanningStartOverrides = {
  prompt?: string;
  framework?: PlanningFrameworkId;
  profile?: PlanningProfile;
  planArtifactsDir?: string | null;
  agentConfig?: PlanningAgentConfig | null;
};

export function WorkView({ workSlug }: { workSlug: string }) {
  const [work, setWork] = useState<WorkDetail | null>(null);
  const [project, setProject] = useState<ProjectSummary | null>(null);
  const [agents, setAgents] = useState<AgentSummary[]>([]);
  const [artifacts, setArtifacts] = useState<ArtifactSummary[]>([]);
  const [shares, setShares] = useState<SharedFolderSummary[]>([]);
  const [chats, setChats] = useState<ChatSummary[]>([]);
  const [plan, setPlan] = useState<WorkPlan | null>(null);
  const [planLoading, setPlanLoading] = useState(false);
  const [selectedPlanArtifactId, setSelectedPlanArtifactId] = useState<string | null>(null);
  const [planArtifactDetail, setPlanArtifactDetail] =
    useState<PlanArtifactDetail | null>(null);
  const [planDraft, setPlanDraft] = useState("");
  const [planPromptDraft, setPlanPromptDraft] = useState<string | null>(null);
  const [planProfile, setPlanProfile] = useState<PlanningProfile>("feature");
  const [planFramework, setPlanFramework] = useState<PlanningFrameworkId>("bmad");
  const [planArtifactRootDraft, setPlanArtifactRootDraft] = useState<string | null>(
    null,
  );
  const [planAgentConfig, setPlanAgentConfig] =
    useState<PlanningAgentConfig | null>(null);
  const [planError, setPlanError] = useState<string | null>(null);
  const [planSaving, setPlanSaving] = useState(false);
  const [planMaterializationStatus, setPlanMaterializationStatus] =
    useState<PlanMaterializationStatus | null>(null);
  const [planSelectedRoot, setPlanSelectedRoot] = useState<string | null>(null);
  const [planRootCleared, setPlanRootCleared] = useState(false);
  const planningStartConsumedRef = useRef(false);
  const [planningSetupPrompt, setPlanningSetupPrompt] =
    useState<PlanningSetupPrompt | null>(null);
  const [planningDialogOpen, setPlanningDialogOpen] = useState(false);
  const [planningEntryChecking, setPlanningEntryChecking] = useState(false);
  const [planningEntryIssue, setPlanningEntryIssue] = useState<{
    message: string;
    workspaces: CompletionWorkspace[];
  } | null>(null);
  const [workMode, setWorkMode] = useState<"manual" | "planning" | "loop">("planning");
  const [workModeExplicit, setWorkModeExplicit] = useState(false);
  const [loopStartSeed, setLoopStartSeed] = useState<LoopStartSeed | null>(null);
  const [planningView, setPlanningView] = useState<PlanningView>({ kind: "overview" });
  const [planOverviewTab, setPlanOverviewTab] =
    useState<PlanOverviewTab>("summary");
  const [planChatOpen, setPlanChatOpen] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [focusedSlug, setFocusedSlug] = useState<string | null>(null);
  const [openChatSlugs, setOpenChatSlugs] = useState<string[]>([]);
  const [agentDialogOpen, setAgentDialogOpen] = useState(false);
  // When the new-agent dialog is opened from the handoff/chat flow, we
  // pre-fill it with either the source agent handoff doc or chat context
  // file. Null in the regular flow.
  const [agentDialogPrefill, setAgentDialogPrefill] = useState<{
    forkFromAgent?: { slug: string; name: string; folder: string };
    initialGoal?: string;
    initialContexts?: ContextEntry[];
  } | null>(null);
  const [handoffSource, setHandoffSource] = useState<AgentSummary | null>(null);
  const [deleteTarget, setDeleteTarget] = useState<AgentSummary | null>(null);
  const [deleteChatTarget, setDeleteChatTarget] = useState<ChatSummary | null>(null);
  const [completeOpen, setCompleteOpen] = useState(false);
  const [workStatusSaving, setWorkStatusSaving] = useState(false);
  const [moveOpen, setMoveOpen] = useState(false);
  // Projects list for the move picker — fetched lazily when the dialog
  // opens so the WorkView doesn't pay the cost on every mount.
  const [allProjects, setAllProjects] = useState<ProjectSummary[] | null>(null);
  // Sibling-works list for the work switcher palette — fetched lazily
  // on first Shift+W / chevron click.
  const [allWorks, setAllWorks] = useState<WorkSummary[] | null>(null);
  const [projectSwitcherOpen, setProjectSwitcherOpen] = useState(false);
  const [workSwitcherOpen, setWorkSwitcherOpen] = useState(false);
  const [searchOpen, setSearchOpen] = useState(false);
  const [artifactSearchOpen, setArtifactSearchOpen] = useState(false);
  const [artifactSearchQuery, setArtifactSearchQuery] = useState("");
  const [chatComposerGrounding, setChatComposerGrounding] =
    useState<ChatGrounding | null | undefined>(undefined);
  const [contextDocFolder, setContextDocFolder] = useState<string | null>(null);
  const artifactsRevision = useArtifactsRefresh((s) =>
    selectWorkRevision(s, workSlug),
  );
  // Transient banner for the detach flow — fades out after 4s. Used both
  // for "launched in Terminal" success and "couldn't launch — command
  // copied to clipboard" fallback. One slot is plenty: detaches happen
  // one at a time and overlapping toasts would just compete for room.
  const [toast, setToast] = useState<string | null>(null);
  const [canvasOrderOverride, setCanvasOrderOverride] = useState<string[]>([]);
  const artifactSearchInputRef = useRef<HTMLInputElement>(null);
  const tileRefs = useRef<Map<string, HTMLDivElement>>(new Map());
  const closedSlugs = useClosedStore((s) => s.byWork[workSlug] ?? NO_CLOSED);
  const closeAgent = useClosedStore((s) => s.close);
  const restoreAgent = useClosedStore((s) => s.restore);
  const agentOrderOverride = useAgentOrderStore(
    (s) => s.byWork[workSlug],
  );
  const editor = useSettingsStore((s) => s.editor);
  const terminal = useSettingsStore((s) => s.terminal);
  const workRailWidth = useLayoutStore((s) => s.workRailWidth);
  const setWorkRailWidth = useLayoutStore((s) => s.setWorkRailWidth);

  // 6px activation distance keeps clicks on the grip from firing a drag —
  // matches @dnd-kit's recommended threshold and feels right with the
  // 22×22 grip target.
  const dragSensors = useSensors(
    useSensor(PointerSensor, { activationConstraint: { distance: 6 } }),
  );

  // Switcher rows. Projects ordered pinned-first; sibling works scoped
  // to the current work's project (or other loose works when the
  // current work is loose). The current work itself is filtered out —
  // switching to where you already are is a no-op the user doesn't need
  // to see.
  const projectItems = useMemo<SwitcherItem[]>(() => {
    if (!allProjects) return [];
    return [...allProjects]
      .sort((a, b) => {
        if (a.pinned !== b.pinned) return a.pinned ? -1 : 1;
        return a.name.localeCompare(b.name);
      })
      .map((p) => ({
        slug: p.slug,
        name: p.name,
        glyph: p.glyph,
        hue: p.color,
        href: `/projects/${p.slug}`,
      }));
  }, [allProjects]);

  const workItems = useMemo<SwitcherItem[]>(() => {
    if (!allWorks || !work) return [];
    const scoped = allWorks.filter(
      (w) => w.slug !== work.slug && w.project_slug === work.project_slug,
    );
    return [...scoped]
      .sort((a, b) => {
        if (a.status !== b.status) return a.status === "active" ? -1 : 1;
        return b.created_at.localeCompare(a.created_at);
      })
      .map((w) => ({
        slug: w.slug,
        name: w.name,
        subtitle:
          (project?.name ?? (w.project_slug ? w.project_slug : "Loose work")) +
          (w.status === "completed" ? " · completed" : ""),
        glyph: project?.glyph,
        hue: project?.color,
        href: `/works/${w.slug}`,
      }));
  }, [allWorks, work, project]);

  // Apply the user-controlled override (handoff insertions, future drag
  // reorder) on top of the backend's creation order. The result drives
  // both the rail and the canvas so they always stay in sync.
  const orderedAgents = useMemo(() => {
    const slugToAgent = new Map(agents.map((a) => [a.slug, a]));
    const ordered = applyAgentOrder(
      agentOrderOverride,
      agents.map((a) => a.slug),
    );
    return ordered
      .map((slug) => slugToAgent.get(slug))
      .filter((a): a is AgentSummary => a !== undefined);
  }, [agents, agentOrderOverride]);

  const artifactSearchActive = artifactSearchQuery.trim().length > 0;
  const pullRequests = useMemo(
    () => artifacts.filter((artifact) => artifact.type === "pr"),
    [artifacts],
  );
  const filteredArtifacts = useMemo(() => {
    if (!artifactSearchActive) return pullRequests;
    return pullRequests.filter((artifact) =>
      artifactMatchesSearch(artifact, artifactSearchQuery),
    );
  }, [pullRequests, artifactSearchActive, artifactSearchQuery]);

  useEffect(() => {
    if (!artifactSearchOpen) return;
    requestAnimationFrame(() => artifactSearchInputRef.current?.focus());
  }, [artifactSearchOpen]);

  useEffect(() => {
    setArtifactSearchOpen(false);
    setArtifactSearchQuery("");
  }, [workSlug]);

  useEffect(() => {
    const params = new URLSearchParams(window.location.search);
    if (params.has("mode") || params.has("start")) {
      params.delete("mode");
      params.delete("start");
      const query = params.toString();
      window.history.replaceState(
        null,
        "",
        `/works/${workSlug}${query ? `?${query}` : ""}`,
      );
    }
    setWork(null);
    setError(null);
    setWorkMode("planning");
    setWorkModeExplicit(false);
    setLoopStartSeed(readLoopStartSeed(workSlug));
    setPlanningView({ kind: "overview" });
    setPlanOverviewTab("summary");
    setPlanPromptDraft(null);
    setPlanProfile("feature");
    setPlanFramework("bmad");
    setPlanAgentConfig(null);
    setPlanSelectedRoot(null);
    setPlanRootCleared(false);
    setPlanningDialogOpen(false);
    setPlanningEntryChecking(false);
    setPlanningEntryIssue(null);
    planningStartConsumedRef.current = false;
    setPlanChatOpen(true);
  }, [workSlug]);

  useEffect(() => {
    if (!work) return;
    if (work.mode) {
      setWorkMode(work.mode);
      return;
    }
    if (workModeExplicit) return;
    if (plan !== null || planningChatFrom(chats, workSlug) !== null) {
      setWorkMode("planning");
      return;
    }
    if (agents.length > 0 || chats.length > 0) {
      setWorkMode("manual");
      return;
    }
    setWorkMode("planning");
  }, [agents.length, chats.length, plan, work, workModeExplicit, workSlug]);

  useEffect(() => {
    const persistedFramework =
      plan?.work_slug === workSlug
        ? plan.framework
        : planningFrameworkFromChat(planningChatFrom(chats, workSlug));
    if (persistedFramework) setPlanFramework(persistedFramework);
  }, [chats, plan, workSlug]);

  useEffect(() => {
    if (!work || planningStartConsumedRef.current) return;
    const key = planningStartStorageKey(workSlug);
    const raw = sessionStorage.getItem(key);
    if (!raw) return;
    let seed: PlanningStartSeed;
    try {
      seed = JSON.parse(raw) as PlanningStartSeed;
    } catch {
      sessionStorage.removeItem(key);
      return;
    }
    if (!seed.folder.trim()) {
      sessionStorage.removeItem(key);
      return;
    }
    planningStartConsumedRef.current = true;
    sessionStorage.removeItem(key);
    setWorkMode("planning");
    setWorkModeExplicit(true);
    setPlanSelectedRoot(seed.folder);
    setPlanRootCleared(false);
    setPlanArtifactRootDraft(seed.planDir);
    setPlanFramework(seed.framework);
    setPlanProfile(seed.profile);
    setPlanPromptDraft(seed.idea || work.description || work.name);
    setPlanAgentConfig(seed.agentConfig);
    void handleStartPlanningChat(seed.folder, {
      prompt: seed.idea || work.description || work.name,
      framework: seed.framework,
      profile: seed.profile,
      planArtifactsDir: seed.planDir,
      agentConfig: seed.agentConfig,
    }).catch(() => {});
  }, [work, workSlug]);

  // Open the project switcher and lazy-fetch the project list if we
  // don't have it yet. The switcher renders gracefully against an empty
  // list and re-renders once the fetch resolves.
  function openProjectSwitcher() {
    if (allProjects === null) {
      listProjects()
        .then(setAllProjects)
        .catch(() => setAllProjects([]));
    }
    setProjectSwitcherOpen(true);
  }
  function openWorkSwitcher() {
    if (allWorks === null) {
      listWorks()
        .then(setAllWorks)
        .catch(() => setAllWorks([]));
    }
    setWorkSwitcherOpen(true);
  }
  function openSearch() {
    if (allProjects === null) {
      listProjects()
        .then(setAllProjects)
        .catch(() => setAllProjects([]));
    }
    if (allWorks === null) {
      listWorks()
        .then(setAllWorks)
        .catch(() => setAllWorks([]));
    }
    setSearchOpen(true);
  }

  function openChatComposer() {
    if (allProjects === null) {
      listProjects()
        .then(setAllProjects)
        .catch(() => setAllProjects([]));
    }
    if (allWorks === null) {
      listWorks()
        .then(setAllWorks)
        .catch(() => setAllWorks([]));
    }
    setChatComposerGrounding({ kind: "work", ref: workSlug });
  }

  function chooseWorkMode(mode: "manual" | "planning" | "loop") {
    setWorkMode(mode);
    setWorkModeExplicit(true);
    if (!work || work.mode === mode) return;
    void patchWork(work.slug, { mode })
      .then(setWork)
      .catch((reason) => setError(reason instanceof Error ? reason.message : String(reason)));
  }

  async function unsafePlanningWorkspaces(): Promise<CompletionWorkspace[]> {
    if (!work) return [];
    const preview = await getWorkCompletion(work.slug);
    return preview.workspaces.filter((workspace) => !workspace.removable);
  }

  async function openPlanningDialog() {
    if (!work || planningEntryChecking) return;
    setPlanningEntryChecking(true);
    setPlanningEntryIssue(null);
    try {
      const workspaces = await unsafePlanningWorkspaces();
      if (workspaces.length > 0) {
        setPlanningEntryIssue({
          message:
            "Planning mode would hide the current agent workspaces. Commit, preserve, or discard their local changes before switching modes.",
          workspaces,
        });
        return;
      }
      setPlanningDialogOpen(true);
    } catch (reason) {
      setPlanningEntryIssue({
        message:
          reason instanceof Error
            ? `Could not inspect the current agent workspaces: ${reason.message}`
            : "Could not inspect the current agent workspaces.",
        workspaces: [],
      });
    } finally {
      setPlanningEntryChecking(false);
    }
  }

  async function startPlanningFromDialog(seed: PlanningStartSeed) {
    const workspaces = await unsafePlanningWorkspaces();
    if (workspaces.length > 0) {
      setPlanningDialogOpen(false);
      setPlanningEntryIssue({
        message:
          "Agent workspace changes appeared while Planning was being configured. Preserve them before switching modes.",
        workspaces,
      });
      return;
    }
    setPlanSelectedRoot(seed.folder);
    setPlanRootCleared(false);
    setPlanArtifactRootDraft(seed.planDir);
    setPlanFramework(seed.framework);
    setPlanProfile(seed.profile);
    setPlanPromptDraft(seed.idea);
    setPlanAgentConfig(seed.agentConfig);
    await handleStartPlanningChat(seed.folder, {
      prompt: seed.idea,
      framework: seed.framework,
      profile: seed.profile,
      planArtifactsDir: seed.planDir,
      agentConfig: seed.agentConfig,
    });
    setPlanningDialogOpen(false);
  }

  async function reopenWork() {
    if (!work || work.status === "active") return;
    setWorkStatusSaving(true);
    setError(null);
    setPlanError(null);
    try {
      setWork(await patchWork(work.slug, { status: "active" }));
    } catch (reason) {
      const message = reason instanceof Error ? reason.message : String(reason);
      setError(message);
      setPlanError(message);
    } finally {
      setWorkStatusSaving(false);
    }
  }

  function openPlanningView(next: PlanningView) {
    setPlanningView(next);
    // Every view that shows one artifact has to select it, or its detail is
    // never fetched and the view renders nothing.
    if (
      next.kind === "artifact"
      || next.kind === "source"
      || next.kind === "run"
      || next.kind === "setup"
    ) {
      setSelectedPlanArtifactId(next.id);
      setFocusedSlug(`plan:${next.id}`);
    } else {
      setSelectedPlanArtifactId(null);
      setFocusedSlug(null);
    }
  }

  // Shortcuts:
  //   N       → new agent
  //   Shift+C → new chat (grounded to this work)
  //   Shift+W → switch to a sibling work (palette)
  //   Shift+P → switch project (palette)
  // Suppressed while any modal is open, when a chord modifier is held,
  // and inside editable fields so typing "n" in the composer doesn't
  // pop the dialog.
  useEffect(() => {
    function onKey(e: KeyboardEvent) {
      if (
        agentDialogOpen ||
        completeOpen ||
        moveOpen ||
        handoffSource !== null ||
        deleteTarget !== null ||
        projectSwitcherOpen ||
        workSwitcherOpen ||
        chatComposerGrounding !== undefined ||
        contextDocFolder !== null ||
        searchOpen
      )
        return;
      if (e.metaKey || e.ctrlKey || e.altKey) return;
      const target = e.target as HTMLElement | null;
      const tag = target?.tagName;
      if (tag === "INPUT" || tag === "TEXTAREA" || target?.isContentEditable)
        return;
      if (e.shiftKey && (e.key === "F" || e.key === "f" || e.key === "S" || e.key === "s")) {
        e.preventDefault();
        openSearch();
      } else if (e.shiftKey && (e.key === "W" || e.key === "w")) {
        e.preventDefault();
        openWorkSwitcher();
      } else if (e.shiftKey && (e.key === "P" || e.key === "p")) {
        e.preventDefault();
        openProjectSwitcher();
      } else if (!e.shiftKey && (e.key === "n" || e.key === "N")) {
        if (work?.status !== "active") return;
        e.preventDefault();
        setAgentDialogOpen(true);
      } else if (e.shiftKey && (e.key === "c" || e.key === "C")) {
        if (work?.status !== "active") return;
        e.preventDefault();
        openChatComposer();
      }
    }
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [
    agentDialogOpen,
    completeOpen,
    moveOpen,
    handoffSource,
    deleteTarget,
    projectSwitcherOpen,
    workSwitcherOpen,
    chatComposerGrounding,
    contextDocFolder,
    searchOpen,
    allProjects,
    allWorks,
    work?.status,
  ]);

  // Refetch on revision bump (an agent emitted artifact_recorded) AND on
  // mount / workSlug change. Initial fetch is the same call so we don't
  // need a separate effect.
  useEffect(() => {
    let cancelled = false;
    listArtifacts(workSlug)
      .then((rows) => {
        if (cancelled) return;
        setArtifacts(rows);
        // If any PR row is non-terminal, kick a background refresh so
        // the freshly-opened tab doesn't show statuses up to 5 min
        // stale. The backend throttles to one refresh per ~30s, so
        // tab-bouncing won't fan out per-click GitHub fetches.
        const hasOpenPr = rows.some(
          (r) => r.type === "pr" && (r.status === "open" || r.status === "draft"),
        );
        if (hasOpenPr) {
          refreshPrStatuses()
            .then((res) => {
              // Only re-fetch the artifact list when the backend
              // actually ran a refresh that touched something. The
              // throttle short-circuit (ran=false) and the zero-update
              // case both leave persisted state unchanged.
              if (cancelled || !res.ran || res.updated === 0) return;
              listArtifacts(workSlug)
                .then((freshRows) => {
                  if (!cancelled) setArtifacts(freshRows);
                })
                .catch(() => {});
            })
            .catch(() => {
              // Best-effort hint, not a user-visible failure path.
            });
        }
      })
      .catch(() => {
        // Silent: rail just shows the previous list (or empty on first
        // mount). Errors surface via the work-fetch effect below.
      });
    return () => {
      cancelled = true;
    };
  }, [workSlug, artifactsRevision]);

  useEffect(() => {
    let cancelled = false;
    setPlanLoading(true);
    setPlanError(null);
    setSelectedPlanArtifactId(null);
    setPlanArtifactDetail(null);
    setPlanDraft("");
    setPlanMaterializationStatus(null);
    getWorkPlan(workSlug)
      .then((next) => {
        if (cancelled) return;
        setPlan(next);
        setPlanOverviewTab(defaultPlanTab(next));
      })
      .catch((err) => {
        if (cancelled) return;
        const message = err instanceof Error ? err.message : String(err);
        if (message.startsWith("404 ")) {
          setPlan(null);
        } else {
          setPlanError(message);
        }
      })
      .finally(() => {
        if (!cancelled) setPlanLoading(false);
      });
    return () => {
      cancelled = true;
    };
  }, [workSlug]);

  useEffect(() => {
    if (workMode !== "planning" || plan !== null) {
      setPlanMaterializationStatus(null);
      return;
    }

    let cancelled = false;
    let loadedComplete = false;
    let inFlight = false;
    let controller: AbortController | null = null;

    async function pollMaterialization() {
      if (inFlight) return;
      inFlight = true;
      controller = new AbortController();
      try {
        const status = await getWorkPlanMaterializationStatus(workSlug, {
          signal: controller.signal,
        });
        if (cancelled) return;
        setPlanMaterializationStatus(status);
        if (status.state === "complete" && !loadedComplete) {
          loadedComplete = true;
          const next = await getWorkPlan(workSlug);
          if (cancelled) return;
          setPlan(next);
          setPlanOverviewTab(defaultPlanTab(next));
          setPlanChatOpen(true);
          openPlanningView({ kind: "overview" });
          showToast("Source plan created.");
        }
      } catch (err) {
        if (!cancelled && !(err instanceof DOMException && err.name === "AbortError")) {
          setPlanMaterializationStatus(null);
        }
      } finally {
        inFlight = false;
        controller = null;
      }
    }

    void pollMaterialization();
    const timer = window.setInterval(() => void pollMaterialization(), 3000);
    return () => {
      cancelled = true;
      controller?.abort();
      window.clearInterval(timer);
    };
  }, [workSlug, workMode, plan]);

  useEffect(() => {
    if (!selectedPlanArtifactId) {
      setPlanArtifactDetail(null);
      setPlanDraft("");
      return;
    }
    let cancelled = false;
    setPlanError(null);
    getPlanArtifact(workSlug, selectedPlanArtifactId)
      .then((detail) => {
        if (cancelled) return;
        setPlanArtifactDetail(detail);
        setPlanDraft(detail.content);
      })
      .catch((err) => {
        if (!cancelled) {
          setPlanError(err instanceof Error ? err.message : String(err));
        }
      });
    return () => {
      cancelled = true;
    };
  }, [workSlug, selectedPlanArtifactId]);

  // A run is settled once it can no longer change on its own. Accepted and
  // cleaned are done; cancelled and failed need a user action to move, and
  // that action refreshes on its own.
  const planDetailRef = useRef<PlanArtifactDetail | null>(null);
  planDetailRef.current = planArtifactDetail;

  const planHasUnsettledRun = useMemo(
    () =>
      (plan?.artifacts ?? []).some((artifact) =>
        artifact.runs.some(
          (run) =>
            !run.loop_status
            || !["accepted", "cleaned", "cancelled", "failed"].includes(
              run.loop_status,
            ),
        ),
      ),
    [plan],
  );

  useEffect(() => {
    // Poll until every run has settled, not just while one is `running`.
    // The stage transition a user is waiting for is often the thing that
    // ends `running` -- approving a review moves the run to
    // awaiting_approval -- so gating on it tore the interval down at
    // exactly the moment there was something new to show, and the change
    // only appeared on a manual refresh.
    if (workMode !== "planning" || !plan || !planHasUnsettledRun) return;
    let cancelled = false;
    let inFlight = false;

    async function pollPlanRuns() {
      if (inFlight) return;
      inFlight = true;
      try {
        const [next, detail] = await Promise.all([
          getWorkPlan(workSlug),
          selectedPlanArtifactId
            ? getPlanArtifact(workSlug, selectedPlanArtifactId)
            : Promise.resolve(null),
        ]);
        if (cancelled) return;
        setPlan(next);
        if (detail) {
          const previous = planDetailRef.current;
          setPlanArtifactDetail(detail);
          setPlanDraft((currentDraft) =>
            !previous || currentDraft === previous.content
              ? detail.content
              : currentDraft,
          );
        }
      } catch {
        // Polling is opportunistic; explicit user actions still surface errors.
      } finally {
        inFlight = false;
      }
    }

    // Lead with one fetch: an action that changes run state should show up
    // now, not on the next tick.
    void pollPlanRuns();
    const timer = window.setInterval(() => void pollPlanRuns(), 3000);
    return () => {
      cancelled = true;
      window.clearInterval(timer);
    };
  }, [
    workMode,
    workSlug,
    planHasUnsettledRun,
    selectedPlanArtifactId,
  ]);

  useEffect(() => {
    let cancelled = false;
    Promise.all([getWork(workSlug), listAgents(workSlug), listChats({ work_slug: workSlug })])
      .then(([w, a, c]) => {
        if (cancelled) return;
        setWork(w);
        setPlanPromptDraft((current) => current ?? (w.description || w.name));
        setPlanProfile(inferPlanningProfile(w.description || w.name));
        setAgents(a);
        setChats(c);
        const initialChatSlug = new URLSearchParams(window.location.search).get("chat");
        if (initialChatSlug && c.some((chat) => chat.slug === initialChatSlug)) {
          focusChat(initialChatSlug);
          window.history.replaceState(null, "", `/works/${workSlug}`);
        } else if (a.length > 0) {
          setFocusedSlug(a[0].slug);
        }
        // Fetch the project lazily so the breadcrumb can render its name
        // and tint without a second mount cycle. Failure is silent — the
        // crumb just falls back to the slug.
        if (w.project_slug) {
          getProject(w.project_slug)
            .then((p) => {
              if (!cancelled) setProject(p);
            })
            .catch(() => {});
          listProjectShares(w.project_slug)
            .then((s) => {
              if (!cancelled) setShares(s);
            })
            .catch(() => {
              if (!cancelled) setShares([]);
            });
        } else {
          setProject(null);
          setShares([]);
        }
      })
      .catch((e) => {
        if (!cancelled) setError(e instanceof Error ? e.message : String(e));
      });
    return () => {
      cancelled = true;
    };
  }, [workSlug]);

  async function refreshAgents() {
    const next = await listAgents(workSlug);
    setAgents(next);
    return next;
  }

  function focusAgent(slug: string) {
    // Click on a closed rail entry restores the tile (mounts AgentTile,
    // which reopens the WS — the supervisor resumes the provider session
    // by ID so the conversation continues from where it left off).
    setSelectedPlanArtifactId(null);
    if (closedSlugs.includes(slug)) {
      restoreAgent(workSlug, slug);
    }
    setFocusedSlug(slug);
    requestAnimationFrame(() => {
      const el = tileRefs.current.get(slug);
      if (el) el.scrollIntoView({ behavior: "smooth", block: "start" });
    });
  }

  function focusChat(slug: string) {
    setSelectedPlanArtifactId(null);
    setOpenChatSlugs((prev) => (prev.includes(slug) ? prev : [...prev, slug]));
    setCanvasOrderOverride((prev) => (prev.includes(slug) ? prev : [...prev, slug]));
    setFocusedSlug(slug);
    requestAnimationFrame(() => {
      const el = tileRefs.current.get(slug);
      if (el) el.scrollIntoView({ behavior: "smooth", block: "start" });
    });
  }

  function closeChat(slug: string) {
    setOpenChatSlugs((prev) => prev.filter((s) => s !== slug));
    setCanvasOrderOverride((prev) => prev.filter((s) => s !== slug));
    if (focusedSlug === slug) setFocusedSlug(null);
  }

  function focusPlanArtifact(artifactId: string) {
    chooseWorkMode("planning");
    openPlanningView({ kind: "artifact", id: artifactId });
  }

  function patchChatSummary(chat: ChatSummary) {
    setChats((curr) =>
      curr.map((c) =>
        c.slug === chat.slug ? normalizePlanningChatSummary(chat, workSlug) : c,
      ),
    );
  }

  function removeChatEverywhere(slug: string) {
    setChats((curr) => curr.filter((c) => c.slug !== slug));
    setOpenChatSlugs((prev) => prev.filter((s) => s !== slug));
    setCanvasOrderOverride((prev) => prev.filter((s) => s !== slug));
    tileRefs.current.delete(slug);
    if (focusedSlug === slug) setFocusedSlug(null);
  }

  async function handleStartAgentFromChat(chat: ChatDetail) {
    const currentWork = work;
    if (!currentWork) return;
    if (currentWork.status !== "active") {
      showToast("Reopen this work before starting an agent.");
      return;
    }
    let folder =
      currentWork.chat_context_folders.find((f) => f.chat_slug === chat.slug) ??
      null;
    if (folder === null) {
      folder = await ensureWorkChatContext(currentWork.slug, chat.slug);
      setWork((curr) => {
        if (!curr) return curr;
        const without = curr.chat_context_folders.filter(
          (f) => f.chat_slug !== folder!.chat_slug,
        );
        return { ...curr, chat_context_folders: [...without, folder!] };
      });
    }
    if (!folder.absolute_path) {
      throw new Error(`chat context path missing for ${chat.slug}`);
    }
    const filePath = `${folder.absolute_path}/${folder.context_filename}`;
    setAgentDialogPrefill({
      initialGoal: `Use the attached ${chat.slug} context file as the starting point.`,
      initialContexts: [{ type: "file", value: filePath, conn_id: null }],
    });
    setAgentDialogOpen(true);
  }

  async function refreshPlan(selectId?: string | null) {
    const next = await getWorkPlan(workSlug);
    setPlan(next);
    setPlanOverviewTab(defaultPlanTab(next));
    if (selectId !== undefined) {
      setSelectedPlanArtifactId(selectId);
    }
    return next;
  }

  async function createPlanningSetupChat(rootPath: string) {
    if (!work) return;
    setPlanLoading(true);
    setPlanError(null);
    try {
      const status = await checkPlanningFrameworkStatus(work.slug, {
        root_path: rootPath,
        framework: planFramework,
      });
      if (status.ready) {
        setPlanningSetupPrompt(null);
        await handleStartPlanningChat(rootPath);
        return;
      }
      const agent = await resolvePlanningAgentConfig(planAgentConfig);
      const created = await startPlanningSetupChat(work.slug, {
        root_path: rootPath,
        framework: planFramework,
        profile: planProfile,
        provider: agent.provider.name,
        model: agent.model,
        options: providerOptionsPayload(agent.provider, agent.model, agent.options),
      });
      setChats((current) => [
        chatSummaryFromDetail(created),
        ...current.filter((chat) => chat.slug !== created.slug),
      ]);
      setPlanningSetupPrompt(null);
      chooseWorkMode("manual");
      focusChat(created.slug);
      showToast("Planning setup chat created.");
    } catch (err) {
      const message = err instanceof Error ? err.message : String(err);
      setPlanError(message);
      setPlanningSetupPrompt((current) =>
        current ? { ...current, message } : current,
      );
    } finally {
      setPlanLoading(false);
    }
  }

  async function handleStartPlanningChat(
    rootPath: string,
    overrides: PlanningStartOverrides = {},
  ) {
    if (!work) return;
    const prompt =
      (overrides.prompt ?? planPromptDraft ?? work.description ?? work.name).trim();
    const framework = overrides.framework ?? planFramework;
    const profile = overrides.profile ?? planProfile;
    const agentConfig = overrides.agentConfig ?? planAgentConfig;
    const planArtifactsDir =
      overrides.planArtifactsDir !== undefined
        ? overrides.planArtifactsDir
        : planArtifactRootDraft ?? defaultPlanningArtifactRoot(framework, work.slug);
    setPlanSelectedRoot(rootPath);
    setPlanRootCleared(false);
    setPlanLoading(true);
    setPlanError(null);
    try {
      const existing = planningChatFrom(chats, work.slug);
      if (existing) {
        chooseWorkMode("planning");
        return;
      }
      const status = await checkPlanningFrameworkStatus(work.slug, {
        root_path: rootPath,
        framework,
      });
      if (!status.ready) {
        const command = status.setup_command.join(" ");
        const message = `${status.label} is not initialized in ${status.root_path}. ${status.setup_hint}${command ? ` Command: ${command}` : ""}`;
        setPlanningSetupPrompt({ rootPath, message });
        return;
      }
      const agent = await resolvePlanningAgentConfig(agentConfig);
      const created = await startPlanningChat(work.slug, {
        root_path: rootPath,
        idea: prompt || work.name,
        plan_artifacts_dir: planArtifactsDir?.trim() || null,
        framework,
        profile,
        provider: agent.provider.name,
        model: agent.model,
        options: providerOptionsPayload(agent.provider, agent.model, agent.options),
      });
      setChats((current) => [
        chatSummaryFromDetail({ ...created, title: "Planning" }),
        ...current.filter((chat) => chat.slug !== created.slug),
      ]);
      chooseWorkMode("planning");
      openPlanningView({ kind: "overview" });
    } catch (err) {
      setPlanError(err instanceof Error ? err.message : String(err));
      throw err;
    } finally {
      setPlanLoading(false);
    }
  }

  async function handleCreateSourcePlanFromPlanningChat() {
    if (!work) return;
    const resumeFromWaiting =
      planMaterializationStatus?.state === "waiting_permission";
    const planningChat = planningChatFrom(chats, work.slug);
    if (!planningChat) {
      setPlanError("Planning chat not found. Start planning again.");
      return;
    }
    if (!planningChat.planning_readiness?.ready) {
      setPlanError(
        "Planning chat is not ready to create a source plan yet. Continue the planning conversation.",
      );
      return;
    }
    if (planMaterializationStatus?.state === "running") {
      setPlanError("Source plan materialization is already running.");
      return;
    }
    const rootPath = planningChat.working_directory;
    if (!rootPath) {
      setPlanError("Planning chat has no working folder. Start planning again.");
      return;
    }
    setPlanLoading(true);
    setPlanSaving(true);
    setPlanError(null);
    setPlanMaterializationStatus({
      state: "running",
      chat_slug: null,
      updated_at: null,
      last_seq: null,
      last_event_type: null,
      last_event_summary: "",
      message: "Materializer is starting.",
      tool_name: null,
    });
    try {
      const payload = {
        planning_chat_slug: planningChat.slug,
      };
      void startWorkPlan(work.slug, payload)
        .then((result) => {
          setPlanMaterializationStatus((current) =>
            resumeFromWaiting &&
            current?.state === "running" &&
            result.materialization_status.state === "waiting_permission"
              ? current
              : result.materialization_status,
          );
          if (!result.plan) return;
          setPlan(result.plan);
          setPlanOverviewTab(defaultPlanTab(result.plan));
          setPlanChatOpen(true);
          chooseWorkMode("planning");
          openPlanningView({ kind: "overview" });
          showToast("Source plan created.");
        })
        .catch((err) => {
          void handleSourcePlanStartFailure(work.slug, err);
        });
      chooseWorkMode("planning");
    } catch (err) {
      setPlanError(err instanceof Error ? err.message : String(err));
    } finally {
      setPlanSaving(false);
      setPlanLoading(false);
    }
  }

  async function handleSourcePlanStartFailure(targetWorkSlug: string, err: unknown) {
    const message = err instanceof Error ? err.message : String(err);
    try {
      const status = await getWorkPlanMaterializationStatus(targetWorkSlug);
      setPlanMaterializationStatus(status);
      if (status.state === "complete") {
        const next = await getWorkPlan(targetWorkSlug);
        setPlan(next);
        setPlanOverviewTab(defaultPlanTab(next));
        setPlanChatOpen(true);
        chooseWorkMode("planning");
        openPlanningView({ kind: "overview" });
        showToast("Source plan created.");
        return;
      }
      if (
        status.state === "running" ||
        status.state === "waiting_permission" ||
        status.state === "stalled"
      ) {
        return;
      }
      if (status.state === "failed" && status.message) {
        setPlanError(status.message);
        return;
      }
    } catch {
      // Fall through to the original transport error.
    }
    setPlanError(message);
  }

  async function handleSavePlanArtifact() {
    if (!planArtifactDetail) return;
    setPlanSaving(true);
    setPlanError(null);
    try {
      const saved = await updatePlanArtifact(
        workSlug,
        planArtifactDetail.artifact.id,
        {
          content: planDraft,
          expected_hash: planArtifactDetail.artifact.source_hash,
        },
      );
      setPlanArtifactDetail(saved);
      setPlanDraft(saved.content);
      await refreshPlan(saved.artifact.id);
    } catch (err) {
      setPlanError(err instanceof Error ? err.message : String(err));
    } finally {
      setPlanSaving(false);
    }
  }

  async function handleApprovePlan() {
    setPlanSaving(true);
    setPlanError(null);
    try {
      const next = await approveWorkPlan(workSlug);
      setPlan(next);
      setPlanOverviewTab(defaultPlanTab(next));
      if (selectedPlanArtifactId) {
        const detail = await getPlanArtifact(workSlug, selectedPlanArtifactId);
        setPlanArtifactDetail(detail);
        setPlanDraft(detail.content);
      }
    } catch (err) {
      setPlanError(err instanceof Error ? err.message : String(err));
    } finally {
      setPlanSaving(false);
    }
  }

  async function handleFinishPlanningConversation() {
    setPlanSaving(true);
    setPlanError(null);
    try {
      const next = await finishWorkPlan(workSlug);
      setPlan(next);
      setPlanOverviewTab(defaultPlanTab(next));
      openPlanningView({ kind: "overview" });
    } catch (err) {
      setPlanError(err instanceof Error ? err.message : String(err));
    } finally {
      setPlanSaving(false);
    }
  }

  async function handleResolvePlanLoopBlocker(
    artifact: PlanArtifact,
    runId: string,
    agentSlug: string,
    retryFailed = false,
    resolutionNote = "",
    gateDecision?: "send_back" | "approve_as_is",
    enforcedFindings: number[] = [],
  ) {
    setPlanSaving(true);
    setPlanError(null);
    try {
      const saved = await resumePlanArtifactRun(workSlug, artifact.id, runId, {
        resolution_note: resolutionNote || undefined,
        retry_failed: retryFailed,
        gate_decision: gateDecision,
        enforced_findings: enforcedFindings,
      });
      setPlanArtifactDetail(saved);
      setPlanDraft(saved.content);
      await refreshPlan(saved.artifact.id);
      setFocusedSlug(agentSlug);
    } catch (err) {
      setPlanError(err instanceof Error ? err.message : String(err));
    } finally {
      setPlanSaving(false);
    }
  }

  async function handleApprovePlanRun(artifact: PlanArtifact, runId: string) {
    setPlanSaving(true);
    setPlanError(null);
    try {
      const saved = await acceptPlanArtifactRun(workSlug, artifact.id, runId, {});
      setPlanArtifactDetail(saved);
      setPlanDraft(saved.content);
      await refreshPlan(saved.artifact.id);
      showToast("Result approved.");
    } catch (err) {
      setPlanError(err instanceof Error ? err.message : String(err));
      throw err;
    } finally {
      setPlanSaving(false);
    }
  }

  async function handleCancelPlanRun(artifact: PlanArtifact, runId: string) {
    setPlanSaving(true);
    setPlanError(null);
    try {
      const saved = await cancelPlanArtifactRun(workSlug, artifact.id, runId);
      setPlanArtifactDetail(saved);
      setPlanDraft(saved.content);
      await refreshPlan(saved.artifact.id);
      showToast("Run cancelled. Its workspace was preserved.");
    } catch (err) {
      setPlanError(err instanceof Error ? err.message : String(err));
      throw err;
    } finally {
      setPlanSaving(false);
    }
  }

  async function handleRequestPlanRunChanges(
    artifact: PlanArtifact,
    runId: string,
    note: string,
  ) {
    setPlanSaving(true);
    setPlanError(null);
    try {
      const saved = await requestPlanArtifactRunChanges(
        workSlug,
        artifact.id,
        runId,
        note,
      );
      setPlanArtifactDetail(saved);
      setPlanDraft(saved.content);
      await refreshPlan(saved.artifact.id);
      showToast("Changes requested. Implementation resumed.");
    } catch (err) {
      setPlanError(err instanceof Error ? err.message : String(err));
      throw err;
    } finally {
      setPlanSaving(false);
    }
  }

  async function handleCreatePlanRunPr(
    artifact: PlanArtifact,
    runId: string,
    setup: PrConfig,
  ) {
    setPlanSaving(true);
    setPlanError(null);
    try {
      const saved = await createPlanArtifactRunPrStage(
        workSlug,
        artifact.id,
        runId,
        setup,
      );
      setPlanArtifactDetail(saved);
      setPlanDraft(saved.content);
      await refreshPlan(saved.artifact.id);
    } catch (err) {
      setPlanError(err instanceof Error ? err.message : String(err));
      throw err;
    } finally {
      setPlanSaving(false);
    }
  }

  async function handleSendPlanRunPrFeedback(
    artifact: PlanArtifact,
    runId: string,
    payload: PrFeedbackPayload,
  ) {
    setPlanSaving(true);
    setPlanError(null);
    try {
      const saved = await sendPlanArtifactRunPrFeedback(
        workSlug,
        artifact.id,
        runId,
        payload,
      );
      setPlanArtifactDetail(saved);
      setPlanDraft(saved.content);
      await refreshPlan(saved.artifact.id);
    } catch (err) {
      setPlanError(err instanceof Error ? err.message : String(err));
      throw err;
    } finally {
      setPlanSaving(false);
    }
  }

  async function handleFollowUpPlanRun(
    artifact: PlanArtifact,
    runId: string,
    kind: "amend" | "verify",
    note: string,
  ) {
    setPlanSaving(true);
    setPlanError(null);
    try {
      const saved = await rerunPlanArtifactRun(workSlug, artifact.id, runId, {
        kind,
        note: note || undefined,
      });
      setPlanArtifactDetail(saved);
      setPlanDraft(saved.content);
      await Promise.all([refreshAgents(), refreshPlan(saved.artifact.id)]);
      const started = saved.artifact.runs.at(-1);
      if (started) openPlanningView({ kind: "run", id: saved.artifact.id, runId: started.id });
      showToast(kind === "amend" ? "Applying feedback." : "Verifying current state.");
    } catch (err) {
      setPlanError(err instanceof Error ? err.message : String(err));
    } finally {
      setPlanSaving(false);
    }
  }

  async function handleRefreshPlanRunPr(
    artifact: PlanArtifact,
    runId: string,
    force = false,
  ) {
    if (force) {
      setPlanSaving(true);
      setPlanError(null);
    }
    try {
      const saved = await refreshPlanArtifactRunPr(
        workSlug,
        artifact.id,
        runId,
        force,
      );
      setPlanArtifactDetail(saved);
      setPlanDraft(saved.content);
      if (force) await refreshPlan(saved.artifact.id);
    } catch (err) {
      if (force) setPlanError(err instanceof Error ? err.message : String(err));
    } finally {
      if (force) setPlanSaving(false);
    }
  }

  async function handleCreatePlanBug(
    artifactId: string,
    title: string,
    description: string,
  ) {
    setPlanSaving(true);
    setPlanError(null);
    try {
      const saved = await createPlanBug(workSlug, artifactId, {
        title,
        description,
      });
      const bugLink = [...saved.artifact.tracking]
        .reverse()
        .find((link) => link.kind === "bug" && link.ref);
      const nextArtifactId = bugLink?.ref || saved.artifact.id;
      openPlanningView({ kind: "artifact", id: nextArtifactId });
      await refreshPlan(nextArtifactId);
      const nextDetail =
        nextArtifactId === saved.artifact.id
          ? saved
          : await getPlanArtifact(workSlug, nextArtifactId);
      setPlanArtifactDetail(nextDetail);
      setPlanDraft(nextDetail.content);
      showToast(`Added ${nextArtifactId}.`);
    } catch (err) {
      const message = err instanceof Error ? err.message : String(err);
      setPlanError(message);
      throw err;
    } finally {
      setPlanSaving(false);
    }
  }

  async function handleStartPlanRunFromSetup(
    detail: PlanArtifactDetail,
    definition: LoopDefinition,
    brief: LoopBrief,
  ): Promise<void> {
    setPlanSaving(true);
    setPlanError(null);
    try {
      const linked = await startPlanArtifactRun(
        workSlug,
        detail.artifact.id,
        definition,
        undefined,
        brief,
      );
      setPlanArtifactDetail(linked);
      setPlanDraft(linked.content);
      await Promise.all([refreshAgents(), refreshPlan(linked.artifact.id)]);
      const started = linked.artifact.runs.at(-1);
      openPlanningView({ kind: "run", id: linked.artifact.id, runId: started?.id });
      showToast(`Started ${definition.name}.`);
    } catch (err) {
      setPlanError(err instanceof Error ? err.message : String(err));
    } finally {
      setPlanSaving(false);
    }
  }

  function handleLaunchPlanArtifact(detail: PlanArtifactDetail) {
    if (!detail.artifact.launchable) {
      setPlanError(detail.artifact.launch_blockers[0] ?? "Artifact is not launchable yet.");
      return;
    }
    // Run setup, not a bare picker: a story stage has no parent agent to
    // inherit a provider from, so the loop has to be configured first.
    openPlanningView({ kind: "setup", id: detail.artifact.id });
  }

  async function handleCreateAgent(payload: CreateAgentPayload) {
    const created = await createAgent(workSlug, payload);
    const next = await refreshAgents();
    // If the new agent was forked from a source, position it
    // immediately after that source in the rail/canvas. Capture the
    // current rendered order so the override stores a complete order
    // rather than a sparse anchor+new pair.
    if (payload.fork_from_agent) {
      const currentOrder = orderedAgents.map((a) => a.slug);
      useAgentOrderStore
        .getState()
        .insertAfter(workSlug, payload.fork_from_agent, created.slug, currentOrder);
    }
    setAgentDialogOpen(false);
    setAgentDialogPrefill(null);
    setFocusedSlug(created.slug);
    requestAnimationFrame(() => {
      const el = tileRefs.current.get(created.slug);
      if (el) el.scrollIntoView({ behavior: "smooth", block: "start" });
    });
    return next;
  }

  function showToast(message: string) {
    setToast(message);
    window.setTimeout(() => setToast((current) => (current === message ? null : current)), 4000);
  }

  async function handleDetach(agentSlug: string) {
    // Optimistically close-to-rail FIRST so the UI feels instant — the
    // backend is going to stop the supervisor anyway. On error we show
    // the failure but leave the rail state alone (the user can click
    // the rail entry to re-open and try something else).
    closeAgent(workSlug, agentSlug);
    if (focusedSlug === agentSlug) setFocusedSlug(null);
    try {
      const result = await detachAgent(agentSlug, terminal);
      if (result.launched) {
        showToast("Detached — opened in your terminal.");
      } else {
        // Fallback: copy the command for the user to paste manually.
        const copied = await navigator.clipboard
          ?.writeText(result.command)
          .then(() => true)
          .catch(() => false);
        showToast(
          copied
            ? "Couldn't launch a terminal — resume command copied to your clipboard."
            : `Couldn't launch a terminal. Run: ${result.command}`,
        );
      }
      await refreshAgents();
    } catch (err) {
      const message = err instanceof Error ? err.message : String(err);
      showToast(`Detach failed: ${message}`);
    }
  }

  if (error) {
    return (
      <div className="home">
        <div className="form-error">{error}</div>
        <a href="/" className="hint">← back</a>
      </div>
    );
  }

  if (!work) {
    return <div className="work-loading hint">Loading…</div>;
  }

  const canvasAgents = orderedAgents.filter(
    (a) => !closedSlugs.includes(a.slug),
  );
  const canvasAgentMap = new Map(canvasAgents.map((a) => [a.slug, a]));
  const planningChat = planningChatFrom(chats, work.slug);
  const visibleChats = chats.filter((chat) => chat.slug !== planningChat?.slug);
  const chatSummaryMap = new Map(visibleChats.map((c) => [c.slug, c]));
  const chatSlugs = new Set(visibleChats.map((c) => c.slug));
  const canvasChatSlugs = openChatSlugs.filter((slug) => chatSlugs.has(slug));
  const canvasBaseOrder = [...canvasAgents.map((a) => a.slug), ...canvasChatSlugs];
  const canvasTileIds = resolveCanvasOrder(canvasOrderOverride, canvasBaseOrder);
  const canvasTileCount = canvasTileIds.length;
  function handleDragEnd(event: DragEndEvent) {
    const { active, over } = event;
    if (!over || active.id === over.id) return;
    const fromIdx = canvasTileIds.indexOf(active.id as string);
    const toIdx = canvasTileIds.indexOf(over.id as string);
    if (fromIdx === -1 || toIdx === -1) return;
    const next = [...canvasTileIds];
    const [moved] = next.splice(fromIdx, 1);
    next.splice(toIdx, 0, moved);
    setCanvasOrderOverride(next);

    const visibleAgentOrder = next.filter((slug) => canvasAgentMap.has(slug));
    const visibleAgentSet = new Set(visibleAgentOrder);
    const closedAgentOrder = orderedAgents
      .map((a) => a.slug)
      .filter((slug) => !visibleAgentSet.has(slug));
    useAgentOrderStore
      .getState()
      .setOrder(workSlug, [...visibleAgentOrder, ...closedAgentOrder]);
    setOpenChatSlugs((prev) => {
      const openSet = new Set(prev);
      const orderedOpen = next.filter((slug) => openSet.has(slug));
      const orderedSet = new Set(orderedOpen);
      return [...orderedOpen, ...prev.filter((slug) => !orderedSet.has(slug))];
    });
  }
  const cols =
    canvasTileCount <= 1
      ? 1
      : canvasTileCount === 2
        ? 2
        : canvasTileCount <= 4
          ? 2
          : 3;

  const projectHue = project ? String(project.color) : undefined;
  const projectStyleVars: React.CSSProperties | undefined = projectHue
    ? {
        ["--proj-h" as string]: projectHue,
      }
    : undefined;
  const workShellStyle: React.CSSProperties = {
    ...(projectStyleVars ?? {}),
    ["--shell-left-width" as string]: `${workRailWidth}px`,
  };

  const chatContextFolders = work.chat_context_folders ?? [];
  const sharedFolderCount = chatContextFolders.length + shares.length;
  const selectedContextFolder =
    chatContextFolders.find((f) => f.name === contextDocFolder) ?? null;
  const planningModeActive = workMode === "planning";
  const planningModeReady = planningModeActive && Boolean(plan || planningChat);
  const loopModeActive = workMode === "loop";
  const planInitialRoot =
    agents[0]?.folder ??
    visibleChats.find((chat) => chat.working_directory)?.working_directory ??
    planningChat?.working_directory ??
    null;
  const planningRoot = planRootCleared
    ? null
    : planSelectedRoot ?? plan?.root_path ?? planInitialRoot;
  const planningArtifactRoot =
    planArtifactRootDraft ?? defaultPlanningArtifactRoot(planFramework, work.slug);
  const planningSetupDialog = planningSetupPrompt ? (
    <div
      className="scrim"
      onClick={() => {
        setPlanError(planningSetupPrompt.message);
        setPlanningSetupPrompt(null);
      }}
    >
      <div
        className="modal modal-sm"
        onClick={(event) => event.stopPropagation()}
        role="dialog"
        aria-modal="true"
        aria-labelledby="planning-setup-title"
      >
        <div className="modal-hd">
          <div>
            <h3 id="planning-setup-title">Set up planning framework</h3>
            <div className="sub">
              This work folder needs its selected planning framework initialized.
            </div>
          </div>
          <button
            className="btn icon"
            onClick={() => {
              setPlanError(planningSetupPrompt.message);
              setPlanningSetupPrompt(null);
            }}
            aria-label="Close"
          >
            ×
          </button>
        </div>
        <div className="modal-bd">
          <div className="form-error">{planningSetupPrompt.message}</div>
        </div>
        <div className="modal-ft">
          <button
            className="btn"
            disabled={planLoading}
            onClick={() => {
              setPlanError(planningSetupPrompt.message);
              setPlanningSetupPrompt(null);
            }}
          >
            Cancel
          </button>
          <button
            className="btn"
            disabled={planLoading}
            onClick={() => {
              setPlanningSetupPrompt(null);
              void handleStartPlanningChat(planningSetupPrompt.rootPath).catch(
                () => undefined,
              );
            }}
          >
            Check again
          </button>
          <button
            className="btn primary"
            disabled={planLoading}
            onClick={() => {
              void createPlanningSetupChat(planningSetupPrompt.rootPath);
            }}
          >
            {planLoading ? "Creating…" : "Create setup chat"}
          </button>
        </div>
      </div>
    </div>
  ) : null;

  if (loopModeActive) {
    return (
      <LoopMode
        work={work}
        project={project}
        artifacts={artifacts}
        initialSeed={loopStartSeed}
      />
    );
  }

  if (planningModeReady) {
    return (
      <div className="planning-host" style={projectStyleVars}>
        <PlanningMode
          work={work}
          workAction={
            work.status === "active" ? (
              <button
                className="btn sm"
                disabled={workStatusSaving}
                onClick={() => setCompleteOpen(true)}
              >
                <CheckIcon size={11} /> Mark done
              </button>
            ) : (
              <button className="btn sm" disabled={workStatusSaving} onClick={() => void reopenWork()}>
                {workStatusSaving ? "Reopening..." : "Reopen"}
              </button>
            )
          }
          project={project}
          plan={plan}
          materializationStatus={planMaterializationStatus}
          planningChatSlug={planningChat?.slug ?? null}
          planningChatSummary={planningChat}
          planningChatProjects={allProjects ?? (project ? [project] : [])}
          planningChatWorks={allWorks ?? [work]}
          selectedDetail={planArtifactDetail}
          draft={planDraft}
          error={planError}
          saving={planSaving}
          view={planningView}
          overviewTab={planOverviewTab}
          chatOpen={planChatOpen}
          framework={planFramework}
          onOverviewTab={setPlanOverviewTab}
          onView={openPlanningView}
          onCreateSourcePlan={handleCreateSourcePlanFromPlanningChat}
          onFinishConversation={handleFinishPlanningConversation}
          onDraftChange={setPlanDraft}
          onSave={handleSavePlanArtifact}
          onReset={() => {
            if (planArtifactDetail) setPlanDraft(planArtifactDetail.content);
          }}
          onApprovePlan={handleApprovePlan}
          onCreateBug={handleCreatePlanBug}
          onLaunch={handleLaunchPlanArtifact}
          onStartRun={handleStartPlanRunFromSetup}
          onResolveLoopBlocker={(artifact, runId, agentSlug, retryFailed, resolutionNote, gateDecision, enforcedFindings) =>
            void handleResolvePlanLoopBlocker(
              artifact,
              runId,
              agentSlug,
              retryFailed,
              resolutionNote,
              gateDecision,
              enforcedFindings,
            )
          }
          onApproveRun={(artifact, runId) =>
            handleApprovePlanRun(artifact, runId)
          }
          onCancelRun={(artifact, runId) =>
            handleCancelPlanRun(artifact, runId)
          }
          onRequestRunChanges={(artifact, runId, note) =>
            handleRequestPlanRunChanges(artifact, runId, note)
          }
          onCreateRunPr={handleCreatePlanRunPr}
          onFollowUpRun={handleFollowUpPlanRun}
          onSendRunPrFeedback={handleSendPlanRunPrFeedback}
          onRefreshRunPr={handleRefreshPlanRunPr}
          onChatOpen={setPlanChatOpen}
          onPlanningChatUpdated={patchChatSummary}
        />
        {completeOpen && (
          <CompleteWorkDialog
            work={work}
            onClose={() => setCompleteOpen(false)}
            onCompleted={() => window.location.assign("/")}
          />
        )}
        {work.status === "active" && agentDialogOpen && (
          <NewAgentDialog
            workSlug={work.slug}
            workName={work.name}
            onClose={() => {
              setAgentDialogOpen(false);
              setAgentDialogPrefill(null);
            }}
            onCreate={async (payload) => {
              await handleCreateAgent(payload);
            }}
            forkFromAgent={agentDialogPrefill?.forkFromAgent}
            initialGoal={agentDialogPrefill?.initialGoal}
            initialContexts={agentDialogPrefill?.initialContexts}
          />
        )}
        {planningSetupDialog}
        {toast && (
          <div className="toast" role="status" aria-live="polite">
            {toast}
          </div>
        )}
      </div>
    );
  }

  return (
    <div className="shell-v3 narrow-left work-v3 has-topbar" style={workShellStyle}>
      <ShellTopbar
        crumbs={[
          ...(project
            ? [{ href: `/projects/${project.slug}`, hue: project.color, label: project.name }]
            : []),
          { label: work.slug },
        ]}
        primaryAction={
          work.status === "active" ? (
            <>
              <button className="btn sm" onClick={() => setCompleteOpen(true)}>
                <CheckIcon size={11} /> Mark done
              </button>
              <button className="btn primary sm" onClick={() => setAgentDialogOpen(true)}>
                + New agent <span className="kbd">N</span>
              </button>
            </>
          ) : (
            <button className="btn sm" disabled={workStatusSaving} onClick={() => void reopenWork()}>
              {workStatusSaving ? "Reopening..." : "Reopen"}
            </button>
          )
        }
      />

      {completeOpen && (
        <CompleteWorkDialog
          work={work}
          onClose={() => setCompleteOpen(false)}
          onCompleted={(_count) => {
            // Navigate back to the workspace; the completed work falls out
            // of the default Active filter but is still reachable from the
            // project page or via the Completed pill.
            window.location.assign("/");
          }}
        />
      )}

      {moveOpen && allProjects !== null && (
        <MoveWorkDialog
          work={work}
          projects={allProjects}
          onClose={() => setMoveOpen(false)}
          onMoved={(updated) => {
            setWork(updated);
            // Re-fetch the project for the breadcrumb (or clear it for Loose).
            if (updated.project_slug) {
              getProject(updated.project_slug)
                .then(setProject)
                .catch(() => setProject(null));
              listProjectShares(updated.project_slug)
                .then(setShares)
                .catch(() => setShares([]));
            } else {
              setProject(null);
              setShares([]);
            }
            setMoveOpen(false);
          }}
        />
      )}

      <aside className="shell-left work-rail">
        <div className="work-hero">
          <div className="id-line">
            {work.slug} · {formatAge(work.created_at)}
          </div>
          <div className="name">{work.name}</div>
          {work.description && <div className="desc">{work.description}</div>}
          <div className="pills">
            {work.status === "active" && (
              <button
                className="btn icon sm"
                onClick={openChatComposer}
                title="Discuss this work in a grounded chat"
                aria-label="Discuss this work in a grounded chat"
                data-tip="Chat"
              >
                <ChatIcon size={12} />
              </button>
            )}
            {work.status === "active" && (
              <button
                className="btn icon sm"
                onClick={() => {
                  if (allProjects === null) {
                    listProjects()
                      .then(setAllProjects)
                      .catch(() => setAllProjects([]));
                  }
                  setMoveOpen(true);
                }}
                title="Move this work to a different project"
                aria-label="Move this work to a different project"
                data-tip="Move"
              >
                <MoveIcon size={12} />
              </button>
            )}
          </div>
        </div>

        <div className="v3-rule flush" />

        <div
          className={`scrolly work-rail-sections${
            pullRequests.length > 0 ? " has-pull-requests" : ""
          }`}
        >
          {sharedFolderCount > 0 && (
            <section className="work-rail-section shared-folders-section">
              <div className="v3-shd">
                <span>
                  Shared folders{" "}
                  <span className="num" style={{ marginLeft: 8 }}>
                    {sharedFolderCount}
                  </span>
                </span>
                {project && work.project_slug && (
                  <span className="right">
                    <a href={`/projects/${work.project_slug}`}>manage ↗</a>
                  </span>
                )}
              </div>
              <div className="work-rail-section-body themed-scrollbar">
                {chatContextFolders.map((folder) => (
                  <button
                    key={folder.name}
                    className="v3-agent-row folder-chat"
                    onClick={() => setContextDocFolder(folder.name)}
                    title="View context.md"
                  >
                    <span className="pip"><FolderIcon size={12} /></span>
                    <span className="meta">
                      <span className="name mono">{folder.name}</span>
                      <span className="role">context.md · from {folder.chat_slug}</span>
                    </span>
                    <span className="status"><DocIcon size={11} /></span>
                  </button>
                ))}
                {shares.map((s) => (
                  <V3RailShareRow
                    key={s.slug}
                    share={s}
                    onCopy={(path) => {
                      void navigator.clipboard
                        ?.writeText(path)
                        .then(() => showToast(`Copied ${path}`))
                        .catch(() => showToast(`Path: ${path}`));
                    }}
                  />
                ))}
              </div>
            </section>
          )}

          {(plan || work.status === "active") && (
            <section className="work-rail-section plan-section">
              <div className="v3-shd">
                <span>
                  Work plan{" "}
                  <span className="num" style={{ marginLeft: 8 }}>
                    {plan?.artifacts.length ?? 0}
                  </span>
                </span>
                <span className="right">
                  {plan ? (
                    <button
                      onClick={() => {
                        chooseWorkMode("planning");
                        openPlanningView({ kind: "overview" });
                      }}
                    >
                      overview
                    </button>
                  ) : (
                    <button
                      onClick={() => void openPlanningDialog()}
                      disabled={planningEntryChecking}
                    >
                      {planningEntryChecking ? "checking" : "plan"}
                    </button>
                  )}
                </span>
              </div>
              <div className="work-rail-section-body themed-scrollbar">
                {!plan && (
                  <button
                    type="button"
                    className="v3-plan-start"
                    onClick={() => void openPlanningDialog()}
                    disabled={planningEntryChecking}
                  >
                    <span className="pip"><SparkIcon size={12} /></span>
                    <span className="meta">
                      <span className="name mono">
                        {planningEntryChecking ? "checking workspaces" : "Plan this work"}
                      </span>
                      <span className="role">BMAD lightweight</span>
                    </span>
                  </button>
                )}
                {plan?.artifacts.map((artifact) => (
                  <V3RailPlanArtifactRow
                    key={artifact.id}
                    artifact={artifact}
                    focused={selectedPlanArtifactId === artifact.id}
                    onFocus={() => focusPlanArtifact(artifact.id)}
                  />
                ))}
              </div>
            </section>
          )}

          <section className="work-rail-section agents-section">
            <div className="v3-shd">
              <span>
                Active agents{" "}
                <span className="num" style={{ marginLeft: 8 }}>
                  {orderedAgents.length}
                </span>
              </span>
              {work.status === "active" && (
                <span className="right">
                  <button onClick={() => setAgentDialogOpen(true)}>
                    + new <span className="kbd" style={{ marginLeft: 4 }}>N</span>
                  </button>
                </span>
              )}
            </div>
            <div className="work-rail-section-body themed-scrollbar">
              {orderedAgents.length === 0 && (
                <div className="v3-empty">no agents on the canvas.</div>
              )}
              {orderedAgents.map((a) => (
                <V3RailAgentRow
                  key={a.slug}
                  agent={a}
                  focused={focusedSlug === a.slug}
                  closed={closedSlugs.includes(a.slug)}
                  readOnly={work.status !== "active"}
                  onFocus={() => focusAgent(a.slug)}
                  onDelete={() => setDeleteTarget(a)}
                  onRename={(name) =>
                    setAgents((curr) =>
                      curr.map((x) => (x.slug === a.slug ? { ...x, name } : x)),
                    )
                  }
                />
              ))}
            </div>
          </section>

          {(visibleChats.length > 0 || work.status === "active") && (
            <section className="work-rail-section chats-section">
              <div className="v3-shd">
                <span>
                  Chats <span className="num" style={{ marginLeft: 8 }}>{visibleChats.length}</span>
                </span>
                {work.status === "active" && (
                  <span className="right">
                    <button onClick={openChatComposer}>
                      + new <span className="kbd" style={{ marginLeft: 4 }}>⇧C</span>
                    </button>
                  </span>
                )}
              </div>
              <div className="work-rail-section-body themed-scrollbar">
                {visibleChats.length === 0 && <div className="v3-empty">no chats grounded here yet.</div>}
                {visibleChats.map((c) => (
                  <ChatRow
                    key={c.slug}
                    chat={c}
                    projects={allProjects ?? (project ? [project] : [])}
                    works={allWorks ?? [work]}
                    dense
                    focused={focusedSlug === c.slug}
                    hideGrounding
                    onOpen={() => focusChat(c.slug)}
                    onRenamed={work.status === "active" ? patchChatSummary : undefined}
                    onDelete={work.status === "active" ? setDeleteChatTarget : undefined}
                  />
                ))}
              </div>
            </section>
          )}

          {pullRequests.length > 0 && (
            <section className="work-rail-section pull-requests-section">
              <div className="v3-shd">
                <span>
                  Pull requests{" "}
                  <span className="num" style={{ marginLeft: 8 }}>
                    {artifactSearchActive
                      ? `${filteredArtifacts.length}/${pullRequests.length}`
                      : pullRequests.length}
                  </span>
                </span>
                <span className="right">
                  <button
                    type="button"
                    className={`v3-shd-icon${artifactSearchOpen ? " active" : ""}`}
                    onClick={() => {
                      if (artifactSearchOpen) {
                        setArtifactSearchOpen(false);
                        setArtifactSearchQuery("");
                      } else {
                        setArtifactSearchOpen(true);
                      }
                    }}
                    title={artifactSearchOpen ? "Close pull request search" : "Search pull requests"}
                    aria-label={artifactSearchOpen ? "Close pull request search" : "Search pull requests"}
                    aria-pressed={artifactSearchOpen}
                  >
                    <SearchIcon size={11} />
                  </button>
                </span>
              </div>
              {artifactSearchOpen && (
                <div className="rail-artifact-search">
                  <SearchIcon size={11} />
                  <input
                    ref={artifactSearchInputRef}
                    value={artifactSearchQuery}
                    onChange={(e) => setArtifactSearchQuery(e.target.value)}
                    onKeyDown={(e) => {
                      if (e.key !== "Escape") return;
                      e.preventDefault();
                      if (artifactSearchQuery.trim()) {
                        setArtifactSearchQuery("");
                      } else {
                        setArtifactSearchOpen(false);
                      }
                    }}
                    placeholder="Filter pull requests"
                    aria-label="Filter pull requests"
                  />
                  {artifactSearchQuery.trim() && (
                    <button
                      type="button"
                      className="rail-artifact-search-clear"
                      onClick={() => setArtifactSearchQuery("")}
                      title="Clear artifact filter"
                      aria-label="Clear artifact filter"
                    >
                      ×
                    </button>
                  )}
                </div>
              )}
              <div className="work-rail-section-body themed-scrollbar">
                {filteredArtifacts.length === 0 && (
                  <div className="v3-empty">no matching pull requests.</div>
                )}
                {filteredArtifacts.map((a) => (
                  <V3RailArtifactRow key={a.slug} artifact={a} />
                ))}
              </div>
            </section>
          )}
        </div>

        <div className="v3-footstrip">
          <span className="seg">
            <span className="dot live" />
            {orderedAgents.filter((a) => a.status === "live").length} live
          </span>
          <span className="seg">
            {orderedAgents.filter((a) => a.status === "thinking").length} working
          </span>
          <span style={{ flex: 1 }} />
          <button
            className="btn icon"
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
        <PaneResizeHandle
          defaultValue={280}
          edge="right"
          label="Resize work rail"
          max={WORK_RAIL_MAX}
          min={WORK_RAIL_MIN}
          value={workRailWidth}
          onChange={setWorkRailWidth}
        />
      </aside>

      <main className="shell-right work-right">
        {moveOpen && allProjects !== null && (
          <MoveWorkDialog
            work={work}
            projects={allProjects}
            onClose={() => setMoveOpen(false)}
            onMoved={(updated) => {
              setWork(updated);
              if (updated.project_slug) {
                getProject(updated.project_slug)
                  .then(setProject)
                  .catch(() => setProject(null));
                listProjectShares(updated.project_slug)
                  .then(setShares)
                  .catch(() => setShares([]));
              } else {
                setProject(null);
                setShares([]);
              }
              setMoveOpen(false);
            }}
          />
        )}

          <DndContext sensors={dragSensors} onDragEnd={handleDragEnd}>
            <div className="work-right-canvas tiles" data-cols={cols}>
              {canvasTileCount === 0 && agents.length === 0 && (
                <div className="canvas-empty">
                  <div className="em-title">No tiles on the canvas</div>
                  <div className="em-sub">Launch an agent or open a chat from the rail.</div>
                  {work.status === "active" && !plan && (
                    <button
                      className="btn primary"
                      onClick={() => void openPlanningDialog()}
                      disabled={planningEntryChecking}
                    >
                      <SparkIcon size={12} />{" "}
                      {planningEntryChecking ? "Checking workspaces..." : "Plan this work"}
                    </button>
                  )}
                </div>
              )}
              {agents.length > 0 && canvasAgents.length === 0 && canvasChatSlugs.length === 0 && (
                <div className="canvas-empty">
                  <div className="em-title">All agents closed</div>
                  <div className="em-sub">Click any rail entry to reopen.</div>
                </div>
              )}
              <SortableContext
                items={canvasTileIds}
                strategy={rectSortingStrategy}
              >
                {canvasTileIds.map((slug) => {
                  const agent = canvasAgentMap.get(slug);
                  if (agent) {
                    const a = agent;
                    return (
                      <SortableCanvasCell
                        key={a.slug}
                        itemId={a.slug}
                        persona={a.persona}
                        focused={focusedSlug === a.slug}
                        onFocus={() => setFocusedSlug(a.slug)}
                        registerRef={(el) => {
                          if (el) tileRefs.current.set(a.slug, el);
                          else tileRefs.current.delete(a.slug);
                        }}
                      >
                        <AgentTile
                          agentSlug={a.slug}
                          workSlug={workSlug}
                          mode="tile"
                          persona={a.persona}
                          agentName={a.name}
                          provider={a.provider}
                          model={a.model}
                          readOnly={work.status !== "active"}
                          worktreePath={a.worktree_path}
                          onClose={() => {
                            closeAgent(workSlug, a.slug);
                            if (focusedSlug === a.slug) setFocusedSlug(null);
                          }}
                          onDetach={work.status === "active" ? () => {
                            void handleDetach(a.slug);
                          } : undefined}
                          onHandoff={work.status === "active" ? () => setHandoffSource(a) : undefined}
                          onOpenInIde={() => {
                            window.location.href = editorUrl(editor, a.worktree_path);
                          }}
                          onOpenInConsole={() => {
                            openAgentInConsole(a.slug, terminal)
                              .then(() => {
                                showToast(
                                  `Opened in ${terminal === "system" ? "your terminal" : terminal}`,
                                );
                              })
                              .catch(async (err) => {
                                const copied = await navigator.clipboard
                                  ?.writeText(a.worktree_path)
                                  .then(() => true)
                                  .catch(() => false);
                                const message =
                                  err instanceof Error ? err.message : String(err);
                                showToast(
                                  copied
                                    ? `Couldn't open terminal — path copied to clipboard. (${message})`
                                    : `Couldn't open terminal: ${message}`,
                                );
                              });
                          }}
                          onRevealWorktree={() => {
                            revealAgent(a.slug).catch(() => {
                              navigator.clipboard
                                ?.writeText(a.worktree_path)
                                .catch(() => {});
                            });
                          }}
                          onRevealAtelierDir={() => {
                            revealAgent(a.slug, "atelier").catch((err) => {
                              showToast(
                                `Couldn't open Atelier folder: ${
                                  err instanceof Error ? err.message : String(err)
                                }`,
                              );
                            });
                          }}
                          onRename={work.status === "active" ? (name) =>
                            setAgents((curr) =>
                              curr.map((x) =>
                                x.slug === a.slug ? { ...x, name } : x,
                              ),
                            )
                          : undefined}
                        />
                      </SortableCanvasCell>
                    );
                  }
                  const chatSummary = chatSummaryMap.get(slug);
                  return (
                    <SortableCanvasCell
                      key={slug}
                      itemId={slug}
                      focused={focusedSlug === slug}
                      onFocus={() => setFocusedSlug(slug)}
                      registerRef={(el) => {
                        if (el) tileRefs.current.set(slug, el);
                        else tileRefs.current.delete(slug);
                      }}
                    >
                      <ChatTile
                        chatSlug={slug}
                        chatSummary={chatSummary}
                        projects={allProjects ?? (project ? [project] : [])}
                        works={allWorks ?? [work]}
                        onClose={() => closeChat(slug)}
                        readOnly={work.status !== "active"}
                        onStartAgent={work.status === "active" ? (chat) => handleStartAgentFromChat(chat) : undefined}
                        onUpdated={work.status === "active" ? patchChatSummary : undefined}
                      />
                    </SortableCanvasCell>
                  );
                })}
              </SortableContext>
            </div>
          </DndContext>
      </main>
      {work.status === "active" && agentDialogOpen && (
        <NewAgentDialog
          workSlug={work.slug}
          workName={work.name}
          onClose={() => {
            setAgentDialogOpen(false);
            setAgentDialogPrefill(null);
          }}
          onCreate={async (payload) => {
            await handleCreateAgent(payload);
          }}
          forkFromAgent={agentDialogPrefill?.forkFromAgent}
          initialGoal={agentDialogPrefill?.initialGoal}
          initialContexts={agentDialogPrefill?.initialContexts}
        />
      )}
      {deleteTarget && (
        <DeleteAgentDialog
          agent={deleteTarget}
          onClose={() => setDeleteTarget(null)}
          onDeleted={() => {
            const slug = deleteTarget.slug;
            // Strip from local state immediately so the rail/canvas
            // collapse without waiting for the refetch round-trip.
            setAgents((curr) => curr.filter((x) => x.slug !== slug));
            if (focusedSlug === slug) setFocusedSlug(null);
            // Drop it from the closed-store so a new agent that
            // happens to reuse the slug down the line doesn't inherit
            // the previous "closed" flag (defensive — slugs are int-PK
            // backed so reuse shouldn't happen, but state hygiene is
            // cheap).
            restoreAgent(workSlug, slug);
            setDeleteTarget(null);
            // Re-fetch in the background to pick up any side-effects
            // (e.g. detach markers cleared, agent count on the work
            // header). Errors here are silent — the optimistic strip
            // already gave the user the right local picture.
            void refreshAgents().catch(() => {});
          }}
        />
      )}
      {deleteChatTarget && (
        <DeleteChatDialog
          chat={deleteChatTarget}
          onClose={() => setDeleteChatTarget(null)}
          onDeleted={() => {
            const slug = deleteChatTarget.slug;
            removeChatEverywhere(slug);
            setDeleteChatTarget(null);
          }}
        />
      )}
      {planningDialogOpen && (
        <NewWorkDialog
          work={work}
          projects={project ? [project] : []}
          presetProjectSlug={work.project_slug}
          lockProjectSlug
          initialPlanningSeed={{
            idea: planPromptDraft ?? work.description ?? work.name,
            framework: planFramework,
            profile: planProfile,
            folder: planningRoot ?? "",
            planDir: planningArtifactRoot,
            agentConfig: planAgentConfig,
          }}
          onClose={() => setPlanningDialogOpen(false)}
          onPlan={startPlanningFromDialog}
        />
      )}
      {planningEntryIssue && (
        <PlanningTransitionBlockedDialog
          issue={planningEntryIssue}
          onClose={() => setPlanningEntryIssue(null)}
        />
      )}
      {planningSetupDialog}
      {handoffSource && (
        <HandoffDialog
          workSlug={workSlug}
          source={handoffSource}
          onClose={() => setHandoffSource(null)}
          onHandoffReady={(handoff: HandoffSummary) => {
            setAgentDialogPrefill({
              forkFromAgent: {
                slug: handoffSource.slug,
                name: handoffSource.name,
                folder: handoffSource.folder,
              },
              initialGoal: handoff.doc_text,
            });
            setHandoffSource(null);
            setAgentDialogOpen(true);
          }}
        />
      )}
      {toast && (
        <div className="toast" role="status" aria-live="polite">
          {toast}
        </div>
      )}
      {projectSwitcherOpen && (
        <Switcher
          placeholder="Switch to project…"
          items={projectItems}
          onClose={() => setProjectSwitcherOpen(false)}
          emptyMessage={allProjects === null ? "Loading…" : "No projects yet"}
        />
      )}
      {workSwitcherOpen && (
        <Switcher
          placeholder={
            work.project_slug
              ? `Switch work in ${project?.name ?? work.project_slug}…`
              : "Switch loose work…"
          }
          items={workItems}
          onClose={() => setWorkSwitcherOpen(false)}
          emptyMessage={
            allWorks === null
              ? "Loading…"
              : work.project_slug
                ? "No sibling work in this project"
                : "No other loose work"
          }
        />
      )}
      {searchOpen && allWorks !== null && (
        <SearchModal
          works={allWorks}
          projects={allProjects ?? []}
          defaultScope={
            work.project_slug ? { slug: work.project_slug } : "all"
          }
          onClose={() => setSearchOpen(false)}
        />
      )}
      {chatComposerGrounding !== undefined && (
        <ChatComposer
          projects={allProjects ?? (project ? [project] : [])}
          works={allWorks ?? [work]}
          presetGrounding={chatComposerGrounding}
          hideGrounding
          onClose={() => setChatComposerGrounding(undefined)}
          onStarted={(chat) => {
            setChats((current) => [
              chatSummaryFromDetail(chat),
              ...current.filter((c) => c.slug !== chat.slug),
            ]);
            setChatComposerGrounding(undefined);
            focusChat(chat.slug);
          }}
        />
      )}
      {selectedContextFolder && (
        <ContextDocModal
          workSlug={work.slug}
          folder={selectedContextFolder}
          onClose={() => setContextDocFolder(null)}
        />
      )}
    </div>
  );
}

// Per-type two-letter glyph for the artifact rail row's left badge.
const ARTIFACT_TYPE_LABEL: Record<ArtifactSummary["type"], string> = {
  pr: "PR",
  jira: "JI",
  doc: "DC",
};

function normalizeArtifactSearch(value: string): string {
  return value.toLocaleLowerCase().replace(/[_-]+/g, " ");
}

function artifactMatchesSearch(artifact: ArtifactSummary, query: string): boolean {
  const terms = normalizeArtifactSearch(query)
    .trim()
    .split(/\s+/)
    .filter(Boolean);
  if (terms.length === 0) return true;
  const haystack = normalizeArtifactSearch(
    [
      artifact.title,
      artifact.slug,
      artifact.type,
      ARTIFACT_TYPE_LABEL[artifact.type],
      artifact.status,
      artifact.agent_slug,
      artifact.repo,
      artifact.url,
      artifact.doc_path,
      artifact.location_kind,
    ].join(" "),
  );
  return terms.every((term) => haystack.includes(term));
}

// Status values that resolve to a "good" (green) chip color. PR
// terminal states get their own colors below — ``merged`` is purple
// (matches GitHub's convention; reads as distinct from "done"/Jira
// completion) and ``closed`` is red. Everything else falls through to
// the neutral / info chip via the cascade in styles.css.
const ARTIFACT_GOOD_STATUSES = new Set(["done", "committed"]);

function chipClassFor(status: string): string {
  if (status === "merged") return "chip merged";
  if (status === "closed") return "chip bad";
  if (ARTIFACT_GOOD_STATUSES.has(status)) return "chip good";
  return "chip info";
}

function inferPlanningProfile(text: string): PlanningProfile {
  const lowered = text.toLowerCase();
  if (/(hotfix|production|urgent)/.test(lowered)) return "hotfix";
  if (/(bug|regression|fix)/.test(lowered)) return "bugfix";
  if (/(migration|migrate)/.test(lowered)) return "migration";
  if (/(refactor|cleanup)/.test(lowered)) return "refactor";
  if (/(full app|new app)/.test(lowered)) return "full_app";
  return "feature";
}

function defaultPlanTab(plan: WorkPlan): PlanOverviewTab {
  const hasActiveWork = plan.overview.running > 0 || plan.overview.review > 0;
  const hasRealBlocker = plan.artifacts.some((artifact) =>
    artifact.launch_blockers.some(
      (blocker) => !blocker.toLowerCase().startsWith("accept the latest plan"),
    ),
  );
  return hasActiveWork || hasRealBlocker ? "blocking" : "summary";
}

// ─── v3 rail rows ───────────────────────────────────────────────

function V3RailAgentRow({
  agent,
  focused,
  closed,
  readOnly,
  onFocus,
  onDelete,
  onRename,
}: {
  agent: AgentSummary;
  focused: boolean;
  closed: boolean;
  readOnly: boolean;
  onFocus: () => void;
  onDelete: () => void;
  onRename: (name: string) => void;
}) {
  const [menuOpen, setMenuOpen] = useState(false);
  const [editing, setEditing] = useState(false);
  const [draftName, setDraftName] = useState(agent.name);
  const [renameError, setRenameError] = useState<string | null>(null);
  const [menuPosition, setMenuPosition] = useState<FloatingMenuPosition | null>(
    null,
  );
  const kebabRef = useRef<HTMLButtonElement>(null);
  const menuRef = useRef<HTMLDivElement>(null);
  const inputRef = useRef<HTMLInputElement>(null);

  useEffect(() => {
    if (!menuOpen) return;
    const handler = () => setMenuOpen(false);
    window.addEventListener("click", handler);
    window.addEventListener("resize", handler);
    window.addEventListener("scroll", handler, true);
    return () => {
      window.removeEventListener("click", handler);
      window.removeEventListener("resize", handler);
      window.removeEventListener("scroll", handler, true);
    };
  }, [menuOpen]);

  useEffect(() => {
    if (!menuOpen || !menuRef.current || !kebabRef.current) return;
    const anchor = kebabRef.current.getBoundingClientRect();
    const menu = menuRef.current.getBoundingClientRect();
    setMenuPosition(
      anchoredMenuPosition(anchor, {
        width: menu.width,
        height: menu.height,
      }),
    );
  }, [menuOpen]);

  useEffect(() => {
    if (editing) {
      inputRef.current?.focus();
      inputRef.current?.select();
    }
  }, [editing]);

  function startRename() {
    setDraftName(agent.name);
    setRenameError(null);
    setEditing(true);
  }

  function cancelRename() {
    setEditing(false);
    setDraftName(agent.name);
    setRenameError(null);
  }

  async function commitRename() {
    const next = draftName.trim();
    if (!next || next === agent.name) {
      cancelRename();
      return;
    }
    try {
      const updated = await patchAgent(agent.slug, { name: next });
      onRename(updated.name);
      setEditing(false);
      setRenameError(null);
    } catch (err) {
      setRenameError(err instanceof Error ? err.message : String(err));
    }
  }

  const isDetached = agent.status === "detached";
  const tooltip = isDetached
    ? "Detached to CLI — click to re-attach"
    : closed
      ? "Closed — click to reopen"
      : undefined;

  return (
    <div
      className={"v3-agent-row" + (focused ? " focused" : "")}
      data-persona={agent.persona}
      style={{ position: "relative" }}
    >
      <button
        type="button"
        onClick={editing ? undefined : onFocus}
        title={editing ? undefined : tooltip}
        disabled={editing}
        style={{
          display: "contents",
          background: "transparent",
          border: 0,
          padding: 0,
          textAlign: "left",
          cursor: editing ? "default" : "pointer",
          font: "inherit",
          color: "inherit",
        }}
      >
        <span className="pip">{PERSONA_GLYPH[agent.persona] ?? "AG"}</span>
        <span className="meta">
          {editing ? (
            <input
              ref={inputRef}
              className="rail-agent-name-input mono"
              value={draftName}
              onChange={(e) => setDraftName(e.target.value)}
              onKeyDown={(e) => {
                // Esc here cancels the rename. The global "stop agent"
                // Esc binding sees the event after this handler returns;
                // we stopPropagation so it doesn't fire on a successful
                // cancel (the user's intent was to bail out of the
                // input, not stop the agent's turn).
                if (e.key === "Escape") {
                  e.preventDefault();
                  e.stopPropagation();
                  cancelRename();
                } else if (e.key === "Enter") {
                  e.preventDefault();
                  void commitRename();
                }
              }}
              onBlur={() => void commitRename()}
              onClick={(e) => e.stopPropagation()}
              aria-label="Rename agent"
            />
          ) : (
            <div
              className="name mono"
              onDoubleClick={readOnly ? undefined : (e) => {
                e.stopPropagation();
                startRename();
              }}
              title={readOnly ? undefined : "Double-click to rename"}
            >
              {agent.name}
            </div>
          )}
          <div className="role">
            {isDetached ? "in CLI · click to re-attach" : agent.role}
          </div>
        </span>
        <span className="status">
          <span className={`dot ${agent.status}`} aria-hidden />
        </span>
      </button>
      {renameError && !editing && (
        <div className="rail-agent-rename-err">{renameError}</div>
      )}
      {!readOnly && !editing && (
        <button
          ref={kebabRef}
          type="button"
          className="rail-agent-kebab"
          aria-label={`More actions for ${agent.name}`}
          title="More"
          onClick={(e) => {
            e.stopPropagation();
            if (menuOpen) {
              setMenuOpen(false);
              return;
            }
            setMenuPosition(
              anchoredMenuPosition(e.currentTarget.getBoundingClientRect()),
            );
            setMenuOpen(true);
          }}
        >
          ⋮
        </button>
      )}
      {menuOpen && (
        <div
          ref={menuRef}
          className="rail-agent-menu floating"
          style={menuPosition ?? undefined}
          onClick={(e) => e.stopPropagation()}
        >
          <button
            className="menu-item"
            onClick={() => {
              setMenuOpen(false);
              startRename();
            }}
          >
            Rename
          </button>
          <button
            className="menu-item danger"
            onClick={() => {
              setMenuOpen(false);
              onDelete();
            }}
          >
            Delete agent…
          </button>
        </div>
      )}
    </div>
  );
}

function resolveCanvasOrder(override: string[], current: string[]): string[] {
  if (override.length === 0) return current;
  const currentSet = new Set(current);
  const valid = override.filter((slug) => currentSet.has(slug));
  const validSet = new Set(valid);
  return [...valid, ...current.filter((slug) => !validSet.has(slug))];
}

function planningChatFrom(
  chats: ChatSummary[],
  workSlug: string,
): ChatSummary | null {
  return (
    chats.find(
      (chat) =>
        chat.title.trim().toLowerCase() === "planning" &&
        chat.grounding?.kind === "work" &&
        chat.grounding.ref === workSlug,
    ) ?? null
  );
}

function planningFrameworkFromChat(
  chat: ChatSummary | null,
): PlanningFrameworkId | null {
  const config = chat?.options?.planning_config;
  if (!config || typeof config !== "object") return null;
  const framework = (config as Record<string, unknown>).framework;
  return framework === "bmad" ||
    framework === "spec" ||
    framework === "openspec" ||
    framework === "custom"
    ? framework
    : null;
}

function normalizePlanningChatSummary(
  chat: ChatSummary,
  workSlug: string,
): ChatSummary {
  if (
    chat.grounding?.kind === "work" &&
    chat.grounding.ref === workSlug &&
    chat.title.trim().toLowerCase() === "planning"
  ) {
    return { ...chat, title: "Planning" };
  }
  return chat;
}

async function resolvePlanningAgentConfig(
  selected: PlanningAgentConfig | null,
): Promise<{
  provider: ProviderDescriptor;
  model: string;
  options: Record<string, string>;
}> {
  const descriptors = await getProviderDescriptors();
  const provider =
    descriptors.find((descriptor) => descriptor.name === selected?.provider) ??
    descriptors.find((descriptor) => descriptor.name === "claude-acp") ??
    descriptors[0];
  if (!provider) throw new Error("No chat provider is configured.");
  const model =
    selected?.provider === provider.name &&
    provider.primary_field.values.includes(selected.model)
      ? selected.model
      : provider.primary_field.default;
  const options =
    selected?.provider === provider.name
      ? coerceProviderOptionsForModel(provider, model, selected.options)
      : providerDefaults(provider, model);
  return { provider, model, options };
}

function V3RailShareRow({
  share,
  onCopy,
}: {
  share: SharedFolderSummary;
  onCopy: (path: string) => void;
}) {
  const realPath = share.is_custom_location
    ? share.real_path ?? share.canonical_path
    : share.canonical_path;
  return (
    <button
      type="button"
      className="v3-folder-row compact"
      title={`Click to copy ${realPath}`}
      onClick={() => onCopy(realPath)}
    >
      <span className="ico">
        <FolderIcon size={12} />
      </span>
      <span className="body">
        <span className="lbl">
          <span>{share.name}</span>
          {share.is_custom_location && <span className="tag">custom</span>}
        </span>
        <span className="path">./{share.mount_path}/</span>
      </span>
    </button>
  );
}

const PLAN_KIND_LABEL: Record<PlanArtifact["kind"], string> = {
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
};

function PlanningTransitionBlockedDialog({
  issue,
  onClose,
}: {
  issue: { message: string; workspaces: CompletionWorkspace[] };
  onClose: () => void;
}) {
  return (
    <div className="scrim" onClick={onClose}>
      <div
        className="modal modal-lg complete-work-modal"
        role="alertdialog"
        aria-modal="true"
        aria-labelledby="planning-transition-blocked-title"
        onClick={(event) => event.stopPropagation()}
      >
        <div className="modal-hd">
          <div>
            <h3 id="planning-transition-blocked-title">Planning mode is blocked</h3>
            <p className="sub">Current agent work must be preserved first</p>
          </div>
          <button className="btn icon" onClick={onClose} aria-label="Close">
            ×
          </button>
        </div>
        <div className="modal-bd">
          <p className="complete-work-copy">{issue.message}</p>
          {issue.workspaces.length > 0 && (
            <section className="complete-work-workspaces" aria-label="Protected workspaces">
              {issue.workspaces.map((workspace) => (
                <article
                  className="complete-work-workspace"
                  key={`${workspace.owner}:${workspace.path}`}
                >
                  <header>
                    <strong>{workspace.owner}</strong>
                    <span className="protected">
                      {workspace.error
                        ? "Inspection failed"
                        : workspace.is_git_repo
                          ? "Changes present"
                          : "Not a Git worktree"}
                    </span>
                  </header>
                  <code title={workspace.path}>{workspace.path}</code>
                  {(workspace.changed_files.length > 0 ||
                    workspace.untracked_files.length > 0) && (
                    <ul>
                      {workspace.changed_files.map((file) => (
                        <li key={`changed:${file}`}>
                          {file} <em>changed</em>
                        </li>
                      ))}
                      {workspace.untracked_files.map((file) => (
                        <li key={`untracked:${file}`}>
                          {file} <em>untracked</em>
                        </li>
                      ))}
                    </ul>
                  )}
                  {workspace.error && <p>{workspace.error}</p>}
                </article>
              ))}
            </section>
          )}
        </div>
        <div className="modal-ft">
          <span className="spacer" />
          <button className="btn primary" onClick={onClose}>
            Keep current mode
          </button>
        </div>
      </div>
    </div>
  );
}

function V3RailPlanArtifactRow({
  artifact,
  focused,
  onFocus,
}: {
  artifact: PlanArtifact;
  focused: boolean;
  onFocus: () => void;
}) {
  return (
    <button
      type="button"
      className={"v3-plan-row" + (focused ? " focused" : "")}
      onClick={onFocus}
      title={artifact.source_ref}
    >
      <span className="ico">{PLAN_KIND_LABEL[artifact.kind]}</span>
      <span className="meta">
        <span className="title">{artifact.title}</span>
        <span className="sub">
          <span>{artifact.path}</span>
          <span className={planChipClass(artifact.status)}>{artifact.status}</span>
        </span>
      </span>
    </button>
  );
}

function planChipClass(status: PlanArtifact["status"]): string {
  if (status === "changed") return "chip bad";
  if (status === "approved" || status === "accepted") return "chip good";
  return "chip info";
}

function V3RailArtifactRow({ artifact }: { artifact: ArtifactSummary }) {
  const isClickable =
    (artifact.type === "doc" && artifact.doc_path) ||
    (artifact.type !== "doc" && artifact.url);
  const handleClick = () => {
    if (artifact.type === "doc") {
      if (artifact.doc_path) {
        void revealArtifact(artifact.slug).catch(() => {});
      }
      return;
    }
    if (artifact.url) {
      window.open(artifact.url, "_blank", "noopener,noreferrer");
    }
  };
  const subtitle = artifact.agent_slug ?? (artifact.repo ?? artifact.url ?? "");
  return (
    <button
      type="button"
      className="v3-artifact-row"
      title={artifact.title}
      onClick={isClickable ? handleClick : undefined}
    >
      <span className="ico">{ARTIFACT_TYPE_LABEL[artifact.type] ?? "AR"}</span>
      <span className="meta">
        <div className="title">{artifact.title}</div>
        <div className="sub">
          <span>{artifact.slug}</span>
          {subtitle ? <span>· {subtitle}</span> : null}
          <span className={chipClassFor(artifact.status)}>
            {artifact.status}
          </span>
        </div>
      </span>
    </button>
  );
}

// Compact relative-time formatter for the work hero id-line.
function formatAge(iso: string): string {
  const ms = Date.now() - new Date(iso).getTime();
  if (ms < 0) return "now";
  const s = Math.floor(ms / 1000);
  if (s < 60) return `${s}s ago`;
  const m = Math.floor(s / 60);
  if (m < 60) return `${m}m ago`;
  const h = Math.floor(m / 60);
  if (h < 24) return `${h}h ago`;
  const d = Math.floor(h / 24);
  if (d < 7) return `${d}d ago`;
  const w = Math.floor(d / 7);
  if (w < 5) return `${w}w ago`;
  const mo = Math.floor(d / 30);
  return `${mo}mo ago`;
}

function readLoopStartSeed(workSlug: string): LoopStartSeed | null {
  const raw = sessionStorage.getItem(loopStartStorageKey(workSlug));
  if (!raw) return null;
  try {
    const seed = JSON.parse(raw) as Partial<LoopStartSeed>;
    if (typeof seed.folder !== "string" || typeof seed.goal !== "string") return null;
    return {
      folder: seed.folder,
      goal: seed.goal,
      definitionId:
        typeof seed.definitionId === "string" ? seed.definitionId : undefined,
      createDefinition:
        typeof seed.createDefinition === "boolean"
          ? seed.createDefinition
          : undefined,
    };
  } catch {
    return null;
  }
}
