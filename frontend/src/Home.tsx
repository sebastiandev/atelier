import { useEffect, useMemo, useState } from "react";

import {
  type CreateWorkPayload,
  type ProjectSummary,
  type WorkSummary,
  createWork,
  listProjects,
  listWorks,
} from "./api";
import { NewProjectDialog } from "./NewProjectDialog";
import { NewWorkDialog } from "./NewWorkDialog";
import { ThemeToggle } from "./ThemeToggle";
import { TweaksToggle } from "./TweaksPanel";

// "all" shows everything (incl. loose). "loose" shows works without a
// project. { slug } scopes to one project. Drives both the project-card
// selection state and the latest-work filter pills (single source of
// truth so they stay in sync).
type ProjectFilter = "all" | "loose" | { slug: string };

type HomeView = "tiles" | "list";

const HOME_VIEW_KEY = "atelier:home:view";

function readHomeView(): HomeView {
  // Default to "list" — Home is a chronological feed and rows scan
  // faster than tiles. Project pages default to "tiles" instead since
  // they're scoped to a smaller set the user wants to graze.
  try {
    const raw = window.localStorage.getItem(HOME_VIEW_KEY);
    if (raw === "tiles" || raw === "list") return raw;
  } catch {
    // private mode / SSR — fall through
  }
  return "list";
}

function writeHomeView(view: HomeView): void {
  try {
    window.localStorage.setItem(HOME_VIEW_KEY, view);
  } catch {
    // ignore — preference loss is non-fatal
  }
}

