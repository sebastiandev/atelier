import { create } from "zustand";
import { persist } from "zustand/middleware";

/**
 * UI theme — currently a binary dark/light toggle.
 *
 * Dark is the default at `:root`; switching to "light" sets
 * `data-theme="light"` on `<html>` so the override block in styles.css
 * takes over. The attribute is always set so the value in the DOM is
 * unambiguous (no "absent attribute means dark" implicit rule).
 */
export type Theme = "dark" | "light";

type ThemeState = {
  theme: Theme;
  setTheme: (theme: Theme) => void;
  toggleTheme: () => void;
};

export const useThemeStore = create<ThemeState>()(
  persist(
    (set) => ({
      theme: "dark",
      setTheme: (theme) => set({ theme }),
      toggleTheme: () =>
        set((state) => ({ theme: state.theme === "dark" ? "light" : "dark" })),
    }),
    { name: "atelier:theme", version: 1 },
  ),
);
