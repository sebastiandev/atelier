import { useEffect, useRef, useState } from "react";

import { AgentTile } from "./AgentTile";
import {
  type AgentSummary,
  type CreateAgentPayload,
  type WorkDetail,
  PERSONA_GLYPH,
  createAgent,
  getWork,
  listAgents,
} from "./api";
import { NewAgentDialog } from "./NewAgentDialog";
import { useClosedStore } from "./state/closed";
import { ThemeToggle } from "./ThemeToggle";

// Stable singleton so the selector below doesn't return a fresh ref on
// every render — Zustand's default Object.is snapshot check would
// otherwise treat each `[]` as a change and re-render in a loop.
const NO_CLOSED: readonly string[] = [];

export function WorkView({ workSlug }: { workSlug: string }) {
  const [work, setWork] = useState<WorkDetail | null>(null);
  const [agents, setAgents] = useState<AgentSummary[]>([]);
  const [error, setError] = useState<string | null>(null);
  const [focusedSlug, setFocusedSlug] = useState<string | null>(null);
  const [agentDialogOpen, setAgentDialogOpen] = useState(false);
  const tileRefs = useRef<Map<string, HTMLDivElement>>(new Map());
  const closedSlugs = useClosedStore((s) => s.byWork[workSlug] ?? NO_CLOSED);
  const closeAgent = useClosedStore((s) => s.close);
  const restoreAgent = useClosedStore((s) => s.restore);

  useEffect(() => {
    let cancelled = false;
    Promise.all([getWork(workSlug), listAgents(workSlug)])
      .then(([w, a]) => {
        if (cancelled) return;
        setWork(w);
        setAgents(a);
        if (a.length > 0) setFocusedSlug(a[0].slug);
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
    setAgentDialogOpen(false);
    setFocusedSlug(created.slug);
    requestAnimationFrame(() => {
      const el = tileRefs.current.get(created.slug);
      if (el) el.scrollIntoView({ behavior: "smooth", block: "start" });
    });
    return next;
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

  const canvasAgents = agents.filter((a) => !closedSlugs.includes(a.slug));
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
          <span className="sep">/</span>
          <span className="now mono">{work.slug}</span>
        </span>
        <span className="hint" style={{ marginLeft: "0.5rem" }}>
          {work.name}
        </span>
        <div className="spacer" />
        <span className="folder-pill mono" title={work.folder}>
          {shortenPath(work.folder)}
        </span>
        <ThemeToggle />
      </header>

      <div className="work-body">
        <aside className="left-rail">
          <RailSection title="Active agents" count={agents.length}>
            {agents.length === 0 && (
              <div className="hint" style={{ padding: "4px 8px" }}>
                None on the canvas. Launch one via the API.
              </div>
            )}
            {agents.map((a) => {
              const isClosed = closedSlugs.includes(a.slug);
              return (
                <button
                  key={a.slug}
                  className={
                    "rail-agent" +
                    (focusedSlug === a.slug ? " focused" : "") +
                    (isClosed ? " minimized" : "")
                  }
                  data-persona={a.persona}
                  onClick={() => focusAgent(a.slug)}
                  title={isClosed ? "Closed — click to reopen" : undefined}
                >
                  <span className="pip">{PERSONA_GLYPH[a.persona] ?? "AG"}</span>
                  <span className="meta">
                    <span className="name mono">{a.name}</span>
                    <span className="role">{a.role}</span>
                  </span>
                  <span className="status-dot" data-status={a.status} />
                </button>
              );
            })}
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
              + New agent
            </button>
          </div>

          <div className="canvas" data-cols={cols}>
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
            {canvasAgents.map((a) => (
              <div
                key={a.slug}
                ref={(el) => {
                  if (el) tileRefs.current.set(a.slug, el);
                  else tileRefs.current.delete(a.slug);
                }}
                className={"canvas-cell" + (focusedSlug === a.slug ? " focused" : "")}
                data-persona={a.persona}
                onMouseDown={() => setFocusedSlug(a.slug)}
              >
                <AgentTile
                  agentSlug={a.slug}
                  mode="tile"
                  persona={a.persona}
                  agentName={a.name}
                  onClose={() => {
                    closeAgent(workSlug, a.slug);
                    if (focusedSlug === a.slug) setFocusedSlug(null);
                  }}
                />
              </div>
            ))}
          </div>
        </div>
      </div>
      {agentDialogOpen && (
        <NewAgentDialog
          workSlug={work.slug}
          workName={work.name}
          onClose={() => setAgentDialogOpen(false)}
          onCreate={async (payload) => {
            await handleCreateAgent(payload);
          }}
        />
      )}
    </div>
  );
}

function RailSection({
  title,
  count,
  children,
}: {
  title: string;
  count: number;
  children: React.ReactNode;
}) {
  return (
    <div className="rail-section">
      <div className="rail-hd">
        <span>{title}</span>
        <span className="count mono">{count}</span>
      </div>
      <div className="rail-section-body">{children}</div>
    </div>
  );
}

function shortenPath(p: string): string {
  if (!p) return "";
  const parts = p.split("/");
  if (parts.length <= 3) return p;
  return [parts[0], "…", ...parts.slice(-2)].join("/");
}
