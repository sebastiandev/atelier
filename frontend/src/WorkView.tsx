import { useEffect, useRef, useState } from "react";

import { AgentTile } from "./AgentTile";
import {
  type AgentSummary,
  type CreateAgentPayload,
  type WorkDetail,
  PERSONA_GLYPH,
  createAgent,
  detachAgent,
  getWork,
  listAgents,
  revealAgent,
  revealWork,
} from "./api";
import { NewAgentDialog } from "./NewAgentDialog";
import { useClosedStore } from "./state/closed";
import { useTweaksStore } from "./state/tweaks";
import { ThemeToggle } from "./ThemeToggle";
import { TweaksToggle } from "./TweaksPanel";

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
  // Transient banner for the detach flow — fades out after 4s. Used both
  // for "launched in Terminal" success and "couldn't launch — command
  // copied to clipboard" fallback. One slot is plenty: detaches happen
  // one at a time and overlapping toasts would just compete for room.
  const [toast, setToast] = useState<string | null>(null);
  const tileRefs = useRef<Map<string, HTMLDivElement>>(new Map());
  const closedSlugs = useClosedStore((s) => s.byWork[workSlug] ?? NO_CLOSED);
  const closeAgent = useClosedStore((s) => s.close);
  const restoreAgent = useClosedStore((s) => s.restore);
  const layout = useTweaksStore((s) => s.layout);

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

  const canvasAgents = agents.filter((a) => !closedSlugs.includes(a.slug));
  // STORY-024 implements the actual freeform drag for "windows"; until
  // then we render windows-mode as tiles so the layout choice persists
  // and the radio thumb sits correctly.
  const effectiveLayout = layout === "windows" ? "tiles" : layout;
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
              const isDetached = a.status === "detached";
              const tooltip = isDetached
                ? "Detached to CLI — click to re-attach (Atelier merges any CLI activity into the transcript)"
                : isClosed
                  ? "Closed — click to reopen"
                  : undefined;
              return (
                <button
                  key={a.slug}
                  className={
                    "rail-agent" +
                    (focusedSlug === a.slug ? " focused" : "") +
                    (isClosed ? " minimized" : "") +
                    (isDetached ? " detached" : "")
                  }
                  data-persona={a.persona}
                  onClick={() => focusAgent(a.slug)}
                  title={tooltip}
                >
                  <span className="pip">{PERSONA_GLYPH[a.persona] ?? "AG"}</span>
                  <span className="meta">
                    <span className="name mono">{a.name}</span>
                    <span className="role">
                      {isDetached ? "in CLI · click to re-attach" : a.role}
                    </span>
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

          <div className="canvas" data-cols={cols} data-layout={effectiveLayout}>
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
      {toast && (
        <div className="toast" role="status" aria-live="polite">
          {toast}
        </div>
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