export function Home() {
  const [works, setWorks] = useState<WorkSummary[]>([]);
  const [projects, setProjects] = useState<ProjectSummary[]>([]);
  const [loadError, setLoadError] = useState<string | null>(null);
  const [filter, setFilter] = useState<ProjectFilter>("all");
  const [view, setView] = useState<HomeView>(() => readHomeView());
  const [workDialogOpen, setWorkDialogOpen] = useState(false);
  const [projectDialogOpen, setProjectDialogOpen] = useState(false);

  function changeView(next: HomeView) {
    setView(next);
    writeHomeView(next);
  }

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

  // Global shortcuts: W → new work, P → new project. Both ignored while
  // any modal is open or focus is in an editable field, and skipped when
  // a chord modifier is held (so Cmd+W still closes the tab).
  useEffect(() => {
    function onKey(e: KeyboardEvent) {
      if (workDialogOpen || projectDialogOpen) return;
      if (e.metaKey || e.ctrlKey || e.altKey) return;
      const target = e.target as HTMLElement | null;
      const tag = target?.tagName;
      if (tag === "INPUT" || tag === "TEXTAREA" || target?.isContentEditable) return;
      if (e.key === "w" || e.key === "W") {
        e.preventDefault();
        setWorkDialogOpen(true);
      } else if (e.key === "p" || e.key === "P") {
        e.preventDefault();
        setProjectDialogOpen(true);
      }
    }
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [workDialogOpen, projectDialogOpen]);

  async function handleCreateWork(payload: CreateWorkPayload) {
    await createWork(payload);
    await refresh();
    setWorkDialogOpen(false);
  }

  // Lookup map: project_slug → ProjectSummary, used when rendering chips
  // and pill counts.
  const projectMap = useMemo(() => {
    const m = new Map<string, ProjectSummary>();
    for (const p of projects) m.set(p.slug, p);
    return m;
  }, [projects]);

  const totalCount = works.length;
  const looseCount = works.filter((w) => w.project_slug == null).length;

  // Latest-work list: filter-aware, sorted by created_at desc, all statuses.
  const latest = useMemo(() => {
    const filtered = works.filter((w) => {
      if (filter === "all") return true;
      if (filter === "loose") return w.project_slug == null;
      return w.project_slug === filter.slug;
    });
    return [...filtered].sort((a, b) => b.created_at.localeCompare(a.created_at));
  }, [works, filter]);

  const recentByProject = useMemo(() => {
    const out = new Map<string, WorkSummary[]>();
    for (const p of projects) {
      out.set(
        p.slug,
        works
          .filter((w) => w.project_slug === p.slug)
          .sort((a, b) => b.created_at.localeCompare(a.created_at))
          .slice(0, 3),
      );
    }
    return out;
  }, [projects, works]);
  const recentLoose = useMemo(
    () =>
      works
        .filter((w) => w.project_slug == null)
        .sort((a, b) => b.created_at.localeCompare(a.created_at))
        .slice(0, 3),
    [works],
  );

  function isFilterActive(target: ProjectFilter): boolean {
    if (typeof target === "object" && typeof filter === "object") {
      return target.slug === filter.slug;
    }
    return target === filter;
  }

  function activeFilterLabel(): string | null {
    if (filter === "all") return null;
    if (filter === "loose") return "Loose work";
    return projectMap.get(filter.slug)?.name ?? filter.slug;
  }

  // When opening NewWorkDialog, seed the picker from the current filter.
  // "all" → no preset (defaults to Loose). "loose" → null preset.
  // { slug } → that project. The picker stays freely editable (per design,
  // home-launched dialogs aren't locked).
  const newWorkPreset: string | null | undefined =
    filter === "all" ? undefined : filter === "loose" ? null : filter.slug;

  return (
    <div className="home">
      <header className="topbar wv-topbar">
        <a className="brand brand-link" href="/">
          <span className="brand-mark" /> Atelier
        </a>
        <div className="spacer" />
        <a className="btn-ghost-sm" href="/connections">
          Connections
        </a>
        <TweaksToggle />
        <ThemeToggle />
      </header>

      <div className="home-hd">
        <div>
          <h1>Your work</h1>
          <p className="tagline">
            Each work unit is a goal and the agents working on it.
          </p>
        </div>
        <button className="btn primary" onClick={() => setWorkDialogOpen(true)}>
          + New work <span className="kbd">W</span>
        </button>
      </div>

      {/* ----- Projects section ----- */}
      <section className="proj-section">
        <div className="proj-section-hd">
          <span className="latest-title">
            Projects <span className="count">{projects.length}</span>
          </span>
          <span className="spacer" />
          <button
            className="btn-ghost-sm"
            onClick={() => setProjectDialogOpen(true)}
          >
            + New project <span className="kbd">P</span>
          </button>
        </div>
        <div className="proj-grid">
          {projects.map((p) => (
            <ProjectCard
              key={p.slug}
              project={p}
              recent={recentByProject.get(p.slug) ?? []}
              activeCount={
                works.filter(
                  (w) => w.project_slug === p.slug && w.status === "active",
                ).length
              }
            />
          ))}
          <LooseCard
            recent={recentLoose}
            activeCount={
              works.filter((w) => w.project_slug == null && w.status === "active").length
            }
            selected={isFilterActive("loose")}
            onSelect={() => setFilter(isFilterActive("loose") ? "all" : "loose")}
          />
          <button
            className="proj-card create"
            onClick={() => setProjectDialogOpen(true)}
          >
            <span className="plus">+</span>
            <span>New project</span>
          </button>
        </div>
      </section>

      {/* ----- Latest work section: header + filter pills + flat list ----- */}
      <section className="latest-section">
        <div className="latest-hd">
          <span className="latest-title">
            Latest work <span className="count">{totalCount}</span>
          </span>
          <div className="latest-pills">
            <button
              className={"filter-pill" + (isFilterActive("all") ? " active" : "")}
              onClick={() => setFilter("all")}
            >
              All
            </button>
            {projects.map((p) => (
              <button
                key={p.slug}
                className={
                  "filter-pill" + (isFilterActive({ slug: p.slug }) ? " active" : "")
                }
                style={{ ["--proj-h" as string]: String(p.color) }}
                onClick={() => setFilter({ slug: p.slug })}
              >
                <span className="filter-pill-glyph">{p.glyph}</span>
                {p.name}
              </button>
            ))}
            <button
              className={"filter-pill" + (isFilterActive("loose") ? " active" : "")}
              onClick={() => setFilter("loose")}
            >
              Loose <span className="count">{looseCount}</span>
            </button>
            <div
              className="view-toggle"
              role="tablist"
              aria-label="View"
              style={{ marginLeft: "0.5rem" }}
            >
              <button
                className={"view-toggle-btn" + (view === "tiles" ? " active" : "")}
                onClick={() => changeView("tiles")}
                aria-pressed={view === "tiles"}
                title="Tile view"
              >
                <GridIcon /> Tiles
              </button>
              <button
                className={"view-toggle-btn" + (view === "list" ? " active" : "")}
                onClick={() => changeView("list")}
                aria-pressed={view === "list"}
                title="List view"
              >
                <ListIcon /> List
              </button>
            </div>
          </div>
        </div>

        {loadError && <div className="form-error">{loadError}</div>}

        {view === "list" ? (
          <div className="work-list">
            <button
              className="work-row create"
              onClick={() => setWorkDialogOpen(true)}
            >
              <span className="work-row-status" aria-hidden="true">
                +
              </span>
              <span className="work-row-main">
                <span className="work-row-top">
                  <span className="work-row-title">
                    Start new work
                    {typeof filter === "object"
                      ? ` in ${activeFilterLabel()}`
                      : filter === "loose"
                        ? " · loose"
                        : ""}
                  </span>
                </span>
              </span>
              <span className="kbd">W</span>
            </button>
            {latest.map((w) => (
              <WorkRow
                key={w.slug}
                work={w}
                project={w.project_slug ? projectMap.get(w.project_slug) ?? null : null}
                showProject={filter === "all"}
              />
            ))}
            {latest.length === 0 && (
              <div className="work-row-empty">
                {filter === "all"
                  ? "No work yet."
                  : `No work in ${activeFilterLabel()} yet.`}
              </div>
            )}
          </div>
        ) : (
          <div className="home-grid">
            <button
              className="work-card create"
              onClick={() => setWorkDialogOpen(true)}
            >
              <span className="plus">+</span>
              <span className="create-title">
                Start new work
                {typeof filter === "object"
                  ? ` in ${activeFilterLabel()}`
                  : filter === "loose"
                    ? " · loose"
                    : ""}
              </span>
              <span className="hint">Brief and name.</span>
            </button>
            {latest.map((w) => (
              <WorkTile
                key={w.slug}
                work={w}
                project={w.project_slug ? projectMap.get(w.project_slug) ?? null : null}
                showProject={filter === "all"}
              />
            ))}
            {latest.length === 0 && (
              <div className="empty hint">
                {filter === "all"
                  ? "No work yet."
                  : `No work in ${activeFilterLabel()} yet.`}
              </div>
            )}
          </div>
        )}
      </section>

      {workDialogOpen && (
        <NewWorkDialog
          onClose={() => setWorkDialogOpen(false)}
          onCreate={handleCreateWork}
          projects={projects}
          presetProjectSlug={newWorkPreset}
        />
      )}
      {projectDialogOpen && (
        <NewProjectDialog
          onClose={() => setProjectDialogOpen(false)}
          onCreated={async (created) => {
            setProjectDialogOpen(false);
            await refresh();
            setFilter({ slug: created.slug });
          }}
        />
      )}
    </div>
  );
}

function ProjectCard({
  project,
  recent,
  activeCount,
}: {
  project: ProjectSummary;
  recent: WorkSummary[];
  activeCount: number;
}) {
  // Card is a clickable div (not anchor) because the recent-work rows
  // inside are anchors — nested <a> is invalid HTML. Recent rows
  // stopPropagation so they navigate without firing the card click.
  const goToProject = () => window.location.assign(`/projects/${project.slug}`);
  const hasConnections =
    project.default_jira_conn || project.default_sentry_conn;
  return (
    <div
      className="proj-card"
      style={{ ["--proj-h" as string]: String(project.color) }}
      onClick={goToProject}
      role="button"
      tabIndex={0}
      onKeyDown={(e) => {
        if (e.key === "Enter" || e.key === " ") {
          e.preventDefault();
          goToProject();
        }
      }}
    >
      <div className="pc-hd">
        <span className="pc-glyph" aria-hidden="true">
          {project.glyph}
        </span>
        <div className="pc-titlewrap">
          <div className="pc-name">{project.name}</div>
          <div className="pc-id mono">{project.slug}</div>
        </div>
        <span className="pc-count">
          <span className="pc-count-num">{activeCount}</span>
          <span className="pc-count-sub">active</span>
        </span>
      </div>
      {project.description && <div className="pc-desc">{project.description}</div>}
      <div className="pc-recent">
        {recent.length === 0 && (
          <div className="hint pc-recent-empty">No work yet.</div>
        )}
        {recent.map((w) => (
          <a
            key={w.slug}
            className="pc-recent-row"
            href={`/works/${w.slug}`}
            onClick={(e) => e.stopPropagation()}
          >
            <WorkStatusIcon status={w.status} />
            <span className="mono">{w.slug}</span>
            <span className="pc-recent-title">{w.name}</span>
          </a>
        ))}
      </div>
      {(hasConnections || true) && (
        <div className="pc-footer">
          {project.default_jira_conn && (
            <span className="conn-pill" data-source="jira">
              JI
            </span>
          )}
          {project.default_sentry_conn && (
            <span className="conn-pill" data-source="sentry">
              SE
            </span>
          )}
          <span className="pc-open">
            Open <span aria-hidden="true">›</span>
          </span>
        </div>
      )}
    </div>
  );
}

function WorkStatusIcon({ status }: { status: WorkSummary["status"] }) {
  if (status === "active") {
    return (
      <span className="work-row-status active" aria-hidden="true">
        <span className="dot live" />
      </span>
    );
  }
  return (
    <span className="work-row-status completed" aria-hidden="true">
      ✓
    </span>
  );
}

function LooseCard({
  recent,
  activeCount,
  selected,
  onSelect,
}: {
  recent: WorkSummary[];
  activeCount: number;
  selected: boolean;
  onSelect: () => void;
}) {
  return (
    <div
      className={"proj-card loose" + (selected ? " selected" : "")}
      onClick={onSelect}
      role="button"
      tabIndex={0}
      onKeyDown={(e) => {
        if (e.key === "Enter" || e.key === " ") {
          e.preventDefault();
          onSelect();
        }
      }}
    >
      <div className="pc-hd">
        <span className="pc-glyph loose" aria-hidden="true">
          ◇
        </span>
        <div className="pc-titlewrap">
          <div className="pc-name">Loose work</div>
          <div className="pc-id">Not in any project</div>
        </div>
        <span className="pc-count">
          <span className="pc-count-num">{activeCount}</span>
          <span className="pc-count-sub">active</span>
        </span>
      </div>
      <div className="pc-recent">
        {recent.length === 0 && (
          <div className="hint pc-recent-empty">No loose work.</div>
        )}
        {recent.map((w) => (
          <a
            key={w.slug}
            className="pc-recent-row"
            href={`/works/${w.slug}`}
            onClick={(e) => e.stopPropagation()}
          >
            <WorkStatusIcon status={w.status} />
            <span className="mono">{w.slug}</span>
            <span className="pc-recent-title">{w.name}</span>
          </a>
        ))}
      </div>
    </div>
  );
}

function WorkRow({
  work,
  project,
  showProject,
}: {
  work: WorkSummary;
  project: ProjectSummary | null;
  showProject: boolean;
}) {
  const created = formatDate(work.created_at);
  return (
    <a className="work-row" href={`/works/${work.slug}`}>
      <span className={`work-row-status ${work.status}`} title={work.status}>
        {work.status === "active" ? <span className="dot live" /> : "✓"}
      </span>
      <span className="work-row-main">
        <span className="work-row-top">
          <span className="work-row-id">{work.slug}</span>
          <span className="work-row-title">{work.name}</span>
        </span>
        <span className="work-row-meta">
          <span>{created}</span>
        </span>
      </span>
      {showProject && project && (
        <span
          className="proj-chip"
          style={{ ["--proj-h" as string]: String(project.color) }}
        >
          <span className="dot" />
          {project.name}
        </span>
      )}
    </a>
  );
}

function WorkTile({
  work,
  project,
  showProject,
}: {
  work: WorkSummary;
  project: ProjectSummary | null;
  showProject: boolean;
}) {
  const created = formatDate(work.created_at);
  return (
    <a className="work-card" href={`/works/${work.slug}`}>
      <div className="wc-hd">
        <div>
          <div className="wc-id mono">
            {work.slug} · {created}
          </div>
          <div className="wc-title">{work.name}</div>
        </div>
        <span className={`chip chip-${work.status}`}>
          {work.status === "active" && <span className="dot live" />}
          {work.status}
        </span>
      </div>
      <div className="wc-desc">{work.description}</div>
      {showProject && project && (
        <span
          className="proj-chip"
          style={{ ["--proj-h" as string]: String(project.color) }}
        >
          <span className="dot" />
          {project.name}
        </span>
      )}
    </a>
  );
}

function GridIcon() {
  return (
    <svg width="12" height="12" viewBox="0 0 12 12" fill="none" aria-hidden="true">
      <rect x="1" y="1" width="4" height="4" rx="1" fill="currentColor" />
      <rect x="7" y="1" width="4" height="4" rx="1" fill="currentColor" />
      <rect x="1" y="7" width="4" height="4" rx="1" fill="currentColor" />
      <rect x="7" y="7" width="4" height="4" rx="1" fill="currentColor" />
    </svg>
  );
}

function ListIcon() {
  return (
    <svg width="12" height="12" viewBox="0 0 12 12" fill="none" aria-hidden="true">
      <rect x="1" y="2" width="10" height="1.5" rx="0.5" fill="currentColor" />
      <rect x="1" y="5.25" width="10" height="1.5" rx="0.5" fill="currentColor" />
      <rect x="1" y="8.5" width="10" height="1.5" rx="0.5" fill="currentColor" />
    </svg>
  );
}

function formatDate(iso: string): string {
  const d = new Date(iso);
  if (isNaN(d.getTime())) return iso;
  return d.toLocaleDateString(undefined, { month: "short", day: "numeric" });
}
