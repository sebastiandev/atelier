import { useEffect, useMemo, useState } from "react";

import {
  type CreateWorkPayload,
  type ProjectDetail,
  type ProjectSummary,
  type SharedFolderSummary,
  type WorkSummary,
  createWork,
  getProject,
  listProjectShares,
  listProjects,
  listWorks,
} from "./api";
import { BrandMark } from "./BrandMark";
import { EditProjectDialog } from "./EditProjectDialog";
import {
  CheckIcon,
  FolderIcon,
  MoreIcon,
  SearchIcon,
  SlidersIcon,
} from "./Icons";
import { NewWorkDialog } from "./NewWorkDialog";
import { SharedFoldersSection } from "./SharedFoldersSection";
import { Switcher, type SwitcherItem } from "./Switcher";
import { ThemeToggle } from "./ThemeToggle";

type Tab = "active" | "completed";

export function ProjectScreen({ projectSlug }: { projectSlug: string }) {
  const [project, setProject] = useState<ProjectDetail | null>(null);
  const [works, setWorks] = useState<WorkSummary[]>([]);
  const [allProjects, setAllProjects] = useState<ProjectSummary[]>([]);
  const [shares, setShares] = useState<SharedFolderSummary[]>([]);
  const [error, setError] = useState<string | null>(null);
  const [tab, setTab] = useState<Tab>("active");
  const [workDialogOpen, setWorkDialogOpen] = useState(false);
  const [editDialogOpen, setEditDialogOpen] = useState(false);
  const [projectSwitcherOpen, setProjectSwitcherOpen] = useState(false);
  const [workSwitcherOpen, setWorkSwitcherOpen] = useState(false);
  const [sharedFoldersOpen, setSharedFoldersOpen] = useState(false);

  async function refresh() {
    try {
      const [p, allWorks, projects, projectShares] = await Promise.all([
        getProject(projectSlug),
        listWorks(),
        listProjects(),
        listProjectShares(projectSlug),
      ]);
      setProject(p);
      setWorks(allWorks.filter((w) => w.project_slug === projectSlug));
      setAllProjects(projects);
      setShares(projectShares);
      setError(null);
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err));
    }
  }

  useEffect(() => {
    refresh();
  }, [projectSlug]);

  // Shortcuts:
  //   N       → new work (in this project)
  //   Shift+W → switch to another work within this project
  //   Shift+P → switch project
  useEffect(() => {
    function onKey(e: KeyboardEvent) {
      if (workDialogOpen || editDialogOpen) return;
      if (projectSwitcherOpen || workSwitcherOpen) return;
      if (sharedFoldersOpen) return;
      if (e.metaKey || e.ctrlKey || e.altKey) return;
      const target = e.target as HTMLElement | null;
      const tag = target?.tagName;
      if (tag === "INPUT" || tag === "TEXTAREA" || target?.isContentEditable) return;
      if (e.shiftKey && (e.key === "W" || e.key === "w")) {
        e.preventDefault();
        setWorkSwitcherOpen(true);
      } else if (e.shiftKey && (e.key === "P" || e.key === "p")) {
        e.preventDefault();
        setProjectSwitcherOpen(true);
      } else if (!e.shiftKey && (e.key === "n" || e.key === "N")) {
        e.preventDefault();
        setWorkDialogOpen(true);
      }
    }
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [
    workDialogOpen,
    editDialogOpen,
    projectSwitcherOpen,
    workSwitcherOpen,
    sharedFoldersOpen,
  ]);

  async function handleCreateWork(payload: CreateWorkPayload) {
    // Re-assert project_slug here so a stale dialog prop can't leak through.
    await createWork({ ...payload, project_slug: projectSlug });
    await refresh();
    setWorkDialogOpen(false);
  }

  const activeCount = works.filter((w) => w.status === "active").length;
  const completedCount = works.filter((w) => w.status === "completed").length;

  const filtered = useMemo(() => {
    const list = works.filter((w) => w.status === tab);
    return [...list].sort((a, b) => b.created_at.localeCompare(a.created_at));
  }, [works, tab]);

  // Switcher rows.
  const projectItems = useMemo<SwitcherItem[]>(() => {
    const sorted = [...allProjects].sort((a, b) => {
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
  }, [allProjects]);

  const workItems = useMemo<SwitcherItem[]>(() => {
    if (!project) return [];
    return [...works]
      .sort((a, b) => {
        if (a.status !== b.status) return a.status === "active" ? -1 : 1;
        return b.created_at.localeCompare(a.created_at);
      })
      .map((w) => ({
        slug: w.slug,
        name: w.name,
        subtitle:
          w.status === "completed" ? `${project.name} · completed` : project.name,
        glyph: project.glyph,
        hue: project.color,
        href: `/works/${w.slug}`,
      }));
  }, [works, project]);

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

  const defaultsCount =
    (project.default_jira_conn ? 1 : 0) +
    (project.default_sentry_conn ? 1 : 0);

  return (
    <div
      className="shell-v3 project-v3"
      style={{
        ["--proj-h" as string]: String(project.color),
        ["--proj-color" as string]: `oklch(0.62 0.16 ${project.color})`,
        ["--proj-soft" as string]: `oklch(0.62 0.16 ${project.color} / 0.10)`,
      }}
    >
      {/* LEFT — rail: crown, crumbs, hero, stats, defaults, shared folders, actions */}
      <aside className="shell-left proj-rail">
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
              onClick={() => setProjectSwitcherOpen(true)}
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
          <span className="sep">/</span>
          <span className="now">{project.slug}</span>
        </div>

        <div className="scrolly">
          <div className="hero-block">
            <div className="hero-glyph">{project.glyph}</div>
            <div className="hero-text">
              <div className="hero-id">{project.slug}</div>
              <h1 className="hero-name">{project.name}</h1>
              {project.description && (
                <div className="hero-desc">{project.description}</div>
              )}
            </div>
          </div>

          <div className="stats">
            <div className="stat-cell">
              <div className="lbl">Active</div>
              <div className="val">{activeCount}</div>
            </div>
            <div className="stat-cell">
              <div className="lbl">Completed</div>
              <div className="val muted">{completedCount}</div>
            </div>
          </div>

          <div className="v3-shd">
            <span>
              Default connections{" "}
              <span className="num" style={{ marginLeft: 8 }}>
                {defaultsCount}
              </span>
            </span>
            <span className="right">
              <a href="/settings/connections">manage ↗</a>
            </span>
          </div>
          <div className="defaults">
            <div className="row">
              {project.default_jira_conn && (
                <span className="conn-mini" data-source="jira">
                  <span className="ico">JI</span> {project.default_jira_conn}
                </span>
              )}
              {project.default_sentry_conn && (
                <span className="conn-mini" data-source="sentry">
                  <span className="ico">SE</span> {project.default_sentry_conn}
                </span>
              )}
              {defaultsCount === 0 && (
                <span
                  className="hint"
                  style={{ fontFamily: "var(--font-mono)", fontSize: 11 }}
                >
                  none configured
                </span>
              )}
            </div>
          </div>

          <div className="v3-shd">
            <span>
              Shared folders{" "}
              <span className="num" style={{ marginLeft: 8 }}>
                {shares.length}
              </span>
            </span>
            <span className="right">
              <button onClick={() => setSharedFoldersOpen(true)}>
                + add
              </button>
            </span>
          </div>
          {shares.length === 0 && (
            <div className="v3-empty" style={{ paddingTop: 4 }}>
              none
            </div>
          )}
          {shares.map((s) => (
            <button
              key={s.slug}
              className="v3-folder-row compact"
              onClick={() => setSharedFoldersOpen(true)}
              title={s.canonical_path}
            >
              <span className="ico">
                <FolderIcon size={12} />
              </span>
              <span className="body">
                <span className="lbl">
                  <span>{s.name}</span>
                  {s.is_custom_location && <span className="tag">custom</span>}
                </span>
                <span className="path">./{s.mount_path}/</span>
              </span>
              <span className="more">
                <MoreIcon size={12} />
              </span>
            </button>
          ))}

          <div style={{ height: 12 }} />
        </div>

        <div
          className="actions"
          style={{ borderTop: "1px solid var(--line-soft)", paddingTop: 12 }}
        >
          <button
            className="btn primary"
            onClick={() => setWorkDialogOpen(true)}
          >
            + New work <span className="kbd" style={{ marginLeft: 8 }}>N</span>
          </button>
          <button className="btn" onClick={() => setEditDialogOpen(true)}>
            Edit project
          </button>
        </div>

        <div className="v3-footstrip">
          <span className="seg">
            <span className="dot live" />
            {activeCount} active
          </span>
          <span className="seg">{completedCount} done</span>
        </div>
      </aside>

      {/* RIGHT — tabs + work list */}
      <main className="shell-right proj-right">
        <div className="head">
          <div>
            <div className="title">Latest work</div>
            <div className="sub">
              {works.length} {works.length === 1 ? "unit" : "units"} in{" "}
              {project.name}
            </div>
          </div>
        </div>
        <div className="tabs">
          <button
            className={"tab" + (tab === "active" ? " active" : "")}
            onClick={() => setTab("active")}
          >
            active<span className="count">{activeCount}</span>
          </button>
          <button
            className={"tab" + (tab === "completed" ? " active" : "")}
            onClick={() => setTab("completed")}
          >
            completed<span className="count">{completedCount}</span>
          </button>
          <span className="spacer" />
        </div>

        <div className="body">
          <button className="v3-add-row" onClick={() => setWorkDialogOpen(true)}>
            <span className="marker">+</span> start new work
            <span style={{ color: "var(--fg-4)", marginLeft: 6 }}>
              · in {project.name}
            </span>
            <span className="kbd">N</span>
          </button>
          {filtered.length === 0 && (
            <div className="v3-empty">nothing {tab} here yet.</div>
          )}
          {filtered.map((w) => (
            <V3WorkRow key={w.slug} work={w} />
          ))}
          <div style={{ height: 40 }} />
        </div>
      </main>

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
            window.location.href = "/";
          }}
        />
      )}
      {projectSwitcherOpen && (
        <Switcher
          placeholder="Switch to project…"
          items={projectItems}
          onClose={() => setProjectSwitcherOpen(false)}
          emptyMessage="No projects yet"
        />
      )}
      {workSwitcherOpen && (
        <Switcher
          placeholder={`Switch work in ${project.name}…`}
          items={workItems}
          onClose={() => setWorkSwitcherOpen(false)}
          emptyMessage="No work in this project"
        />
      )}
      {sharedFoldersOpen && (
        <SharedFoldersManagerDialog
          projectSlug={project.slug}
          projectName={project.name}
          onClose={() => {
            setSharedFoldersOpen(false);
            // Refresh shares so any add/edit/delete reflects in the rail.
            refresh();
          }}
        />
      )}
    </div>
  );
}

