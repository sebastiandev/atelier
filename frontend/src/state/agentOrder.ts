import { create } from "zustand";
import { persist } from "zustand/middleware";

/**
 * Per-work agent display order. The backend's `listAgents` always returns
 * by creation order; this store layers a user-controlled override on top
 * so the rail and canvas can show agents in any sequence.
 *
 * Storage model: when the user reorders, we capture the FULL ordered list
 * at write time (not a sparse "anchor + new"). Sparse overrides can't
 * preserve creation-order positions for slugs that the writer didn't
 * mention, so we always materialize the resolved order before saving.
 *
 * The resolver is then trivial: filter the override for slugs that still
 * exist (cheaply tolerating deleted agents), then append any creation-
 * order slugs the override doesn't mention (newly-created agents land at
 * the end until the user repositions them).
 *
 * Writers:
 *   - Handoff: `insertAfter(workSlug, sourceSlug, newSlug, currentOrder)`
 *     so a freshly forked agent shows up adjacent to its source.
 *   - STORY-024 drag-to-reorder will land `setOrder(workSlug, ordered)`.
 *
 * Persisted to localStorage — UI session state is frontend-local per
 * the locked architecture pivot.
 */
type AgentOrderState = {
  byWork: Record<string, string[]>;
  /**
   * Insert ``newSlug`` immediately after ``anchorSlug`` in the work's
   * order. ``currentOrder`` is the order the caller is rendering RIGHT
   * NOW (override-applied) — the writer materializes that as the new
   * authoritative order with ``newSlug`` injected. Pass an empty list
   * when the new agent's an only-child; it'll just be the sole entry.
   */
  insertAfter: (
    workSlug: string,
    anchorSlug: string,
    newSlug: string,
    currentOrder: string[],
  ) => void;
};

export const useAgentOrderStore = create<AgentOrderState>()(
  persist(
    (set) => ({
      byWork: {},
      insertAfter: (workSlug, anchorSlug, newSlug, currentOrder) =>
        set((state) => {
          const without = currentOrder.filter((s) => s !== newSlug);
          const anchorIdx = without.indexOf(anchorSlug);
          const next =
            anchorIdx === -1
              ? // Anchor missing from current — append. The handoff flow
                // shouldn't hit this since the source is always already
                // on the canvas at write time, but tolerate it cleanly.
                [...without, newSlug]
              : [
                  ...without.slice(0, anchorIdx + 1),
                  newSlug,
                  ...without.slice(anchorIdx + 1),
                ];
          return { byWork: { ...state.byWork, [workSlug]: next } };
        }),
    }),
    { name: "atelier:agent-order" },
  ),
);

/**
 * Resolve the display order for a work given the override and the
 * backend's creation-order list of slugs.
 *
 * - Override entries that no longer match an existing slug are dropped
 *   (deleted-agent tolerance).
 * - Creation-order slugs the override doesn't mention go at the end,
 *   keeping their relative creation order. New agents created after
 *   the last reorder land here until the user moves them.
 */
export function applyAgentOrder(
  override: string[] | undefined,
  creationOrder: string[],
): string[] {
  if (!override || override.length === 0) return creationOrder;
  const creationSet = new Set(creationOrder);
  const validOverride = override.filter((s) => creationSet.has(s));
  const overrideSet = new Set(validOverride);
  const tail = creationOrder.filter((s) => !overrideSet.has(s));
  return [...validOverride, ...tail];
}
