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

export function WorkView({ workSlug }: { workSlug: string }) {
  const [work, setWork] = useState<WorkDetail | null>(null);
  const [agents, setAgents] = useState<AgentSummary[]>([]);
  const [error, setError] = useState<string | null>(null);
  const [focusedSlug, setFocusedSlug] = useState<string | null>(null);
  const [agentDialogOpen, setAgentDialogOpen] = useState(false);
  const tileRefs = useRef<Map<string, HTMLDivElement>>(new Map());

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
    setFocusedSlug(slug);
    const el = tileRefs.current.get(slug);
    if (el) el.scrollIntoView({ behavior: "smooth", block: "start" });
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

  const cols = agents.length <= 1 ? 1 : agents.length === 2 ? 2 : agents.length <= 4 ? 2 : 3;

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
      </header>

      <div className="work-body">
        <aside className="left-rail">
          <RailSection title="Active agents" count={agents.length}>
            {agents.length === 0 && (
              <div className="hint" style={{ padding: "4px 8px" }}>
                None on the canvas. Launch one via the API.
              </div>
            )}
            {agents.map((a) => (
              <button
                key={a.slug}
                className={"rail-agent" + (focusedSlug === a.slug ? " focused" : "")}
                data-persona={a.persona}
                onClick={() => focusAgent(a.slug)}
              >
                <span className="pip">{PERSONA_GLYPH[a.persona] ?? "AG"}</span>
                <span className="meta">
                  <span className="name mono">{a.name}</span>
                  <span className="role">{a.role}</span>
                </span>
                <span className="status-dot" data-status={a.status} />
              </button>
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
              + New agent
            </button>
          </div>

          <div className="canvas" data-cols={cols}>
            {agents.length === 0 && (
              <div className="canvas-empty">
                <div className="em-title">No agents on the canvas</div>
                <div className="em-sub">
                  Launch one with{" "}
                  <code>POST /api/works/{work.slug}/agents</code>.
                </div>
              </div>
            )}
            {agents.map((a) => (
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
