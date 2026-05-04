import { useEffect, useState } from "react";

import { type CreateWorkPayload, type WorkSummary, createWork, listWorks } from "./api";
import { NewWorkDialog } from "./NewWorkDialog";
import { ThemeToggle } from "./ThemeToggle";
import { TweaksToggle } from "./TweaksPanel";

type Tab = "active" | "completed";

export function Home() {
  const [works, setWorks] = useState<WorkSummary[]>([]);
  const [loadError, setLoadError] = useState<string | null>(null);
  const [tab, setTab] = useState<Tab>("active");
  const [dialogOpen, setDialogOpen] = useState(false);

  async function refresh() {
    try {
      setWorks(await listWorks());
      setLoadError(null);
    } catch (err) {
      setLoadError(err instanceof Error ? err.message : String(err));
    }
  }

  useEffect(() => {
    refresh();
  }, []);

  useEffect(() => {
    function onKey(e: KeyboardEvent) {
      if (dialogOpen) return;
      if (e.key !== "n" && e.key !== "N") return;
      // Don't hijack Cmd+N / Ctrl+N (new browser window) or Alt+N.
      if (e.metaKey || e.ctrlKey || e.altKey) return;
      const target = e.target as HTMLElement | null;
      const tag = target?.tagName;
      if (tag === "INPUT" || tag === "TEXTAREA" || target?.isContentEditable) return;
      e.preventDefault();
      setDialogOpen(true);
    }
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [dialogOpen]);

  async function handleCreate(payload: CreateWorkPayload) {
    await createWork(payload);
    await refresh();
    setTab("active");
    setDialogOpen(false);
  }

  const activeCount = works.filter((w) => w.status === "active").length;
  const completedCount = works.filter((w) => w.status === "completed").length;
  const filtered = works.filter((w) => w.status === tab);

  return (
    <div className="home">
      <header className="topbar">
        <div className="brand">
          <span className="brand-mark" />
          Atelier
        </div>
        <div className="spacer" />
        <a className="btn-ghost-sm" href="/connections">
          Connections
        </a>
        <span className="hint mono">
          {activeCount} active
        </span>
        <TweaksToggle />
        <ThemeToggle />
      </header>

      <div className="home-hd">
        <div>
          <h1>Your work</h1>
          <p className="tagline">
            Each work unit is a goal, a folder, and the agents working on it.
          </p>
        </div>
        <button className="btn primary" onClick={() => setDialogOpen(true)}>
          + New work <span className="kbd">N</span>
        </button>
      </div>

      <div className="home-tabs">
        <TabButton active={tab === "active"} onClick={() => setTab("active")}>
          Active <span className="count mono">{activeCount}</span>
        </TabButton>
        <TabButton active={tab === "completed"} onClick={() => setTab("completed")}>
          Completed <span className="count mono">{completedCount}</span>
        </TabButton>
      </div>

      {loadError && <div className="form-error">{loadError}</div>}

      <div className="home-grid">
        <button className="work-card create" onClick={() => setDialogOpen(true)}>
          <span className="plus">+</span>
          <span className="create-title">Start new work</span>
          <span className="hint">Brief, name, optional folder.</span>
        </button>
        {filtered.map((w) => (
          <WorkCard key={w.slug} work={w} />
        ))}
        {filtered.length === 0 && tab === "completed" && (
          <div className="empty hint">No completed work yet.</div>
        )}
      </div>

      {dialogOpen && (
        <NewWorkDialog
          onClose={() => setDialogOpen(false)}
          onCreate={handleCreate}
        />
      )}
    </div>
  );
}

function TabButton({
  active,
  onClick,
  children,
}: {
  active: boolean;
  onClick: () => void;
  children: React.ReactNode;
}) {
  return (
    <button className={"home-tab" + (active ? " active" : "")} onClick={onClick}>
      {children}
    </button>
  );
}

function WorkCard({ work }: { work: WorkSummary }) {
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
      <div className="wc-folder mono">{work.folder}</div>
    </a>
  );
}

function formatDate(iso: string): string {
  const d = new Date(iso);
  if (isNaN(d.getTime())) return iso;
  return d.toLocaleDateString(undefined, { month: "short", day: "numeric" });
}
