import { create } from "zustand";

import { getSettings, putSettings, type SettingsToolOption } from "../api";

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
export type EditorChoice = string;

export type TerminalChoice = string;

export type ToolOption = SettingsToolOption;

export type Theme = "dark" | "light" | "ansi";

const THEMES: readonly Theme[] = ["light", "dark", "ansi"] as const;

export const SETTINGS_DEFAULTS = {
  // 278° in OKLCH is the soft purple of the Atelier dock icon
  // (#5B5BD6). The rest of the accent ramp is derived via oklch() in
  // styles.css; keep this in lockstep with the :root ``--accent-h``
  // token and the backend route's DEFAULTS dict so a fresh install,
  // a missing settings row, and the SSR-less initial paint all land
  // on the same hue.
  accentHue: 278,
  editor: "vscode" as EditorChoice,
  terminal: "system" as TerminalChoice,
  theme: "ansi" as Theme,
};

type SettingsState = {
  accentHue: number;
  editor: EditorChoice;
  terminal: TerminalChoice;
  theme: Theme;
  editorOptions: ToolOption[];
  terminalOptions: ToolOption[];
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

const FALLBACK_EDITOR_OPTIONS: ToolOption[] = [
  {
    value: "vscode",
    label: "VS Code",
    command: "code .",
    url_template: "vscode://file{path_uri}",
  },
  {
    value: "cursor",
    label: "Cursor",
    command: "cursor .",
    url_template: "cursor://file{path_uri}",
  },
  {
    value: "zed",
    label: "Zed",
    command: "zed .",
    url_template: "zed://file{path_segments}",
  },
  {
    value: "pycharm",
    label: "PyCharm",
    command: "charm .",
    url_template: "pycharm://open?file={path_param}",
  },
  {
    value: "idea",
    label: "IntelliJ IDEA",
    command: "idea .",
    url_template: "idea://open?file={path_param}",
  },
  {
    value: "webstorm",
    label: "WebStorm",
    command: "wstorm .",
    url_template: "webstorm://open?file={path_param}",
  },
  {
    value: "vim",
    label: "Vim (MacVim)",
    command: "mvim .",
    url_template: "mvim://open?url={file_uri}",
  },
];

const FALLBACK_TERMINAL_OPTIONS: ToolOption[] = [
  { value: "system", label: "System default", command: "open -a Terminal" },
  { value: "iterm2", label: "iTerm2 (macOS)", command: "open -a iTerm" },
  { value: "terminator", label: "Terminator (Linux)", command: "terminator" },
  {
    value: "gnome-terminal",
    label: "GNOME Terminal",
    command: "gnome-terminal",
  },
  { value: "konsole", label: "Konsole (KDE)", command: "konsole" },
  { value: "tmux", label: "tmux", command: "tmux new" },
];

export const useSettingsStore = create<SettingsState>()((set, get) => ({
  ...SETTINGS_DEFAULTS,
  editorOptions: FALLBACK_EDITOR_OPTIONS,
  terminalOptions: FALLBACK_TERMINAL_OPTIONS,
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

/**
 * Build the OS-handler URL for the chosen editor from the backend-owned
 * descriptor. The frontend only knows how to interpolate path tokens.
 */
export function editorUrl(editor: EditorChoice, path: string): string {
  const option =
    useSettingsStore.getState().editorOptions.find((opt) => opt.value === editor) ??
    FALLBACK_EDITOR_OPTIONS.find((opt) => opt.value === editor) ??
    FALLBACK_EDITOR_OPTIONS[0];
  return renderUrlTemplate(option.url_template, path);
}

function encodeFilePath(path: string): string {
  return path.split("/").map(encodeURIComponent).join("/");
}

function renderUrlTemplate(
  template: string | null | undefined,
  path: string,
): string {
  const fileUri = `file://${encodeURI(path)}`;
  return (template || "vscode://file{path_uri}")
    .replaceAll("{path}", path)
    .replaceAll("{path_uri}", encodeURI(path))
    .replaceAll("{path_param}", encodeURIComponent(path))
    .replaceAll("{path_segments}", encodeFilePath(path))
    .replaceAll("{file_uri}", fileUri);
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
      editorOptions: remote.editor_options ?? FALLBACK_EDITOR_OPTIONS,
      terminalOptions: remote.terminal_options ?? FALLBACK_TERMINAL_OPTIONS,
    });
  } catch {
    // Backend unreachable on boot — keep defaults and let the next
    // setter retry. We deliberately don't surface a toast: the first
    // load on a freshly-booted backend will hit this for ~100ms.
  }
}
