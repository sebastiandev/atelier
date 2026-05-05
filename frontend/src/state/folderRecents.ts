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
   * Returns the per-work list (most-recent first) followed by globals not
   * already in it. Empty for a brand-new work that hasn't seen any agent
   * created yet.
   */
  candidates: (workSlug: string) => string[];
  /**
   * Record a folder as freshly used. De-dupes case-sensitively (paths
   * are case-sensitive on Linux/macOS — we don't try to be clever here).
   */
  remember: (workSlug: string, folder: string) => void;
};

function moveToFront(list: string[], value: string, cap: number): string[] {
  const trimmed = value.trim();
  if (!trimmed) return list;
  const without = list.filter((f) => f !== trimmed);
  return [trimmed, ...without].slice(0, cap);
}

export const useFolderRecentsStore = create<FolderRecentsState>()(
  persist(
    (set, get) => ({
      byWork: {},
      global: [],
      candidates: (workSlug) => {
        const state = get();
        const local = state.byWork[workSlug] ?? [];
        const localSet = new Set(local);
        const globalRest = state.global.filter((f) => !localSet.has(f));
        return [...local, ...globalRest];
      },
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
