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
  type CreateAgentPayload,
  type HandoffSummary,
  type ProjectSummary,
  type SharedFolderSummary,
  type WorkDetail,
  type WorkSummary,
  PERSONA_GLYPH,
  createAgent,
  detachAgent,
  getProject,
  getWork,
  listAgents,
  listArtifacts,
  refreshPrStatuses,
  listProjectShares,
  listProjects,
  listWorks,
  openAgentInConsole,
  revealAgent,
  revealArtifact,
  revealWork,
} from "./api";
import { BrandMark } from "./BrandMark";
import { CompleteWorkDialog } from "./CompleteWorkDialog";
import { DeleteAgentDialog } from "./DeleteAgentDialog";
import { HandoffDialog } from "./HandoffDialog";
import {
  CheckIcon,
  FolderIcon,
  SearchIcon,
  SlidersIcon,
} from "./Icons";
import { MoveWorkDialog } from "./MoveWorkDialog";
import { NewAgentDialog } from "./NewAgentDialog";
import { SearchModal } from "./SearchModal";
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
import { editorUrl, useTweaksStore } from "./state/tweaks";
import { ThemeToggle } from "./ThemeToggle";

// Stable singleton so the selector below doesn't return a fresh ref on
// every render — Zustand's default Object.is snapshot check would
// otherwise treat each `[]` as a change and re-render in a loop.
const NO_CLOSED: readonly string[] = [];

