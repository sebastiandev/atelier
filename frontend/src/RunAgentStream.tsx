import { useEffect, useMemo, useState } from "react";

import { groupEvents, TranscriptUnits } from "./AgentTile";
import { shortenPath } from "./pathFormat";
import type { AgentEvent, ConnectionStatus } from "./useAgentStream";

type ActivityKind = "command" | "edit" | "read" | "status";

export type RunActivityLine = {
  at: number;
  complete: boolean;
  key: string;
  kind: ActivityKind;
  label: string;
  target: string;
};

type TrackedTool = {
  kind: ActivityKind;
  line: RunActivityLine;
};

/** Derive the compact run activity feed from the agent transcript. */
export function runActivityLines(events: AgentEvent[]): RunActivityLine[] {
  const lines: RunActivityLine[] = [];
  const tools = new Map<string, TrackedTool>();

  for (const event of events) {
    if (event.type === "tool_call") {
      const next = toolActivity(event);
      if (!next) continue;
      const line = appendActivity(lines, next);
      const toolId = text(event.tool_id);
      if (toolId) tools.set(toolId, { kind: next.kind, line });
      continue;
    }

    if (event.type === "tool_call_update") {
      const toolId = text(event.tool_id);
      const tracked = tools.get(toolId);
      if (tracked) {
        const target = eventPath(event);
        if (target && tracked.kind !== "command" && tracked.kind !== "status") {
          tracked.line.target = `${tracked.kind}:${target}`;
          tracked.line.label = activityLabel(tracked.kind, target);
        }
        tracked.line.at = eventTime(event);
      } else {
        const next = toolActivity(event);
        if (next) {
          const line = appendActivity(lines, next);
          if (toolId) tools.set(toolId, { kind: next.kind, line });
        }
      }
      continue;
    }

    if (event.type === "tool_result") {
      const tracked = tools.get(text(event.tool_id));
      if (tracked) {
        tracked.line.at = eventTime(event);
        tracked.line.complete = true;
      }
      continue;
    }

    if (event.type === "status_change") {
      const status = clean(text(event.status));
      if (!status) continue;
      appendActivity(lines, {
        at: eventTime(event),
        complete: status === "idle" || status === "stopped",
        key: `${event.seq}:status:${status}`,
        kind: "status",
        label: status,
        target: `status:${status}`,
      });
    }
  }

  return lines.slice(-3);
}

/** Last-three live activity window for a running agent stage. */
export function RunLiveActivity({
  events,
  running = true,
}: {
  events: AgentEvent[];
  running?: boolean;
}) {
  const lines = useMemo(() => runActivityLines(events), [events]);
  const [startedAt] = useState(() => Date.now());
  const [now, setNow] = useState(() => Date.now());

  useEffect(() => {
    if (!running) return;
    const timer = window.setInterval(() => setNow(Date.now()), 1_000);
    return () => window.clearInterval(timer);
  }, [running]);

  const current = lines.at(-1);
  const elapsed = elapsedSeconds(current?.at ?? startedAt, now);
  const silent = running && (!current || elapsed > 10);

  return (
    <section className="run-live-activity" aria-label="Live agent activity">
      <header className="run-live-activity-head">
        <strong>Live activity</strong>
        <span>from the agent status stream</span>
      </header>
      <div className="run-live-activity-lines" aria-live="polite">
        {lines.slice(0, -1).map((line) => (
          <div className="run-live-activity-line dim" key={line.key}>
            <span aria-hidden>{line.complete ? "✓" : "·"}</span>
            <span>{line.label}</span>
            <time>{formatElapsed(elapsedSeconds(line.at, now))}</time>
          </div>
        ))}
        <div className="run-live-activity-line current">
          {running && <span className="run-live-dot" aria-hidden />}
          {!running && <span aria-hidden>{current?.complete ? "✓" : "·"}</span>}
          <span>{silent ? "working…" : current?.label ?? "waiting for activity…"}</span>
          <time>{formatElapsed(elapsed)}</time>
        </div>
      </div>
    </section>
  );
}

