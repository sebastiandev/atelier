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
      version: 2,
      // v1 persisted Theme = "dark" | "light" only; any other value lands
      // here on hydration. Coerce unknown values back to ansi (the new
      // default) instead of letting `data-theme` end up bogus.
      migrate: (state) => {
        const t = (state as { theme?: unknown } | null)?.theme;
        const valid = t === "light" || t === "dark" || t === "ansi";
        return { theme: valid ? (t as Theme) : "ansi" };
      },
    },
  ),
);
