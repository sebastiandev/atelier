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
  listProjectShares,
  listProjects,
  listWorks,
  openAgentInConsole,
  revealAgent,
  revealArtifact,
  revealWork,
} from "./api";
import { CompleteWorkDialog } from "./CompleteWorkDialog";
import { DeleteAgentDialog } from "./DeleteAgentDialog";
import { HandoffDialog } from "./HandoffDialog";
import { MoveWorkDialog } from "./MoveWorkDialog";
import { NewAgentDialog } from "./NewAgentDialog";
import { SortableCanvasCell } from "./SortableCanvasCell";
import { Switcher, SwitcherChevron, type SwitcherItem } from "./Switcher";
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
import { TweaksToggle } from "./TweaksPanel";

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
  const layout = useTweaksStore((s) => s.layout);
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
        workSwitcherOpen
      )
        return;
      if (e.metaKey || e.ctrlKey || e.altKey) return;
      const target = e.target as HTMLElement | null;
      const tag = target?.tagName;
      if (tag === "INPUT" || tag === "TEXTAREA" || target?.isContentEditable)
        return;
      if (e.shiftKey && (e.key === "W" || e.key === "w")) {
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
        if (!cancelled) setArtifacts(rows);
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

  return (
    <div className="work-view">
      <header className="topbar wv-topbar">
        <a className="brand brand-link" href="/">
          <span className="brand-mark" /> Atelier
        </a>
        <a className="btn-ghost-sm" href="/">
          ← Workspace
        </a>
        <span className="crumbs">
          {work.project_slug && (
            <>
              <span className="sep">/</span>
              <a
                className="crumb-link"
                href={`/projects/${work.project_slug}`}
                style={
                  project
                    ? { ["--proj-h" as string]: String(project.color) }
                    : undefined
                }
              >
                {project ? (
                  <span className="filter-pill-glyph" aria-hidden="true">
                    {project.glyph}
                  </span>
                ) : null}
                {project?.name ?? work.project_slug}
              </a>
              <SwitcherChevron
                onClick={openProjectSwitcher}
                title="Switch project (Shift+P)"
              />
            </>
          )}
          <span className="sep">/</span>
          <span className="now mono">{work.slug}</span>
          <SwitcherChevron
            onClick={openWorkSwitcher}
            title="Switch work (Shift+W)"
          />
        </span>
        <span className="hint" style={{ marginLeft: "0.5rem" }}>
          {work.name}
        </span>
        <div className="spacer" />
        {work.status === "active" && (
          <>
            <button
              className="btn-ghost-sm"
              type="button"
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
            <button
              className="btn-ghost-sm"
              type="button"
              onClick={() => setCompleteOpen(true)}
              title="Mark this work as complete (stops agents, removes worktrees, keeps transcripts)"
            >
              ✓ Complete work
            </button>
          </>
        )}
        {work.status === "completed" && (
          <span className="chip chip-completed" title="This work is completed">
            completed
          </span>
        )}
        <button
          className="folder-pill mono"
          type="button"
          title={`Open ${work.atelier_path} in the file browser`}
          onClick={() => {
            // Best-effort reveal: the backend tries the platform's file
            // browser. If it fails (e.g. headless Linux without xdg-open),
            // we fall back to copying the path to the clipboard so the
            // user still has it. Either way, no UI for the failure path
            // — this is a convenience, not a load-bearing action.
            revealWork(work.slug).catch(() => {
              navigator.clipboard?.writeText(work.atelier_path).catch(() => {});
            });
          }}
        >
          {shortenPath(work.atelier_path)}
        </button>
        <TweaksToggle />
        <ThemeToggle />
      </header>

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

      <div className="work-body">
        <aside className="left-rail">
          <RailSection title="Active agents" count={orderedAgents.length}>
            {orderedAgents.length === 0 && (
              <div className="hint" style={{ padding: "4px 8px" }}>
                None on the canvas. Launch one via the API.
              </div>
            )}
            {orderedAgents.map((a) => (
              <RailAgentRow
                key={a.slug}
                agent={a}
                focused={focusedSlug === a.slug}
                closed={closedSlugs.includes(a.slug)}
                onFocus={() => focusAgent(a.slug)}
                onDelete={() => setDeleteTarget(a)}
              />
            ))}
          </RailSection>

          {work.project_slug && (
            <RailSection
              title="Shared folders"
              count={shares.length}
              hint={
                project
                  ? `Project ${project.name} — manage on project page`
                  : undefined
              }
              hintHref={`/projects/${work.project_slug}`}
            >
              {shares.length === 0 && (
                <div className="hint" style={{ padding: "4px 8px" }}>
                  No shared folders for this project yet.
                </div>
              )}
              {shares.map((s) => (
                <ShareRailRow
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
            </RailSection>
          )}

          <RailSection title="Artifacts" count={artifacts.length}>
            {artifacts.length === 0 && (
              <div className="hint" style={{ padding: "4px 8px" }}>
                None tracked yet. Agents will report PRs and tickets here.
              </div>
            )}
            {artifacts.map((a) => (
              <ArtifactRow key={a.slug} artifact={a} />
            ))}
          </RailSection>
        </aside>

        <div className="canvas-wrap">
          <div className="canvas-hd">
            <div>
              <div className="canvas-title">{work.name}</div>
              <div className="canvas-desc">{work.description}</div>
            </div>
            <div className="spacer" />
            <button className="btn primary" onClick={() => setAgentDialogOpen(true)}>
              + New agent <span className="kbd">N</span>
            </button>
          </div>

          <DndContext sensors={dragSensors} onDragEnd={handleDragEnd}>
            <div className="canvas" data-cols={cols} data-layout={layout}>
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
                        // Backend shells out to the platform terminal
                        // — same fallback shape as reveal: copy the
                        // path so the user can paste it manually if
                        // the launch fails.
                        openAgentInConsole(a.slug, terminal).catch(() => {
                          navigator.clipboard
                            ?.writeText(a.worktree_path)
                            .catch(() => {});
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
        </div>
      </div>
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

// Status values that resolve to a "good" chip color (everything else
// renders as the neutral / info chip via the cascade in styles.css).
// "committed" is the doc terminal state, "merged" and "done" are the
// PR / Jira ones.
const ARTIFACT_GOOD_STATUSES = new Set(["merged", "done", "committed"]);

function ArtifactRow({ artifact }: { artifact: ArtifactSummary }) {
  const isClickable =
    (artifact.type === "doc" && artifact.doc_path) ||
    (artifact.type !== "doc" && artifact.url);
  const handleClick = () => {
    if (artifact.type === "doc") {
      if (artifact.doc_path) {
        // Fire and forget — backend logs failure server-side; the row
        // stays visible either way. If we ever get a "reveal failed"
        // toast slot, surface it here.
        void revealArtifact(artifact.slug).catch(() => {});
      }
      return;
    }
    if (artifact.url) {
      window.open(artifact.url, "_blank", "noopener,noreferrer");
    }
  };
  const subtitle =
    artifact.agent_slug ?? (artifact.repo ?? artifact.url ?? "");
  return (
    <button
      type="button"
      className="rail-arti"
      title={artifact.title}
      onClick={isClickable ? handleClick : undefined}
      data-clickable={isClickable ? "true" : undefined}
    >
      <div className="arti-ico" data-type={artifact.type}>
        {ARTIFACT_TYPE_LABEL[artifact.type] ?? "AR"}
      </div>
      <div className="rail-arti-meta">
        <div className="rail-arti-title">{artifact.title}</div>
        <div className="rail-arti-id mono">
          {artifact.slug}
          {subtitle ? ` · ${subtitle}` : ""}
        </div>
      </div>
      <ArtifactStatusChips artifact={artifact} />
    </button>
  );
}

function ArtifactStatusChips({ artifact }: { artifact: ArtifactSummary }) {
  // One pill per artifact. The backend's derivation already collapses
  // location + git into ``status`` for docs (draft = shared, pending /
  // committed = worktree), so a second location chip would just be
  // noise in the rail's narrow column.
  const statusClass = ARTIFACT_GOOD_STATUSES.has(artifact.status)
    ? "chip good"
    : "chip info";
  return <span className={statusClass}>{artifact.status}</span>;
}

function RailSection({
  title,
  count,
  children,
  hint,
  hintHref,
}: {
  title: string;
  count: number;
  children: React.ReactNode;
  hint?: string;
  hintHref?: string;
}) {
  return (
    <div className="rail-section">
      <div className="rail-hd">
        <span>{title}</span>
        <span className="count mono">{count}</span>
        {hint && hintHref && (
          <a
            className="rail-hd-link"
            href={hintHref}
            title={hint}
            aria-label={hint}
          >
            ↗
          </a>
        )}
      </div>
      <div className="rail-section-body">{children}</div>
    </div>
  );
}

function RailAgentRow({
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

  // Outside-click closes the popover. Cheap event listener — no portal
  // needed since the menu is positioned relative to the row.
  useEffect(() => {
    if (!menuOpen) return;
    const handler = () => setMenuOpen(false);
    window.addEventListener("click", handler);
    return () => window.removeEventListener("click", handler);
  }, [menuOpen]);

  const isDetached = agent.status === "detached";
  const tooltip = isDetached
    ? "Detached to CLI — click to re-attach (Atelier merges any CLI activity into the transcript)"
    : closed
      ? "Closed — click to reopen"
      : undefined;

  return (
    <div
      className={
        "rail-agent-row" +
        (focused ? " focused" : "") +
        (closed ? " minimized" : "") +
        (isDetached ? " detached" : "")
      }
      data-persona={agent.persona}
    >
      <button
        type="button"
        className="rail-agent"
        onClick={onFocus}
        title={tooltip}
      >
        <span className="pip">{PERSONA_GLYPH[agent.persona] ?? "AG"}</span>
        <span className="meta">
          <span className="name mono">{agent.name}</span>
          <span className="role">
            {isDetached ? "in CLI · click to re-attach" : agent.role}
          </span>
        </span>
        <span className="status-dot" data-status={agent.status} />
      </button>
      <button
        type="button"
        className="rail-agent-kebab"
        aria-label={`More actions for ${agent.name}`}
        title="More"
        onClick={(e) => {
          // Don't bubble into the row's focus handler; opening the
          // menu shouldn't also re-focus the canvas tile.
          e.stopPropagation();
          setMenuOpen((v) => !v);
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

function ShareRailRow({
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
      className="rail-share"
      title={`Click to copy ${realPath}`}
      onClick={() => onCopy(realPath)}
    >
      <span className="rail-share-glyph" aria-hidden>
        📁
      </span>
      <span className="rail-share-meta">
        <span className="rail-share-name">
          {share.name}
          {share.is_custom_location && (
            <span className="rail-share-badge" title="Custom location">
              custom
            </span>
          )}
        </span>
        <span className="rail-share-mount mono">./{share.mount_path}/</span>
      </span>
    </button>
  );
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