/** Read-only dock showing the full transcript already replayed by useAgentStream. */
export function RunTranscriptDock({
  agentSlug,
  endSeq = null,
  events,
  onClose,
  startSeq = 0,
  status,
  title = "Agent transcript",
}: {
  agentSlug: string;
  endSeq?: number | null;
  events: AgentEvent[];
  onClose: () => void;
  startSeq?: number;
  status?: ConnectionStatus;
  title?: string;
}) {
  const units = useMemo(() => groupEvents(events.filter((event) => (
    event.seq > startSeq && (endSeq === null || event.seq <= endSeq)
  ))), [endSeq, events, startSeq]);

  return (
    <aside className="run-transcript-dock" aria-label={title}>
      <header className="run-transcript-dock-head">
        <div>
          <strong>{title}</strong>
          <span className="mono">{agentSlug}</span>
        </div>
        <div className="run-transcript-dock-actions">
          <span className="tag">read-only</span>
          {status && <span className="mono dim">{status}</span>}
          <button
            type="button"
            className="btn icon sm ghost"
            aria-label="Close transcript"
            title="Close transcript"
            onClick={onClose}
          >
            ×
          </button>
        </div>
      </header>
      <div className="transcript run-transcript-dock-body themed-scrollbar">
        {units.length > 0 ? (
          <TranscriptUnits units={units} agentSlug={agentSlug} />
        ) : (
          <div className="msg msg-status">Waiting for transcript…</div>
        )}
      </div>
    </aside>
  );
}

function appendActivity(
  lines: RunActivityLine[],
  next: RunActivityLine,
): RunActivityLine {
  const previous = lines.at(-1);
  if (previous?.target === next.target) {
    previous.at = next.at;
    previous.complete = next.complete;
    previous.label = next.label;
    return previous;
  }
  lines.push(next);
  return next;
}

function toolActivity(event: AgentEvent): RunActivityLine | null {
  const name = clean(text(event.name));
  const protocolKind = clean(text(event.kind)).toLowerCase();
  const path = eventPath(event);
  const args = record(event.arguments);
  const nameLower = name.toLowerCase();

  let kind: ActivityKind;
  let target: string;
  if (
    protocolKind === "read" ||
    nameLower === "read" ||
    nameLower === "grep" ||
    nameLower === "glob"
  ) {
    kind = "read";
    target = path || clean(text(args.pattern)) || name || "provider";
  } else if (
    protocolKind === "edit" ||
    nameLower === "edit" ||
    nameLower === "multiedit" ||
    nameLower === "write"
  ) {
    kind = "edit";
    target = path || name || "provider";
  } else if (
    protocolKind === "execute" ||
    nameLower === "bash" ||
    nameLower === "exec"
  ) {
    kind = "command";
    target = command(args) || clean(text(event.title)) || name || "provider";
  } else {
    const fallback = clean(text(event.title)) || protocolKind || name;
    if (!fallback) return null;
    kind = "status";
    target = fallback;
  }

  return {
    at: eventTime(event),
    complete: false,
    key: `${event.seq}:${kind}:${target}`,
    kind,
    label: activityLabel(kind, target),
    target: `${kind}:${target}`,
  };
}

function activityLabel(kind: ActivityKind, target: string): string {
  if (kind === "command") return `$ ${target}`;
  if (kind === "edit") return `editing ${shortenPath(target)}`;
  if (kind === "read") return `read ${shortenPath(target)}`;
  return target;
}

function eventPath(event: AgentEvent): string {
  const locations = event.locations;
  if (Array.isArray(locations)) {
    for (const location of locations) {
      const path = text(record(location).path);
      if (path) return clean(path);
    }
  }
  const args = record(event.arguments);
  return clean(text(args.path) || text(args.file_path) || text(args.filePath));
}

function command(args: Record<string, unknown>): string {
  const value = text(args.command) || text(args.cmd);
  if (value) return clean(value);
  return Array.isArray(args.argv) ? clean(args.argv.map(String).join(" ")) : "";
}

function eventTime(event: AgentEvent): number {
  const parsed = Date.parse(event.ts);
  return Number.isFinite(parsed) ? parsed : 0;
}

function elapsedSeconds(at: number, now: number): number {
  return at > 0 ? Math.max(0, Math.floor((now - at) / 1_000)) : 0;
}

function formatElapsed(seconds: number): string {
  if (seconds < 60) return `${seconds}s`;
  return `${Math.floor(seconds / 60)}m ${seconds % 60}s`;
}

function clean(value: string): string {
  return value.replace(/\s+/g, " ").trim();
}

function record(value: unknown): Record<string, unknown> {
  return value && typeof value === "object"
    ? (value as Record<string, unknown>)
    : {};
}

function text(value: unknown): string {
  return typeof value === "string" ? value : "";
}
