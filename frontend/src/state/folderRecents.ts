import { create } from "zustand";
import { persist } from "zustand/middleware";

/**
 * Recent agent folders, kept both globally and per-work, so the new-agent
 * dialog can pre-populate its folder dropdown with sensible candidates:
 * folders other agents in this work used (highest signal — same task
 * context) followed by global recents (next best — same user, recent
 * activity).
 *
 * Most-recently-used first within each list. Cap is small on purpose;
 * a long history isn't useful for a typed text field.
 */
const MAX_PER_WORK = 8;
const MAX_GLOBAL = 16;

type FolderRecentsState = {
  byWork: Record<string, string[]>;
  global: string[];
  /**
   * Record a folder as freshly used. De-dupes case-sensitively (paths
   * are case-sensitive on Linux/macOS — we don't try to be clever here).
   */
  remember: (workSlug: string, folder: string) => void;
};

/** Stable empty singleton for ``byWork[slug]`` lookups so the selector
 *  doesn't return a fresh ``[]`` each render and trip Zustand's default
 *  Object.is snapshot check (same trap as ``state/closed.ts``). */
export const NO_FOLDERS: readonly string[] = [];

/**
 * Per-work list (most-recent first) followed by global recents not
 * already in it. Pure derivation — call inside a ``useMemo`` from the
 * raw slices selected via the store, never as a Zustand selector
 * itself: it builds a new array each call.
 */
export function deriveFolderCandidates(
  byWork: readonly string[],
  global: readonly string[],
): string[] {
  const localSet = new Set(byWork);
  return [...byWork, ...global.filter((f) => !localSet.has(f))];
}

function moveToFront(list: string[], value: string, cap: number): string[] {
  const trimmed = value.trim();
  if (!trimmed) return list;
  const without = list.filter((f) => f !== trimmed);
  return [trimmed, ...without].slice(0, cap);
}

export const useFolderRecentsStore = create<FolderRecentsState>()(
  persist(
    (set) => ({
      byWork: {},
      global: [],
      remember: (workSlug, folder) =>
        set((state) => ({
          byWork: {
            ...state.byWork,
            [workSlug]: moveToFront(
              state.byWork[workSlug] ?? [],
              folder,
              MAX_PER_WORK,
            ),
          },
          global: moveToFront(state.global, folder, MAX_GLOBAL),
        })),
    }),
    { name: "atelier:folder-recents", version: 1 },
  ),
);
