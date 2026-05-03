import { create } from "zustand";
import { persist } from "zustand/middleware";

/**
 * Per-agent WS resume cursor. Persisted to localStorage so a page refresh
 * resumes the stream from the last seen `seq` instead of replaying the
 * full transcript.
 *
 * Store stays narrow on purpose — one map, two operations. Future
 * frontend-only state (UI presentation concerns) can colocate as siblings
 * under `state/` rather than bloat this file.
 */
type CursorState = {
  cursors: Record<string, number>;
  getCursor: (agentSlug: string) => number;
  setCursor: (agentSlug: string, seq: number) => void;
};

export const useCursorStore = create<CursorState>()(
  persist(
    (set, get) => ({
      cursors: {},
      getCursor: (agentSlug) => get().cursors[agentSlug] ?? 0,
      setCursor: (agentSlug, seq) =>
        set((state) => {
          const current = state.cursors[agentSlug] ?? 0;
          if (seq <= current) return state;
          return { cursors: { ...state.cursors, [agentSlug]: seq } };
        }),
    }),
    { name: "atelier:cursors", version: 1 },
  ),
);