function V3WorkRow({ work }: { work: WorkSummary }) {
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
      <span />
    </a>
  );
}

// Wraps the existing SharedFoldersSection (which manages all CRUD)
// in a lightweight modal frame. The section's own header/empty UI
// stays — it's the canonical management surface; the rail rows are
// just a read-only preview.
function SharedFoldersManagerDialog({
  projectSlug,
  projectName,
  onClose,
}: {
  projectSlug: string;
  projectName: string;
  onClose: () => void;
}) {
  useEffect(() => {
    function onKey(e: KeyboardEvent) {
      if (e.key === "Escape") onClose();
    }
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [onClose]);
  return (
    <div
      className="scrim"
      onClick={(e) => {
        if (e.target === e.currentTarget) onClose();
      }}
    >
      <div className="modal modal-lg">
        <div className="modal-hd">
          <div>
            <h3>Shared folders</h3>
            <div className="sub">
              Project: <strong>{projectName}</strong>
            </div>
          </div>
          <button
            className="btn-ghost-sm"
            onClick={onClose}
            aria-label="Close"
          >
            ×
          </button>
        </div>
        <div className="modal-bd">
          <SharedFoldersSection
            projectSlug={projectSlug}
            projectName={projectName}
          />
        </div>
        <div className="modal-ft">
          <button className="btn" onClick={onClose}>
            Done
          </button>
        </div>
      </div>
    </div>
  );
}

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
