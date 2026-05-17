import { useEffect, useMemo, useState } from "react";

import {
  type CreateWorkPayload,
  type ProjectSummary,
  type WorkSummary,
  createWork,
  listProjects,
  listWorks,
} from "./api";
import { CheckIcon, ChevronRightIcon, PlugIcon, SearchIcon, SlidersIcon } from "./Icons";
import { NewProjectDialog } from "./NewProjectDialog";
import { NewWorkDialog } from "./NewWorkDialog";
import { SearchModal } from "./SearchModal";
import { Switcher, type SwitcherItem } from "./Switcher";
import { ThemeToggle } from "./ThemeToggle";

// "all" → everything (incl. loose). "loose" → no project. { slug } →
// scope to a specific project. Single source of truth shared by the
// filter pills row and the "+ start new work" composer row (the +
// preselects the active filter's project in the new-work dialog).
type ProjectFilter = "all" | "loose" | { slug: string };

export function Home() {
  const [works, setWorks] = useState<WorkSummary[]>([]);
  const [projects, setProjects] = useState<ProjectSummary[]>([]);
  const [loadError, setLoadError] = useState<string | null>(null);
  const [filter, setFilter] = useState<ProjectFilter>("all");
  const [workDialogOpen, setWorkDialogOpen] = useState(false);
  const [projectDialogOpen, setProjectDialogOpen] = useState(false);
  const [projectSwitcherOpen, setProjectSwitcherOpen] = useState(false);
  const [workSwitcherOpen, setWorkSwitcherOpen] = useState(false);
  const [searchOpen, setSearchOpen] = useState(false);

  async function refresh() {
    try {
      const [w, p] = await Promise.all([listWorks(), listProjects()]);
      setWorks(w);
      setProjects(p);
      setLoadError(null);
    } catch (err) {
      setLoadError(err instanceof Error ? err.message : String(err));
    }
  }

  useEffect(() => {
    refresh();
  }, []);

  // Global shortcuts:
  //   N       → new work
  //   P       → new project
  //   Shift+W → switch work (palette over all works in the workspace)
  //   Shift+P → switch project (palette)
  // Ignored while modals/palettes are open or focus is in an editable
  // field, and skipped when a chord modifier is held (Cmd+W = close tab).
  useEffect(() => {
    function onKey(e: KeyboardEvent) {
      if (workDialogOpen || projectDialogOpen) return;
      if (projectSwitcherOpen || workSwitcherOpen) return;
      if (searchOpen) return;
      if (e.metaKey || e.ctrlKey || e.altKey) return;
      const target = e.target as HTMLElement | null;
      const tag = target?.tagName;
      if (tag === "INPUT" || tag === "TEXTAREA" || target?.isContentEditable) return;
      if (e.shiftKey && (e.key === "F" || e.key === "f" || e.key === "S" || e.key === "s")) {
        e.preventDefault();
        setSearchOpen(true);
      } else if (e.shiftKey && (e.key === "W" || e.key === "w")) {
        e.preventDefault();
        setWorkSwitcherOpen(true);
      } else if (e.shiftKey && (e.key === "P" || e.key === "p")) {
        e.preventDefault();
        setProjectSwitcherOpen(true);
      } else if (!e.shiftKey && (e.key === "n" || e.key === "N")) {
        e.preventDefault();
        setWorkDialogOpen(true);
      } else if (!e.shiftKey && (e.key === "p" || e.key === "P")) {
        e.preventDefault();
        setProjectDialogOpen(true);
      }
    }
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [
    workDialogOpen,
    projectDialogOpen,
    projectSwitcherOpen,
    workSwitcherOpen,
    searchOpen,
  ]);

  async function handleCreateWork(payload: CreateWorkPayload) {
    await createWork(payload);
    await refresh();
    setWorkDialogOpen(false);
  }

  const projectMap = useMemo(() => {
    const m = new Map<string, ProjectSummary>();
    for (const p of projects) m.set(p.slug, p);
    return m;
  }, [projects]);

  const activeCount = works.filter((w) => w.status === "active").length;
  const looseCount = works.filter((w) => w.project_slug == null).length;

  // Top 10 most-recent work for the rail's "Latest work" section.
  const latest = useMemo(() => {
    const filtered = works.filter((w) => {
      if (filter === "all") return true;
      if (filter === "loose") return w.project_slug == null;
      return w.project_slug === filter.slug;
    });
    return [...filtered]
      .sort((a, b) => {
        // Active first, then newest within each group.
        if (a.status !== b.status) return a.status === "active" ? -1 : 1;
        return b.created_at.localeCompare(a.created_at);
      })
      .slice(0, 10);
  }, [works, filter]);

  const newWorkPreset: string | null | undefined =
    filter === "all" ? undefined : filter === "loose" ? null : filter.slug;

  // Switcher palettes — palette mode is keyboard + click; we feed the
  // full project / work universe (the v2 behavior).
  const projectItems = useMemo<SwitcherItem[]>(() => {
    const sorted = [...projects].sort((a, b) => {
      if (a.pinned !== b.pinned) return a.pinned ? -1 : 1;
      return a.name.localeCompare(b.name);
    });
    return sorted.map((p) => ({
      slug: p.slug,
      name: p.name,
      glyph: p.glyph,
      hue: p.color,
      href: `/projects/${p.slug}`,
    }));
  }, [projects]);

  const workItems = useMemo<SwitcherItem[]>(() => {
    return [...works]
      .sort((a, b) => b.created_at.localeCompare(a.created_at))
      .map((w) => {
        const p = w.project_slug ? projectMap.get(w.project_slug) : undefined;
        return {
          slug: w.slug,
          name: w.name,
          subtitle: p ? p.name : "Loose work",
          glyph: p?.glyph,
          hue: p?.color,
          href: `/works/${w.slug}`,
        };
      });
  }, [works, projectMap]);

  return (
    <div className="shell-v3 wide-left home-v3">
      {/* LEFT: hero wordmark + tagline + 3 action buttons + footer */}
      <aside className="shell-left">
        <div className="home-v3-hero">
          <div className="home-v3-hero-top">
            <div className="home-v3-mark" aria-label="Atelier">
              <span className="glyph-a" aria-hidden>
                {/* viewBox crops to the A's bounds so the SVG box's
                    baseline lands at the bottom of the legs. The
                    dash sits at y=56 (below the viewBox) and renders
                    via overflow:visible — it's decoration, not part
                    of the letterform's optical baseline. */}
                <svg viewBox="0 0 64 50" overflow="visible">
                  <g
                    fill="none"
                    stroke="currentColor"
                    strokeWidth="6"
                    strokeLinecap="butt"
                    strokeLinejoin="miter"
                    strokeMiterlimit="10"
                  >
                    {/* Single path through both legs so the apex at
                        (32, 12) renders as a clean miter joint, not
                        two butted flat caps. */}
                    <path d="M 14 50 L 32 12 L 50 50" />
                    <path d="M 21 36 L 43 36" />
                  </g>
                  <rect
                    className="cur-dash"
                    x="11"
                    y="56"
                    width="42"
                    height="5"
                    fill="currentColor"
                  >
                    <animate
                      attributeName="opacity"
                      values="1;1;0;0"
                      keyTimes="0;0.55;0.6;1"
                      dur="1.05s"
                      repeatCount="indefinite"
                    />
                  </rect>
                </svg>
              </span>
              <span className="rest">telier</span>
            </div>
            <div className="home-v3-tag">
              Manage work across multiple agents.
              <br />
              <span className="strong">
                Group it under projects, or run it loose.
              </span>
            </div>
            <div className="home-v3-actions">
              <button className="btn primary" onClick={() => setWorkDialogOpen(true)}>
                + New work <span className="kbd">N</span>
              </button>
              <button className="btn" onClick={() => setProjectDialogOpen(true)}>
                + New project <span className="kbd">P</span>
              </button>
              <button
                className="btn ghost"
                onClick={() => setSearchOpen(true)}
              >
                <SearchIcon size={12} /> Search{" "}
                <span className="kbd">⇧F</span>
              </button>
            </div>
          </div>
          <div className="home-v3-foot">
            <div className="home-v3-meta">
              <div className="ln">
                <span className="lbl">$ status</span>
                <span className="val">
                  <span className="dot live" />
                  {activeCount} active · {works.length} total
                </span>
              </div>
              <div className="ln">
                <span className="lbl">$ projects</span>
                <span className="val">{projects.length} configured</span>
              </div>
              <div className="ln">
                <span className="lbl">$ atelier</span>
                <span className="val">v3 shell</span>
              </div>
            </div>
            <div className="home-v3-util">
              <a className="util-btn" href="/settings/connections">
                <PlugIcon size={11} /> connections
              </a>
              <a className="util-btn" href="/settings">
                <SlidersIcon size={11} /> settings
              </a>
              <ThemeToggle className="util-btn" labelled />
            </div>
          </div>
        </div>
      </aside>

      {/* RIGHT: latest work + projects */}
      <main className="shell-right home-v3-right">
        {loadError && (
          <div className="v3-empty" style={{ color: "var(--danger)" }}>
            {loadError}
          </div>
        )}
        <div className="scroll-area">
          {/* Latest work */}
          <V3SectionHd
            title="Latest work"
            count={works.length}
            right={
              <button onClick={() => setWorkSwitcherOpen(true)}>
                search <span className="kbd" style={{ marginLeft: 4 }}>⇧W</span>
              </button>
            }
          />
          <div className="v3-filter-bar">
            <button
              className={"v3-filter-pill" + (filter === "all" ? " active" : "")}
              onClick={() => setFilter("all")}
            >
              all <span className="count">{works.length}</span>
            </button>
            {projects.map((p) => (
              <button
                key={p.slug}
                className={
                  "v3-filter-pill" +
                  (typeof filter === "object" && filter.slug === p.slug
                    ? " active"
                    : "")
                }
                onClick={() => setFilter({ slug: p.slug })}
                style={{
                  ["--proj-color" as string]: `oklch(0.62 0.16 ${p.color})`,
                }}
              >
                <span className="swatch" />
                {p.name}
              </button>
            ))}
            <button
              className={"v3-filter-pill" + (filter === "loose" ? " active" : "")}
              onClick={() => setFilter("loose")}
            >
              loose <span className="count">{looseCount}</span>
            </button>
          </div>
          <div className="v3-rule" />
          <button
            className="v3-add-row"
            onClick={() => setWorkDialogOpen(true)}
          >
            <span className="marker">+</span> start new work
            {typeof filter === "object" && projectMap.get(filter.slug) && (
              <span style={{ color: "var(--fg-4)", marginLeft: 6 }}>
                · in {projectMap.get(filter.slug)!.name}
              </span>
            )}
            <span className="kbd">N</span>
          </button>
          {latest.length === 0 && (
            <div className="v3-empty">
              no work {filter !== "all" ? "for this filter" : "yet"}.
            </div>
          )}
          {latest.map((w) => (
            <V3WorkRow
              key={w.slug}
              work={w}
              project={
                w.project_slug ? projectMap.get(w.project_slug) ?? null : null
              }
              showProject={filter === "all"}
            />
          ))}

          {/* Projects */}
          <div style={{ height: 28 }} />
          <V3SectionHd
            title="Projects"
            count={projects.length}
            right={
              <button onClick={() => setProjectDialogOpen(true)}>
                + new <span className="kbd" style={{ marginLeft: 4 }}>P</span>
              </button>
            }
          />
          <div className="v3-rule" />
          {projects.map((p) => {
            const projWork = works.filter((w) => w.project_slug === p.slug);
            const active = projWork.filter((w) => w.status === "active").length;
            return (
              <a
                key={p.slug}
                className="v3-proj-row"
                href={`/projects/${p.slug}`}
                style={{
                  ["--proj-color" as string]: `oklch(0.62 0.16 ${p.color})`,
                  ["--proj-soft" as string]: `oklch(0.62 0.16 ${p.color} / 0.12)`,
                }}
              >
                <span className="swatch">{p.glyph}</span>
                <span>
                  <div className="name">{p.name}</div>
                  <div className="desc">{p.description}</div>
                </span>
                <span className="meta">
                  <span>
                    <span className="num">{active}</span> active
                  </span>
                  <span>
                    <span className="num">{projWork.length}</span> total
                  </span>
                </span>
                <span className="chev">
                  <ChevronRightIcon size={12} />
                </span>
              </a>
            );
          })}
          <button
            className="v3-proj-row"
            onClick={() => setFilter("loose")}
            style={{ borderTop: "1px solid var(--line-soft)" }}
          >
            <span className="swatch loose">·</span>
            <span>
              <div className="name">Loose work</div>
              <div className="desc">
                One-offs and quick fixes that aren't in a project.
              </div>
            </span>
            <span className="meta">
              <span>
                <span className="num">{looseCount}</span> total
              </span>
            </span>
            <span className="chev">
              <ChevronRightIcon size={12} />
            </span>
          </button>
        </div>
      </main>

      {workDialogOpen && (
        <NewWorkDialog
          projects={projects}
          presetProjectSlug={newWorkPreset}
          onClose={() => setWorkDialogOpen(false)}
          onCreate={handleCreateWork}
        />
      )}
      {projectDialogOpen && (
        <NewProjectDialog
          onClose={() => setProjectDialogOpen(false)}
          onCreated={() => {
            setProjectDialogOpen(false);
            refresh();
          }}
        />
      )}
      {projectSwitcherOpen && (
        <Switcher
          placeholder="Switch project"
          items={projectItems}
          onClose={() => setProjectSwitcherOpen(false)}
        />
      )}
      {workSwitcherOpen && (
        <Switcher
          placeholder="Switch work"
          items={workItems}
          onClose={() => setWorkSwitcherOpen(false)}
        />
      )}
      {searchOpen && (
        <SearchModal
          works={works}
          projects={projects}
          onClose={() => setSearchOpen(false)}
        />
      )}
    </div>
  );
}

function V3SectionHd({
  title,
  count,
  right,
}: {
  title: string;
  count?: number;
  right?: React.ReactNode;
}) {
  return (
    <div className="v3-shd">
      <span>
        {title}
        {count != null && (
          <span className="num" style={{ marginLeft: 8 }}>
            {count}
          </span>
        )}
      </span>
      {right && <span className="right">{right}</span>}
    </div>
  );
}

function V3WorkRow({
  work,
  project,
  showProject,
}: {
  work: WorkSummary;
  project: ProjectSummary | null;
  showProject: boolean;
}) {
  return (
    <a className="v3-work-row" href={`/works/${work.slug}`}>
      <span className="stat-dot" aria-hidden>
        {work.status === "active" ? (
          <span className="dot live" title="active" />
        ) : (
          <span className="check">
            <CheckIcon size={10} />
          </span>
        )}
      </span>
      <span className="id-mono">{work.slug}</span>
      <span className="name">{work.name}</span>
      <span className="age">{formatAge(work.created_at)}</span>
      {showProject ? (
        <span
          className="proj-tag"
          style={
            project
              ? {
                  ["--proj-color" as string]: `oklch(0.62 0.16 ${project.color})`,
                }
              : undefined
          }
        >
          <span className="swatch" />
          {project?.name ?? "loose"}
        </span>
      ) : (
        <span />
      )}
    </a>
  );
}

// Compact relative-time formatter. The list shows up to 10 rows so a
// crude "Xm/h/d/wk ago" is plenty — no need for a full i18n library.
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
