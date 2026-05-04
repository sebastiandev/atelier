import { create } from "zustand";
import { persist } from "zustand/middleware";

/**
 * Per-work set of *closed* agent slugs. A closed agent is unmounted from
 * the canvas — the WS connection is torn down and no events stream while
 * closed. The agent still appears in the rail; clicking a rail entry
 * restores the tile, the WS reconnects, and the supervisor resumes the
 * provider session by ID so the conversation continues exactly where it
 * left off.
 *
 * Persisted to localStorage so the user's layout survives reloads.
 */
type ClosedState = {
  byWork: Record<string, string[]>;
  isClosed: (workSlug: string, agentSlug: string) => boolean;
  close: (workSlug: string, agentSlug: string) => void;
  restore: (workSlug: string, agentSlug: string) => void;
};

export const useClosedStore = create<ClosedState>()(
  persist(
    (set, get) => ({
      byWork: {},
      isClosed: (workSlug, agentSlug) =>
        (get().byWork[workSlug] ?? []).includes(agentSlug),
      close: (workSlug, agentSlug) =>
        set((state) => {
          const current = state.byWork[workSlug] ?? [];
          if (current.includes(agentSlug)) return state;
          return {
            byWork: { ...state.byWork, [workSlug]: [...current, agentSlug] },
          };
        }),
      restore: (workSlug, agentSlug) =>
        set((state) => {
          const current = state.byWork[workSlug] ?? [];
          if (!current.includes(agentSlug)) return state;
          return {
            byWork: {
              ...state.byWork,
              [workSlug]: current.filter((s) => s !== agentSlug),
            },
          };
        }),
    }),
    { name: "atelier:closed", version: 1 },
  ),
);
