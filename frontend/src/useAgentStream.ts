import { useEffect, useMemo, useRef, useState } from "react";

import type { ContextEntry } from "./api";

export type AgentEvent = {
  seq: number;
  type: string;
  ts: string;
  [key: string]: unknown;
};

export type PermissionDecision = "allow" | "allow_always" | "deny";

// ACP agents name their own answer options (kind ∈ allow_once |
// allow_always | reject_once | reject_always). Absent for providers
// that only speak Atelier's three-way decision.
export type PermissionOptionInfo = {
  option_id: string;
  name: string;
  kind: string;
};

export type PendingPermission = {
  request_id: string;
  tool_name: string;
  tool_input: Record<string, unknown>;
  ts: string;
  seq: number;
  options?: PermissionOptionInfo[];
};

export type PendingHandoff = {
  new_thread_id: string;
  ts: string;
  seq: number;
};

export type ConnectionStatus =
  | "connecting"
  | "connected"
  | "closed"
  | "stopped"
  | "error";

// Backend WS close code when the supervisor has no live adapter for the
// requested agent slug (e.g. backend was restarted after the agent ran).
// Treat as terminal — don't retry, let the UI surface "stopped".
const CLOSE_CODE_AGENT_NOT_RUNNING = 4404;

// Exponential reconnect schedule: 1s → 2s → 4s → 8s → 16s → 30s (cap).
// Resets to 0 on a successful connect so transient blips don't push us
// to the cap.
const RETRY_DELAYS_MS = [1000, 2000, 4000, 8000, 16000, 30000];
const STREAM_EVENT_FLUSH_MS = 50;

function delayForAttempt(attempt: number): number {
  return RETRY_DELAYS_MS[Math.min(attempt, RETRY_DELAYS_MS.length - 1)];
}

/**
 * Subscribe to an agent's WS stream. Reconnects on transient close with
 * exponential backoff, resuming from the last seen seq via `?cursor=N` so
 * the server replays missed events from `transcript.ndjson` before going
 * live. A 4404 close (supervisor doesn't know this agent) is terminal —
 * the UI surfaces "stopped" instead of retrying.
 *
 * On mount the cursor starts at 0 — the transcript is the source of truth
 * and the server-side replay is what populates `events`. We don't persist
 * the cursor across mounts because we don't persist the events either:
 * starting non-zero on a fresh mount would yield an empty tile.
 */
export type StreamResource = "agents" | "chats";

