import { create } from "zustand";

/**
 * Per-work revision counter that AgentTiles bump when their stream
 * surfaces an ``artifact_recorded`` event. The WorkView pull-request rail
 * watches the counter for its work and refetches the artifact list when
 * it changes.
 *
 * Why a Zustand store rather than a context: rail and tiles are siblings
 * under WorkView, so a bump from a closed-then-reopened tile (or any
 * future panel) needs to reach the rail without prop-drilling. Not
 * persisted — the source of truth is the server; this is just a
 * "something changed, refetch" signal.
 */
type ArtifactsRefreshState = {
  byWork: Record<string, number>;
  bump: (workSlug: string) => void;
};

export const useArtifactsRefresh = create<ArtifactsRefreshState>((set) => ({
  byWork: {},
  bump: (workSlug) =>
    set((state) => ({
      byWork: { ...state.byWork, [workSlug]: (state.byWork[workSlug] ?? 0) + 1 },
    })),
}));

export function selectWorkRevision(
  state: ArtifactsRefreshState,
  workSlug: string,
): number {
  return state.byWork[workSlug] ?? 0;
}
