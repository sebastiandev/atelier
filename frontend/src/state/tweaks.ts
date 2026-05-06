import { create } from "zustand";
import { persist } from "zustand/middleware";

/**
 * Tweaks panel state — accent hue + canvas layout choice.
 *
 * `accentHue` (0-360°) drives the `--accent-h` CSS variable on `<html>`;
 * the rest of the accent ramp is `oklch()` derived from it. Defaults to
 * 250 to match the design tokens at `:root`. Note that `--md-accent`
 * and `--md-heading` (used by the agent's rendered markdown) are
 * decoupled — they're hardcoded sage + purple, not driven by this hue.
 *
 * `layout` controls how `WorkView` arranges its agent tiles. STORY-024
 * makes "windows" actually freeform-drag; until then it falls back to
 * "tiles" with a hint in the panel.
 *
 * `panelOpen` is intentionally NOT persisted — the user opens the panel
 * to tweak something, not to leave it floating across reloads.
 */
export type CanvasLayout = "tiles" | "columns" | "windows";

const DEFAULTS = {
  accentHue: 250,
  layout: "tiles" as CanvasLayout,
};

type TweaksState = {
  accentHue: number;
  layout: CanvasLayout;
  panelOpen: boolean;
  setAccentHue: (h: number) => void;
  setLayout: (l: CanvasLayout) => void;
  reset: () => void;
  togglePanel: () => void;
  closePanel: () => void;
};

export const TWEAKS_DEFAULTS = DEFAULTS;

export const useTweaksStore = create<TweaksState>()(
  persist(
    (set) => ({
      ...DEFAULTS,
      panelOpen: false,
      setAccentHue: (accentHue) => set({ accentHue }),
      setLayout: (layout) => set({ layout }),
      reset: () => set(DEFAULTS),
      togglePanel: () => set((s) => ({ panelOpen: !s.panelOpen })),
      closePanel: () => set({ panelOpen: false }),
    }),
    {
      name: "atelier:tweaks",
      version: 1,
      // Only persist tweak values, not panel-open transient state.
      partialize: (s) => ({ accentHue: s.accentHue, layout: s.layout }),
    },
  ),
);
