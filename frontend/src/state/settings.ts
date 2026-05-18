import { create } from "zustand";

import { getSettings, putSettings } from "../api";

/**
 * User settings — the canonical store for everything the Settings page
 * surfaces (theme, editor, terminal, accent hue) plus the dev tweaks
 * panel's transient open/close state.
 *
 * Persistence model: the **backend** is canonical. On boot we GET
 * ``/api/settings`` and hydrate the store; setters write through to
 * PUT in the background. Settings follow the user across browsers on
 * the same Atelier host (which is the actual use case — local backend,
 * possibly multiple browsers / PWA installs).
 *
 * For backward compatibility with installs that already wrote
 * ``localStorage[atelier:tweaks]`` + ``localStorage[atelier:theme]``,
 * the boot path runs a one-shot migration that pushes the legacy values
 * to the backend and deletes the local keys. See ``hydrateSettings``.
 *
 * ``panelOpen`` is intentionally not persisted anywhere — the dev
 * tweaks panel is for live colour iteration and shouldn't survive a
 * reload.
 */
export type EditorChoice =
  | "vscode"
  | "cursor"
  | "pycharm"
  | "idea"
  | "webstorm"
  | "vim";

export type TerminalChoice =
  | "system"
  | "iterm2"
  | "terminator"
  | "gnome-terminal"
  | "konsole"
  | "tmux";

export type Theme = "dark" | "light" | "ansi";

const THEMES: readonly Theme[] = ["light", "dark", "ansi"] as const;

export const SETTINGS_DEFAULTS = {
  accentHue: 250,
  editor: "vscode" as EditorChoice,
  terminal: "system" as TerminalChoice,
  theme: "ansi" as Theme,
};

type SettingsState = {
  accentHue: number;
  editor: EditorChoice;
  terminal: TerminalChoice;
  theme: Theme;
  panelOpen: boolean;
  setAccentHue: (h: number) => void;
  setEditor: (e: EditorChoice) => void;
  setTerminal: (t: TerminalChoice) => void;
  setTheme: (t: Theme) => void;
  toggleTheme: () => void;
  reset: () => void;
  togglePanel: () => void;
  closePanel: () => void;
};

function nextTheme(current: Theme): Theme {
  // light → dark → ansi → light. Mirrors the cycle order used by the
  // ThemeToggle icon's "preview the next theme" semantics.
  const i = THEMES.indexOf(current);
  return THEMES[(i + 1) % THEMES.length];
}

export const useSettingsStore = create<SettingsState>()((set, get) => ({
  ...SETTINGS_DEFAULTS,
  panelOpen: false,
  setAccentHue: (accentHue) => {
    set({ accentHue });
    void putSettings({ accent_hue: accentHue });
  },
  setEditor: (editor) => {
    set({ editor });
    void putSettings({ editor });
  },
  setTerminal: (terminal) => {
    set({ terminal });
    void putSettings({ terminal });
  },
  setTheme: (theme) => {
    set({ theme });
    void putSettings({ theme });
  },
  toggleTheme: () => {
    const theme = nextTheme(get().theme);
    set({ theme });
    void putSettings({ theme });
  },
  reset: () => {
    set(SETTINGS_DEFAULTS);
    void putSettings({
      accent_hue: SETTINGS_DEFAULTS.accentHue,
      editor: SETTINGS_DEFAULTS.editor,
      terminal: SETTINGS_DEFAULTS.terminal,
      theme: SETTINGS_DEFAULTS.theme,
    });
  },
  togglePanel: () => set((s) => ({ panelOpen: !s.panelOpen })),
  closePanel: () => set({ panelOpen: false }),
}));

export const EDITOR_OPTS: { value: EditorChoice; label: string }[] = [
  { value: "vscode", label: "VS Code" },
  { value: "cursor", label: "Cursor" },
  { value: "pycharm", label: "PyCharm" },
  { value: "idea", label: "IntelliJ IDEA" },
  { value: "webstorm", label: "WebStorm" },
  { value: "vim", label: "Vim (MacVim)" },
];