export function useAgentStream(
  agentSlug: string,
  options: { resource?: StreamResource } = {},
) {
  const resource = options.resource ?? "agents";
  const [events, setEvents] = useState<AgentEvent[]>([]);
  const [status, setStatus] = useState<ConnectionStatus>("connecting");
  const wsRef = useRef<WebSocket | null>(null);
  const lastSeqRef = useRef(0);
  const retryAttemptRef = useRef(0);
  const connectionIdRef = useRef(0);
  const pendingEventsRef = useRef<AgentEvent[]>([]);
  const flushHandleRef = useRef<number | null>(null);

  useEffect(() => {
    let cancelled = false;
    let retryHandle: number | null = null;

    function isCurrentConnection(ws: WebSocket, connectionId: number): boolean {
      return (
        !cancelled &&
        wsRef.current === ws &&
        connectionIdRef.current === connectionId
      );
    }

    function clearRetry() {
      if (retryHandle !== null) {
        window.clearTimeout(retryHandle);
        retryHandle = null;
      }
    }

    function clearPendingFlush() {
      if (flushHandleRef.current !== null) {
        window.clearTimeout(flushHandleRef.current);
        flushHandleRef.current = null;
      }
    }

    function flushPendingEvents() {
      clearPendingFlush();
      const pending = pendingEventsRef.current;
      if (pending.length === 0) return;
      pendingEventsRef.current = [];
      setEvents((prev) => [...prev, ...pending]);
    }

    function scheduleEventFlush() {
      if (flushHandleRef.current !== null) return;
      flushHandleRef.current = window.setTimeout(() => {
        flushHandleRef.current = null;
        if (cancelled) return;
        flushPendingEvents();
      }, STREAM_EVENT_FLUSH_MS);
    }

    // Reset on agent change / fresh mount. Cursor goes to 0 so the server
    // replays the full transcript; lastSeqRef advances as events arrive
    // and is what an in-session WS reconnect resumes from (no duplicates).
    clearPendingFlush();
    pendingEventsRef.current = [];
    setEvents([]);
    setStatus("connecting");
    lastSeqRef.current = 0;
    retryAttemptRef.current = 0;
    connectionIdRef.current += 1;

    function scheduleReconnect(connectionId: number) {
      clearRetry();
      const delay = delayForAttempt(retryAttemptRef.current);
      retryAttemptRef.current += 1;
      retryHandle = window.setTimeout(() => {
        retryHandle = null;
        if (cancelled || connectionIdRef.current !== connectionId) return;
        connect();
      }, delay);
    }

    function connect() {
      if (cancelled) return;
      clearRetry();
      const connectionId = connectionIdRef.current + 1;
      connectionIdRef.current = connectionId;
      const proto = window.location.protocol === "https:" ? "wss:" : "ws:";
      const url =
        `${proto}//${window.location.host}` +
        `/api/${resource}/${agentSlug}/stream` +
        `?cursor=${lastSeqRef.current}`;
      const ws = new WebSocket(url);
      wsRef.current = ws;

      ws.onopen = () => {
        if (!isCurrentConnection(ws, connectionId)) return;
        retryAttemptRef.current = 0;
        setStatus("connected");
      };

      ws.onmessage = (msg) => {
        if (!isCurrentConnection(ws, connectionId)) return;
        try {
          const event = JSON.parse(msg.data) as AgentEvent;
          if (typeof event.seq === "number") {
            lastSeqRef.current = event.seq;
          }
          pendingEventsRef.current.push(event);
          scheduleEventFlush();
        } catch {
          // Malformed frame — drop silently.
        }
      };

      ws.onclose = (ev) => {
        if (!isCurrentConnection(ws, connectionId)) return;
        flushPendingEvents();
        wsRef.current = null;
        if (ev.code === CLOSE_CODE_AGENT_NOT_RUNNING) {
          setStatus("stopped");
          return;
        }
        setStatus("closed");
        scheduleReconnect(connectionId);
      };

      ws.onerror = () => {
        if (!isCurrentConnection(ws, connectionId)) return;
        setStatus("error");
        ws.close();
      };
    }

    connect();

    return () => {
      cancelled = true;
      connectionIdRef.current += 1;
      clearRetry();
      clearPendingFlush();
      pendingEventsRef.current = [];
      wsRef.current?.close();
      wsRef.current = null;
    };
  }, [agentSlug, resource]);

  function sendInput(text: string, contexts?: ContextEntry[]) {
    const ws = wsRef.current;
    if (ws && ws.readyState === WebSocket.OPEN) {
      const frame: Record<string, unknown> = { type: "input", text };
      if (contexts && contexts.length > 0) frame.contexts = contexts;
      ws.send(JSON.stringify(frame));
    }
  }

  function sendStop() {
    const ws = wsRef.current;
    if (ws && ws.readyState === WebSocket.OPEN) {
      ws.send(JSON.stringify({ type: "stop" }));
    }
  }

  function sendPermission(request_id: string, decision: PermissionDecision) {
    const ws = wsRef.current;
    if (ws && ws.readyState === WebSocket.OPEN) {
      ws.send(JSON.stringify({ type: "permission", request_id, decision }));
    }
  }

  function sendSessionConfig(config_id: string, value: string | boolean) {
    const ws = wsRef.current;
    if (ws && ws.readyState === WebSocket.OPEN) {
      ws.send(JSON.stringify({ type: "session_config", config_id, value }));
    }
  }

  function sendSessionConfigRefresh(config_id: string) {
    const ws = wsRef.current;
    if (ws && ws.readyState === WebSocket.OPEN) {
      ws.send(JSON.stringify({ type: "session_config_refresh", config_id }));
    }
  }

  // Derive the open prompts from the event log. A `permission_request`
  // opens one; a matching `permission_decision` closes it. The transcript
  // is the source of truth, so this trivially survives WS reconnects —
  // replay rebuilds the same set the server sees.
  const pendingPermissions = useMemo(() => {
    const open = new Map<string, PendingPermission>();
    for (const ev of events) {
      if (ev.type === "permission_request" && typeof ev.request_id === "string") {
        open.set(ev.request_id, {
          request_id: ev.request_id,
          tool_name: typeof ev.tool_name === "string" ? ev.tool_name : "(unknown)",
          tool_input:
            ev.tool_input && typeof ev.tool_input === "object"
              ? (ev.tool_input as Record<string, unknown>)
              : {},
          ts: ev.ts,
          seq: ev.seq,
          options: Array.isArray(ev.options)
            ? (ev.options as PermissionOptionInfo[])
            : undefined,
        });
      } else if (
        ev.type === "permission_decision" &&
        typeof ev.request_id === "string"
      ) {
        open.delete(ev.request_id);
      }
    }
    return Array.from(open.values());
  }, [events]);

  // The latest unaccepted handoff (Amp auto-handoff). A `handoff_offered`
  // event opens one; a subsequent `handoff_accepted` (written by the
  // backend after the switch lands) clears it. Survives WS reconnects
  // for the same reason as `pendingPermissions`.
  const pendingHandoff = useMemo<PendingHandoff | null>(() => {
    let latest: PendingHandoff | null = null;
    for (const ev of events) {
      if (
        ev.type === "handoff_offered" &&
        typeof ev.new_thread_id === "string"
      ) {
        latest = {
          new_thread_id: ev.new_thread_id,
          ts: ev.ts,
          seq: ev.seq,
        };
      } else if (ev.type === "handoff_accepted") {
        latest = null;
      }
    }
    return latest;
  }, [events]);

  return {
    events,
    status,
    sendInput,
    sendStop,
    sendPermission,
    sendSessionConfig,
    sendSessionConfigRefresh,
    pendingPermissions,
    pendingHandoff,
  };
}
