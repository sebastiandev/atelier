import { useEffect, useMemo, useState } from "react";

import {
  type CreateWorkPayload,
  type ProjectDetail,
  type WorkSummary,
  createWork,
  getProject,
  listWorks,
} from "./api";
import { EditProjectDialog } from "./EditProjectDialog";
import { NewWorkDialog } from "./NewWorkDialog";
import { ThemeToggle } from "./ThemeToggle";
import { TweaksToggle } from "./TweaksPanel";

type Tab = "active" | "completed";
type View = "tiles" | "list";

const VIEW_KEY_PREFIX = "atelier:project:";

function readPersistedView(slug: string): View {
  // Per-project view preference. localStorage so each project remembers
  // whether the user prefers Tiles or List independently. Default Tiles
  // on first visit (matches design).
  try {
    const raw = window.localStorage.getItem(`${VIEW_KEY_PREFIX}${slug}:view`);
    if (raw === "list" || raw === "tiles") return raw;
  } catch {
    // private mode / SSR / etc — fall through to default
  }
  return "tiles";
}

function writePersistedView(slug: string, view: View): void {
  try {
    window.localStorage.setItem(`${VIEW_KEY_PREFIX}${slug}:view`, view);
  } catch {
    // ignore — preference loss is non-fatal
  }
}

export function ProjectScreen({ projectSlug }: { projectSlug: string }) {
  const [project, setProject] = useState<ProjectDetail | null>(null);
  const [works, setWorks] = useState<WorkSummary[]>([]);
  const [error, setError] = useState<string | null>(null);
  const [tab, setTab] = useState<Tab>("active");
  const [view, setView] = useState<View>(() => readPersistedView(projectSlug));
  const [workDialogOpen, setWorkDialogOpen] = useState(false);
  const [editDialogOpen, setEditDialogOpen] = useState(false);

  async function refresh() {
    try {
      const [p, allWorks] = await Promise.all([
        getProject(projectSlug),
        listWorks(),
      ]);
      setProject(p);
      setWorks(allWorks.filter((w) => w.project_slug === projectSlug));
      setError(null);
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err));
    }
  }

  useEffect(() => {
    refresh();
    setView(readPersistedView(projectSlug));
  }, [projectSlug]);

  useEffect(() => {
    function onKey(e: KeyboardEvent) {
      if (workDialogOpen) return;
      if (e.metaKey || e.ctrlKey || e.altKey) return;
      const target = e.target as HTMLElement | null;
      const tag = target?.tagName;
      if (tag === "INPUT" || tag === "TEXTAREA" || target?.isContentEditable) return;
      if (e.key === "w" || e.key === "W") {
        e.preventDefault();
        setWorkDialogOpen(true);
      }
    }
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [workDialogOpen]);

  async function handleCreateWork(payload: CreateWorkPayload) {
    // Re-assert project_slug here so a stale dialog prop can't leak through.
    await createWork({ ...payload, project_slug: projectSlug });
    await refresh();
    setWorkDialogOpen(false);
  }

  function changeView(next: View) {
    setView(next);
    writePersistedView(projectSlug, next);
  }

  const activeCount = works.filter((w) => w.status === "active").length;
  const completedCount = works.filter((w) => w.status === "completed").length;

  const filtered = useMemo(() => {
    const list = works.filter((w) => w.status === tab);
    return [...list].sort((a, b) => b.created_at.localeCompare(a.created_at));
  }, [works, tab]);

  if (error) {
    return (
      <div className="home">
        <div className="form-error">{error}</div>
        <a href="/" className="hint">
          ← back
        </a>
      </div>
    );
  }

  if (!project) {
    return <div className="work-loading hint">Loading…</div>;
  }

  return (
    <div className="home" style={{ ["--proj-h" as string]: String(project.color) }}>
      <header className="topbar wv-topbar">
        <a className="brand brand-link" href="/">
          <span className="brand-mark" /> Atelier
        </a>
        <a className="btn-ghost-sm" href="/">
          ← Workspace
        </a>
        <span className="crumbs">
          <span className="sep">/</span>
          <span className="now">
            <span className="filter-pill-glyph" aria-hidden="true">
              {project.glyph}
            </span>
            {project.name}
          </span>
        </span>
        <span className="hint" style={{ marginLeft: "0.5rem" }}>
          {project.slug}
        </span>
        <div className="spacer" />
        <TweaksToggle />
        <ThemeToggle />
      </header>

      <section className="proj-hero">
        <div className="proj-hero-bar" aria-hidden="true" />
        <div className="proj-hero-body">
          <div className="proj-hero-l">
            <span className="proj-glyph-xl" aria-hidden="true">
              {project.glyph}
            </span>
            <div>
              <div className="hint mono">{project.slug}</div>
              <h1 className="proj-hero-name">{project.name}</h1>
              {project.description && (
                <div className="proj-hero-desc">{project.description}</div>
              )}
            </div>
          </div>
          <div className="proj-hero-actions">
            <button
              className="btn"
              onClick={() => setEditDialogOpen(true)}
              title="Edit name, glyph, color, default connections"
            >
              Edit
            </button>
            <button
              className="btn primary proj-hero-cta"
              onClick={() => setWorkDialogOpen(true)}
            >
              + New work in {project.name}
            </button>
          </div>
        </div>
        <dl className="proj-hero-meta">
          <div className="proj-meta-item">
            <dt className="proj-meta-lbl">ID</dt>
            <dd className="proj-meta-val mono">{project.slug}</dd>
          </div>
          <div className="proj-meta-item">
            <dt className="proj-meta-lbl">Default connections</dt>
            <dd className="proj-meta-val">
              <div className="proj-defaults">
                {project.default_jira_conn && (
                  <span className="conn-pill" data-source="jira">
                    JI · {project.default_jira_conn}
                  </span>
                )}
                {project.default_sentry_conn && (
                  <span className="conn-pill" data-source="sentry">
                    SE · {project.default_sentry_conn}
                  </span>
                )}
                {!project.default_jira_conn && !project.default_sentry_conn && (
                  <span className="hint">None</span>
                )}
                <a
                  className="btn-icon-sm"
                  href="/connections"
                  title="Manage connections"
                  aria-label="Manage connections"
                >
                  ↗
                </a>
              </div>
            </dd>
          </div>
          <div className="proj-meta-item">
            <dt className="proj-meta-lbl">Active</dt>
            <dd className="proj-meta-val proj-meta-num">{activeCount}</dd>
          </div>
          <div className="proj-meta-item">
            <dt className="proj-meta-lbl">Completed</dt>
            <dd className="proj-meta-val proj-meta-num">{completedCount}</dd>
          </div>
        </dl>
      </section>

      <div className="home-tabs proj-tabs">
        <button
          className={"home-tab" + (tab === "active" ? " active" : "")}
          onClick={() => setTab("active")}
        >
          Active <span className="count mono">{activeCount}</span>
        </button>
        <button
          className={"home-tab" + (tab === "completed" ? " active" : "")}
          onClick={() => setTab("completed")}
        >
          Completed <span className="count mono">{completedCount}</span>
        </button>
        <div className="view-toggle" role="tablist" aria-label="View">
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

      {view === "tiles" ? (
        <div className="home-grid">
          <button
            className="work-card create"
            onClick={() => setWorkDialogOpen(true)}
          >
            <span className="plus">+</span>
            <span className="create-title">Start new work</span>
            <span className="hint">in {project.name}</span>
          </button>
          {filtered.map((w) => (
            <WorkTile key={w.slug} work={w} />
          ))}
          {filtered.length === 0 && (
            <div className="empty hint">
              {tab === "active"
                ? "Nothing active here yet."
                : "No completed work yet."}
            </div>
          )}
        </div>
      ) : (
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
                  Start new work in {project.name}
                </span>
              </span>
            </span>
            <span className="kbd">W</span>
          </button>
          {filtered.map((w) => (
            <a key={w.slug} className="work-row" href={`/works/${w.slug}`}>
              <span className={`work-row-status ${w.status}`} title={w.status}>
                {w.status === "active" ? <span className="dot live" /> : "✓"}
              </span>
              <span className="work-row-main">
                <span className="work-row-top">
                  <span className="work-row-id">{w.slug}</span>
                  <span className="work-row-title">{w.name}</span>
                </span>
                <span className="work-row-meta">
                  <span>{formatDate(w.created_at)}</span>
                  <span className="wc-stat" title={`${w.agent_count} agents`}>
                    <AgentIcon /> {w.agent_count}
                  </span>
                  <span className="wc-stat" title={`${w.artifact_count} artifacts`}>
                    <ArtifactIcon /> {w.artifact_count}
                  </span>
                </span>
              </span>
            </a>
          ))}
          {filtered.length === 0 && (
            <div className="work-row-empty">
              {tab === "active"
                ? "Nothing active here yet."
                : "No completed work yet."}
            </div>
          )}
        </div>
      )}

      {workDialogOpen && (
        <NewWorkDialog
          onClose={() => setWorkDialogOpen(false)}
          onCreate={handleCreateWork}
          projects={[project]}
          presetProjectSlug={project.slug}
          lockProjectSlug
        />
      )}

      {editDialogOpen && (
        <EditProjectDialog
          project={project}
          onClose={() => setEditDialogOpen(false)}
          onSaved={(updated) => {
            setProject(updated);
            setEditDialogOpen(false);
          }}
          onDeleted={() => {
            // No project on screen any more — drop back to the workspace.
            window.location.href = "/";
          }}
        />
      )}
    </div>
  );
}