export const TERMINAL_OPTS: { value: TerminalChoice; label: string }[] = [
  { value: "system", label: "System default" },
  { value: "iterm2", label: "iTerm2 (macOS)" },
  { value: "terminator", label: "Terminator (Linux)" },
  { value: "gnome-terminal", label: "GNOME Terminal" },
  { value: "konsole", label: "Konsole (KDE)" },
  { value: "tmux", label: "tmux" },
];

/**
 * Build the OS-handler URL for the chosen editor. JetBrains IDEs use
 * `<ide>://open?file=<encoded path>` (registered by JetBrains Toolbox);
 * VSCode + Cursor use `vscode://file<path>` (Cursor also responds to
 * the vscode:// scheme, but its native `cursor://` is preferred when
 * the user explicitly picks Cursor).
 *
 * "Vim" maps to MacVim's `mvim://open?url=file://<path>` scheme — the
 * de-facto browser-launchable handler for Vim on macOS.
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

const LEGACY_TWEAKS_KEY = "atelier:tweaks";
const LEGACY_THEME_KEY = "atelier:theme";

/**
 * One-shot migration: if the legacy zustand-persisted blobs exist in
 * localStorage, push their values into the backend (so they aren't lost
 * when the store is renamed) and remove the keys. Idempotent: subsequent
 * boots find no legacy keys and short-circuit.
 *
 * Returns a partial settings payload if any value was migrated, so the
 * caller can decide whether to re-fetch before hydrating.
 */
async function migrateLegacyLocalStorage(): Promise<boolean> {
  let migrated = false;
  const patch: Record<string, string | number> = {};

  const tweaksRaw = readKey(LEGACY_TWEAKS_KEY);
  if (tweaksRaw) {
    const state = parseState(tweaksRaw);
    if (state) {
      if (typeof state.editor === "string") patch.editor = state.editor;
      if (typeof state.terminal === "string") patch.terminal = state.terminal;
      if (typeof state.accentHue === "number") patch.accent_hue = state.accentHue;
    }
    removeKey(LEGACY_TWEAKS_KEY);
    migrated = true;
  }

  const themeRaw = readKey(LEGACY_THEME_KEY);
  if (themeRaw) {
    const state = parseState(themeRaw);
    if (state && typeof state.theme === "string") patch.theme = state.theme;
    removeKey(LEGACY_THEME_KEY);
    migrated = true;
  }

  if (Object.keys(patch).length > 0) {
    await putSettings(patch).catch(() => null);
  }
  return migrated;
}

function readKey(key: string): string | null {
  try {
    return window.localStorage.getItem(key);
  } catch {
    return null;
  }
}

function removeKey(key: string): void {
  try {
    window.localStorage.removeItem(key);
  } catch {
    // ignore — the key just won't be cleaned up; non-fatal.
  }
}

function parseState(raw: string): Record<string, unknown> | null {
  try {
    const parsed = JSON.parse(raw) as { state?: Record<string, unknown> };
    return parsed.state ?? null;
  } catch {
    return null;
  }
}

/**
 * Run once on app boot. Migrates legacy localStorage values into the
 * backend, then fetches the canonical row and hydrates the store. On
 * fetch failure the store stays at defaults — the user can still drive
 * the UI, and the next setter call retries the PUT against the
 * backend. Safe to call repeatedly (the legacy migration is idempotent).
 */
export async function hydrateSettings(): Promise<void> {
  await migrateLegacyLocalStorage();
  try {
    const remote = await getSettings();
    useSettingsStore.setState({
      accentHue: remote.accent_hue,
      editor: remote.editor as EditorChoice,
      terminal: remote.terminal as TerminalChoice,
      theme: remote.theme as Theme,
    });
  } catch {
    // Backend unreachable on boot — keep defaults and let the next
    // setter retry. We deliberately don't surface a toast: the first
    // load on a freshly-booted backend will hit this for ~100ms.
  }
}
