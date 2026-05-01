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
  | "error";

/**
 * Subscribe to an agent's WS stream. Reconnects automatically on close
 * with a 1s delay; resumes from the last seen seq via `?cursor=N` so the
 * server replays missed events from `transcript.ndjson` before going live.
 *
 * Phase A keeps the retry policy dead-simple. Phase B adds backoff +
 * cursor persistence (Zustand) per the design handoff's reconnection UX.
 */
export function useAgentStream(agentSlug: string) {
  const [events, setEvents] = useState<AgentEvent[]>([]);
  const [status, setStatus] = useState<ConnectionStatus>("connecting");
  const wsRef = useRef<WebSocket | null>(null);
  const lastSeqRef = useRef(0);

  useEffect(() => {
    let cancelled = false;
    let retryHandle: number | null = null;

    // Reset on agent change.
    setEvents([]);
    setStatus("connecting");
    lastSeqRef.current = 0;

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

      ws.onclose = () => {
        if (cancelled) return;
        setStatus("closed");
        retryHandle = window.setTimeout(connect, 1000);
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
