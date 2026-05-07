import { create } from "zustand";
import { persist } from "zustand/middleware";

/**
 * UI theme — three-way cycle.
 *
 * `dark` is the default (no `[data-theme]` selector needed beyond
 * `:root`); `light` and `ansi` are explicit overrides in styles.css.
 * `App.tsx` mirrors this onto `<html data-theme=...>` so the attribute
 * always reflects the current value (no "absent attribute means dark"
 * implicit rule).
 */
export type Theme = "dark" | "light" | "ansi";

const THEMES: readonly Theme[] = ["light", "dark", "ansi"] as const;

type ThemeState = {
  theme: Theme;
  setTheme: (theme: Theme) => void;
  toggleTheme: () => void;
};

function nextTheme(current: Theme): Theme {
  // light → dark → ansi → light. The cycle order matches the toggle
  // icon's "preview the next theme" semantics in ThemeToggle.
  const i = THEMES.indexOf(current);
  return THEMES[(i + 1) % THEMES.length];
}

export const useThemeStore = create<ThemeState>()(
  persist(
    (set) => ({
      theme: "ansi",
      setTheme: (theme) => set({ theme }),
      toggleTheme: () => set((state) => ({ theme: nextTheme(state.theme) })),
    }),
    {
      name: "atelier:theme",
      // v3 force-resets every persisted preference to ansi. v2 introduced
      // ansi as the default for fresh installs but left existing users on
      // their persisted dark/light. The 2026-05-07 design pass made ansi
      // the canonical look — bumping the version pushes the reset to
      // every browser without dropping the rest of localStorage.
      // Users who actually preferred dark or light can re-toggle once
      // (preference re-persists under the new version key).
      version: 3,
      migrate: () => ({ theme: "ansi" as Theme }),
    },
  ),
);
