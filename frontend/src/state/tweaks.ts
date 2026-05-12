import { create } from "zustand";
import { persist } from "zustand/middleware";

/**
 * Tweaks panel state — accent hue + canvas layout choice + editor.
 *
 * `accentHue` (0-360°) drives the `--accent-h` CSS variable on `<html>`;
 * the rest of the accent ramp is `oklch()` derived from it. Defaults to
 * 250 to match the design tokens at `:root`. Note that `--md-accent`
 * and `--md-heading` (used by the agent's rendered markdown) are
 * decoupled — they're hardcoded sage + purple, not driven by this hue.
 *
 * `layout` controls how `WorkView` arranges its agent tiles. STORY-024
 * landed drag-to-reorder on the existing layouts and dropped the
 * freeform-drag "windows" mode — the migration below maps any persisted
 * "windows" value back to "tiles" so the layout choice survives the
 * removal cleanly.
 *
 * `editor` is the user's preferred IDE for the "Open in editor" action
 * on agent tiles. It's just a per-browser preference — we map it to the
 * right URL scheme when the button fires (see `editorUrl`). Defaults to
 * VSCode since that's what shipped originally.
 *
 * `panelOpen` is intentionally NOT persisted — the user opens the panel
 * to tweak something, not to leave it floating across reloads.
 */
export type CanvasLayout = "tiles" | "columns";

export type EditorChoice =
  | "vscode"
  | "cursor"
  | "pycharm"
  | "idea"
  | "webstorm"
  | "vim";

const DEFAULTS = {
  accentHue: 250,
  layout: "tiles" as CanvasLayout,
  editor: "vscode" as EditorChoice,
};

type TweaksState = {
  accentHue: number;
  layout: CanvasLayout;
  editor: EditorChoice;
  panelOpen: boolean;
  setAccentHue: (h: number) => void;
  setLayout: (l: CanvasLayout) => void;
  setEditor: (e: EditorChoice) => void;
  reset: () => void;
  togglePanel: () => void;
  closePanel: () => void;
};

export const TWEAKS_DEFAULTS = DEFAULTS;

export const EDITOR_OPTS: { value: EditorChoice; label: string }[] = [
  { value: "vscode", label: "VS Code" },
  { value: "cursor", label: "Cursor" },
  { value: "pycharm", label: "PyCharm" },
  { value: "idea", label: "IntelliJ IDEA" },
  { value: "webstorm", label: "WebStorm" },
  { value: "vim", label: "Vim (MacVim)" },
];

/**
 * Build the OS-handler URL for the chosen editor. JetBrains IDEs use
 * `<ide>://open?file=<encoded path>` (registered by JetBrains Toolbox);
 * VSCode + Cursor use `vscode://file<path>` (Cursor also responds to
 * the vscode:// scheme, but its native `cursor://` is preferred when
 * the user explicitly picks Cursor).
 *
 * "Vim" maps to MacVim's `mvim://open?url=file://<path>` scheme — the
 * de-facto browser-launchable handler for Vim on macOS. Plain terminal
 * Vim has no URL scheme registered with the OS, so users on Linux /
 * Windows would need a custom handler for it to work.
 */
export function editorUrl(editor: EditorChoice, path: string): string {
  switch (editor) {
    case "cursor":
      return `cursor://file${path}`;
    case "pycharm":
      return `pycharm://open?file=${encodeURIComponent(path)}`;
    case "idea":
      return `idea://open?file=${encodeURIComponent(path)}`;
    case "webstorm":
      return `webstorm://open?file=${encodeURIComponent(path)}`;
    case "vim":
      return `mvim://open?url=file://${encodeURI(path)}`;
    case "vscode":
    default:
      return `vscode://file${path}`;
  }
}

export const useTweaksStore = create<TweaksState>()(
  persist(
    (set) => ({
      ...DEFAULTS,
      panelOpen: false,
      setAccentHue: (accentHue) => set({ accentHue }),
      setLayout: (layout) => set({ layout }),
      setEditor: (editor) => set({ editor }),
      reset: () => set(DEFAULTS),
      togglePanel: () => set((s) => ({ panelOpen: !s.panelOpen })),
      closePanel: () => set({ panelOpen: false }),
    }),
    {
      name: "atelier:tweaks",
      version: 3,
      // Only persist tweak values, not panel-open transient state.
      partialize: (s) => ({
        accentHue: s.accentHue,
        layout: s.layout,
        editor: s.editor,
      }),
      // v1 → v2: drop the "windows" layout option (replaced by tile-
      // reorder drag in STORY-024). Map any persisted "windows" back
      // to the default so reload doesn't show a broken layout.
      // v2 → v3: introduce `editor`; default existing users to VSCode
      // so the previous behavior is preserved.
      migrate: (persisted, version) => {
        const p = persisted as
          | { accentHue?: number; layout?: string; editor?: EditorChoice }
          | null;
        if (!p) return p;
        const next: {
          accentHue?: number;
          layout?: string;
          editor?: EditorChoice;
        } = { ...p };
        if (version < 2 && next.layout === "windows") {
          next.layout = "tiles";
        }
        if (version < 3 && !next.editor) {
          next.editor = DEFAULTS.editor;
        }
        return next;
      },
    },
  ),
);