function WorkTile({ work }: { work: WorkSummary }) {
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
      <div className="wc-stats">
        <span className="wc-stat" title={`${work.agent_count} agents`}>
          <AgentIcon /> {work.agent_count}
        </span>
        <span className="wc-stat" title={`${work.artifact_count} artifacts`}>
          <ArtifactIcon /> {work.artifact_count}
        </span>
      </div>
    </a>
  );
}

function AgentIcon() {
  return (
    <svg width="11" height="11" viewBox="0 0 12 12" fill="none" aria-hidden="true">
      <circle cx="6" cy="3.6" r="1.9" fill="currentColor" />
      <path
        d="M2 11 C2 7.6 4 6.6 6 6.6 C8 6.6 10 7.6 10 11 Z"
        fill="currentColor"
      />
    </svg>
  );
}

function ArtifactIcon() {
  return (
    <svg width="11" height="11" viewBox="0 0 12 12" fill="none" aria-hidden="true">
      <path
        d="M3 1 L7.4 1 L10 3.6 L10 11 L3 11 Z"
        fill="none"
        stroke="currentColor"
        strokeWidth="1.2"
        strokeLinejoin="round"
      />
      <path
        d="M7.4 1 L7.4 3.6 L10 3.6"
        fill="none"
        stroke="currentColor"
        strokeWidth="1.2"
      />
    </svg>
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