export function WorkView({ workSlug }: { workSlug: string }) {
  const [work, setWork] = useState<WorkDetail | null>(null);
  const [project, setProject] = useState<ProjectSummary | null>(null);
  const [agents, setAgents] = useState<AgentSummary[]>([]);
  const [artifacts, setArtifacts] = useState<ArtifactSummary[]>([]);
  const [shares, setShares] = useState<SharedFolderSummary[]>([]);
  const [error, setError] = useState<string | null>(null);
  const [focusedSlug, setFocusedSlug] = useState<string | null>(null);
  const [agentDialogOpen, setAgentDialogOpen] = useState(false);
  // When the new-agent dialog is opened from the handoff flow, we
  // pre-fill it with the source agent's slug + folder + the freshly
  // generated handoff doc as initial goal. Null in the regular flow.
  const [agentDialogPrefill, setAgentDialogPrefill] = useState<{
    forkFromAgent: { slug: string; name: string; folder: string };
    initialGoal: string;
  } | null>(null);
  const [handoffSource, setHandoffSource] = useState<AgentSummary | null>(null);
  const [deleteTarget, setDeleteTarget] = useState<AgentSummary | null>(null);
  const [completeOpen, setCompleteOpen] = useState(false);
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
  const artifactsRevision = useArtifactsRefresh((s) =>
    selectWorkRevision(s, workSlug),
  );
  // Transient banner for the detach flow — fades out after 4s. Used both
  // for "launched in Terminal" success and "couldn't launch — command
  // copied to clipboard" fallback. One slot is plenty: detaches happen
  // one at a time and overlapping toasts would just compete for room.
  const [toast, setToast] = useState<string | null>(null);
  const tileRefs = useRef<Map<string, HTMLDivElement>>(new Map());
  const closedSlugs = useClosedStore((s) => s.byWork[workSlug] ?? NO_CLOSED);
  const closeAgent = useClosedStore((s) => s.close);
  const restoreAgent = useClosedStore((s) => s.restore);
  const agentOrderOverride = useAgentOrderStore(
    (s) => s.byWork[workSlug],
  );
  const editor = useTweaksStore((s) => s.editor);
  const terminal = useTweaksStore((s) => s.terminal);

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

  // Shortcuts:
  //   N       → new agent
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
        e.preventDefault();
        setAgentDialogOpen(true);
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
    searchOpen,
    allProjects,
    allWorks,
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
    Promise.all([getWork(workSlug), listAgents(workSlug)])
      .then(([w, a]) => {
        if (cancelled) return;
        setWork(w);
        setAgents(a);
        if (a.length > 0) setFocusedSlug(a[0].slug);
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
    if (closedSlugs.includes(slug)) {
      restoreAgent(workSlug, slug);
    }
    setFocusedSlug(slug);
    requestAnimationFrame(() => {
      const el = tileRefs.current.get(slug);
      if (el) el.scrollIntoView({ behavior: "smooth", block: "start" });
    });
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
      const result = await detachAgent(agentSlug);
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
  function handleDragEnd(event: DragEndEvent) {
    const { active, over } = event;
    if (!over || active.id === over.id) return;
    // canvasAgents is what the user sees; closed agents stay in their
    // override slot but aren't on the canvas. Persist the FULL ordered
    // set (canvas + closed) so re-opening a closed tile restores its
    // prior slot rather than dumping it at the end.
    const fullOrder = orderedAgents.map((a) => a.slug);
    const fromIdx = fullOrder.indexOf(active.id as string);
    const toIdx = fullOrder.indexOf(over.id as string);
    if (fromIdx === -1 || toIdx === -1) return;
    const next = [...fullOrder];
    const [moved] = next.splice(fromIdx, 1);
    next.splice(toIdx, 0, moved);
    useAgentOrderStore.getState().setOrder(workSlug, next);
  }
  const cols =
    canvasAgents.length <= 1
      ? 1
      : canvasAgents.length === 2
        ? 2
        : canvasAgents.length <= 4
          ? 2
          : 3;

  const projectHue = project ? String(project.color) : undefined;
  const projectStyleVars: React.CSSProperties | undefined = projectHue
    ? {
        ["--proj-h" as string]: projectHue,
        ["--proj-color" as string]: `oklch(0.62 0.16 ${projectHue})`,
        ["--proj-soft" as string]: `oklch(0.62 0.16 ${projectHue} / 0.10)`,
      }
    : undefined;

  return (
    <div className="shell-v3 narrow-left work-v3" style={projectStyleVars}>

      {completeOpen && (
        <CompleteWorkDialog
          work={work}
          agentCount={agents.length}
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
        <div className="crown">
          <a className="wordmark" href="/" title="Back to workspace">
            <span className="wm-mark" aria-hidden>
              <BrandMark />
            </span>
            <span className="wm-rest">telier</span>
          </a>
          <div className="crown-actions">
            <button
              className="btn-icon"
              onClick={openSearch}
              title="Search (⇧F)"
              aria-label="Search"
            >
              <SearchIcon size={12} />
            </button>
            <a
              className="btn-icon"
              href="/settings"
              title="Settings (⌘,)"
              aria-label="Settings"
            >
              <SlidersIcon size={12} />
            </a>
            <ThemeToggle className="btn-icon" />
          </div>
        </div>

        <div className="crumbs-v3">
          <a className="crumb" href="/">
            ← workspace
          </a>
          {work.project_slug && (
            <>
              <span className="sep">/</span>
              <a className="crumb" href={`/projects/${work.project_slug}`}>
                {project?.name ?? work.project_slug}
              </a>
            </>
          )}
          <span className="sep">/</span>
          <span className="now">{work.slug}</span>
        </div>

        <div className="work-hero">
          <div className="id-line">
            {work.slug} · {formatAge(work.created_at)}
          </div>
          <div className="name">{work.name}</div>
          {work.description && <div className="desc">{work.description}</div>}
          <div className="pills">
            {work.status === "active" ? (
              <button
                className="btn"
                onClick={() => setCompleteOpen(true)}
                title="Mark this work as complete (stops agents, removes worktrees, keeps transcripts)"
              >
                <CheckIcon size={11} /> Mark done
              </button>
            ) : (
              <span className="chip chip-completed" title="This work is completed">
                completed
              </span>
            )}
            {work.status === "active" && (
              <button
                className="btn"
                onClick={() => {
                  if (allProjects === null) {
                    listProjects()
                      .then(setAllProjects)
                      .catch(() => setAllProjects([]));
                  }
                  setMoveOpen(true);
                }}
                title="Move this work to a different project"
              >
                Move…
              </button>
            )}
          </div>
        </div>

        <div className="v3-rule flush" />

        <div className="scrolly">
          <div className="v3-shd">
            <span>
              Active agents{" "}
              <span className="num" style={{ marginLeft: 8 }}>
                {orderedAgents.length}
              </span>
            </span>
            <span className="right">
              <button onClick={() => setAgentDialogOpen(true)}>
                + new <span className="kbd" style={{ marginLeft: 4 }}>N</span>
              </button>
            </span>
          </div>
          {orderedAgents.length === 0 && (
            <div className="v3-empty">no agents on the canvas.</div>
          )}
          {orderedAgents.map((a) => (
            <V3RailAgentRow
              key={a.slug}
              agent={a}
              focused={focusedSlug === a.slug}
              closed={closedSlugs.includes(a.slug)}
              onFocus={() => focusAgent(a.slug)}
              onDelete={() => setDeleteTarget(a)}
            />
          ))}

          {work.project_slug && (
            <>
              <div style={{ height: 14 }} />
              <div className="v3-shd">
                <span>
                  Shared folders{" "}
                  <span className="num" style={{ marginLeft: 8 }}>
                    {shares.length}
                  </span>
                </span>
                {project && (
                  <span className="right">
                    <a href={`/projects/${work.project_slug}`}>manage ↗</a>
                  </span>
                )}
              </div>
              {shares.length === 0 && (
                <div className="v3-empty">none in this project.</div>
              )}
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
            </>
          )}

          {artifacts.length > 0 && (
            <>
              <div style={{ height: 14 }} />
              <div className="v3-shd">
                <span>
                  Artifacts{" "}
                  <span className="num" style={{ marginLeft: 8 }}>
                    {artifacts.length}
                  </span>
                </span>
              </div>
              {artifacts.map((a) => (
                <V3RailArtifactRow key={a.slug} artifact={a} />
              ))}
            </>
          )}
          <div style={{ height: 24 }} />
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
            className="btn-icon"
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
      </aside>

      <main className="shell-right work-right">
        <div className="work-right-hd">
          <div className="ttl">
            <span className="t">{work.name}</span>
            <span className="d">
              {canvasAgents.length} agent
              {canvasAgents.length === 1 ? "" : "s"} on canvas
            </span>
          </div>
          <div className="spacer" />
          <button
            className="btn primary"
            onClick={() => setAgentDialogOpen(true)}
          >
            + New agent <span className="kbd" style={{ marginLeft: 4 }}>N</span>
          </button>
        </div>

        {completeOpen && (
          <CompleteWorkDialog
            work={work}
            agentCount={agents.length}
            onClose={() => setCompleteOpen(false)}
            onCompleted={(_count) => {
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
              {agents.length === 0 && (
                <div className="canvas-empty">
                  <div className="em-title">No agents on the canvas</div>
                  <div className="em-sub">Launch one with the “+ New agent” button above.</div>
                </div>
              )}
              {agents.length > 0 && canvasAgents.length === 0 && (
                <div className="canvas-empty">
                  <div className="em-title">All agents closed</div>
                  <div className="em-sub">Click any rail entry to reopen.</div>
                </div>
              )}
              <SortableContext
                items={canvasAgents.map((a) => a.slug)}
                strategy={rectSortingStrategy}
              >
                {canvasAgents.map((a) => (
                  <SortableCanvasCell
                    key={a.slug}
                    agentSlug={a.slug}
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
                      worktreePath={a.worktree_path}
                      onClose={() => {
                        closeAgent(workSlug, a.slug);
                        if (focusedSlug === a.slug) setFocusedSlug(null);
                      }}
                      onDetach={() => {
                        void handleDetach(a.slug);
                      }}
                      onHandoff={() => setHandoffSource(a)}
                      onOpenInIde={() => {
                        // The configured editor (Tweaks panel) maps to
                        // its OS-registered URL scheme. Browsers route
                        // unknown protocols to the OS handler without
                        // navigating, so the page stays put.
                        window.location.href = editorUrl(editor, a.worktree_path);
                      }}
                      onOpenInConsole={() => {
                        // Backend shells out to the platform terminal.
                        // Surface the actual outcome — earlier the
                        // failure path was silent (just copied the
                        // path to clipboard), so a misconfigured
                        // terminal preference looked like "the button
                        // does nothing".
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
                        // Best-effort reveal — same fallback shape as the
                        // work-level pill: copy the path on backend
                        // failure so the user can paste it into a terminal.
                        revealAgent(a.slug).catch(() => {
                          navigator.clipboard
                            ?.writeText(a.worktree_path)
                            .catch(() => {});
                        });
                      }}
                    />
                  </SortableCanvasCell>
                ))}
              </SortableContext>
            </div>
          </DndContext>
      </main>
      {agentDialogOpen && (
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
    </div>
  );
}

// Per-type two-letter glyph for the rail row's left badge. Mirrors the
// design prototype's ``ArtifactRow`` (PR / JI / DC).
const ARTIFACT_TYPE_LABEL: Record<ArtifactSummary["type"], string> = {
  pr: "PR",
  jira: "JI",
  doc: "DC",
};

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

// ─── v3 rail rows ───────────────────────────────────────────────

function V3RailAgentRow({
  agent,
  focused,
  closed,
  onFocus,
  onDelete,
}: {
  agent: AgentSummary;
  focused: boolean;
  closed: boolean;
  onFocus: () => void;
  onDelete: () => void;
}) {
  const [menuOpen, setMenuOpen] = useState(false);
  useEffect(() => {
    if (!menuOpen) return;
    const handler = () => setMenuOpen(false);
    window.addEventListener("click", handler);
    return () => window.removeEventListener("click", handler);
  }, [menuOpen]);

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
        onClick={onFocus}
        title={tooltip}
        style={{
          display: "contents",
          background: "transparent",
          border: 0,
          padding: 0,
          textAlign: "left",
          cursor: "pointer",
          font: "inherit",
          color: "inherit",
        }}
      >
        <span className="pip">{PERSONA_GLYPH[agent.persona] ?? "AG"}</span>
        <span className="meta">
          <div className="name mono">{agent.name}</div>
          <div className="role">
            {isDetached ? "in CLI · click to re-attach" : agent.role}
          </div>
        </span>
        <span className="status">
          <span className={`dot ${agent.status}`} aria-hidden />
        </span>
      </button>
      <button
        type="button"
        aria-label={`More actions for ${agent.name}`}
        title="More"
        onClick={(e) => {
          e.stopPropagation();
          setMenuOpen((v) => !v);
        }}
        style={{
          position: "absolute",
          right: 6,
          top: "50%",
          transform: "translateY(-50%)",
          background: "transparent",
          border: 0,
          color: "var(--fg-4)",
          cursor: "pointer",
          padding: "4px 6px",
          fontSize: 14,
          lineHeight: 1,
        }}
      >
        ⋮
      </button>
      {menuOpen && (
        <div
          className="rail-agent-menu"
          onClick={(e) => e.stopPropagation()}
        >
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

export function shortenPath(p: string): string {
  if (!p) return "";
  // Substitute ``$HOME/...`` → ``~/...`` for display when we can detect
  // home from the path. Backend sends absolute paths; FE has no env var
  // access, so we just probe the common ``/Users/{user}`` and
  // ``/home/{user}`` prefixes plus check whether ``Atelier`` is the
  // user's home — covers macOS + Linux, the platforms we ship today.
  const homeMatch = p.match(/^(\/Users\/[^/]+|\/home\/[^/]+)\/(.*)$/);
  const display = homeMatch ? `~/${homeMatch[2]}` : p;
  const parts = display.split("/");
  if (parts.length <= 3) return display;
  return [parts[0], "…", ...parts.slice(-2)].join("/");
}
