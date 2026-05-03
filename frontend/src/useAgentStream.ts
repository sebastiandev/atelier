import { useEffect, useRef, useState } from "react";

export type AgentEvent = {
  seq: number;
  type: string;
  ts: string;
  [key: string]: unknown;
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

function delayForAttempt(attempt: number): number {
  return RETRY_DELAYS_MS[Math.min(attempt, RETRY_DELAYS_MS.length - 1)];
}

/**
 * Subscribe to an agent's WS stream. Reconnects on transient close with
 * exponential backoff, resuming from the last seen seq via `?cursor=N` so
 * the server replays missed events from `transcript.ndjson` before going
 * live. A 4404 close (supervisor doesn't know this agent) is terminal —
 * the UI surfaces "stopped" instead of retrying.
 */
export function useAgentStream(agentSlug: string) {
  const [events, setEvents] = useState<AgentEvent[]>([]);
  const [status, setStatus] = useState<ConnectionStatus>("connecting");
  const wsRef = useRef<WebSocket | null>(null);
  const lastSeqRef = useRef(0);
  const retryAttemptRef = useRef(0);

  useEffect(() => {
    let cancelled = false;
    let retryHandle: number | null = null;

    // Reset on agent change.
    setEvents([]);
    setStatus("connecting");
    lastSeqRef.current = 0;
    retryAttemptRef.current = 0;

    function scheduleReconnect() {
      const delay = delayForAttempt(retryAttemptRef.current);
      retryAttemptRef.current += 1;
      retryHandle = window.setTimeout(connect, delay);
    }

    function connect() {
      if (cancelled) return;
      const proto = window.location.protocol === "https:" ? "wss:" : "ws:";
      const url =
        `${proto}//${window.location.host}` +
        `/api/agents/${agentSlug}/stream` +
        `?cursor=${lastSeqRef.current}`;
      const ws = new WebSocket(url);
      wsRef.current = ws;

      ws.onopen = () => {
        if (cancelled) return;
        retryAttemptRef.current = 0;
        setStatus("connected");
      };

      ws.onmessage = (msg) => {
        if (cancelled) return;
        try {
          const event = JSON.parse(msg.data) as AgentEvent;
          if (typeof event.seq === "number") {
            lastSeqRef.current = event.seq;
          }
          setEvents((prev) => [...prev, event]);
        } catch {
          // Malformed frame — drop silently.
        }
      };

      ws.onclose = (ev) => {
        if (cancelled) return;
        if (ev.code === CLOSE_CODE_AGENT_NOT_RUNNING) {
          setStatus("stopped");
          return;
        }
        setStatus("closed");
        scheduleReconnect();
      };

      ws.onerror = () => {
        if (cancelled) return;
        setStatus("error");
        ws.close();
      };
    }

    connect();

    return () => {
      cancelled = true;
      if (retryHandle !== null) window.clearTimeout(retryHandle);
      wsRef.current?.close();
      wsRef.current = null;
    };
  }, [agentSlug]);

  function sendInput(text: string) {
    const ws = wsRef.current;
    if (ws && ws.readyState === WebSocket.OPEN) {
      ws.send(JSON.stringify({ type: "input", text }));
    }
  }

  return { events, status, sendInput };
}
