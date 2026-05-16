import { useEffect, useMemo, useState } from "react";

import { type Connection, listConnections } from "./api";
import { BrandMark } from "./BrandMark";
import { Connections } from "./Connections";
import { CheckIcon, SlidersIcon } from "./Icons";
import { useThemeStore, type Theme } from "./state/theme";
import {
  EDITOR_OPTS,
  TERMINAL_OPTS,
  type EditorChoice,
  type TerminalChoice,
  useTweaksStore,
} from "./state/tweaks";

export type SettingsSection =
  | "tools"
  | "connections"
  | "appearance"
  | "about";

const SECTIONS: { id: SettingsSection; label: string; href: string }[] = [
  { id: "tools", label: "default tools", href: "/settings" },
  { id: "connections", label: "connections", href: "/settings/connections" },
  { id: "appearance", label: "appearance", href: "/settings/appearance" },
  { id: "about", label: "about", href: "/settings/about" },
];

export function Settings({ section }: { section: SettingsSection }) {
  // Connection count drives the nav-item chip — fetched lazily.
  const [connectionCount, setConnectionCount] = useState<number | null>(null);
  useEffect(() => {
    listConnections()
      .then((rows: Connection[]) => setConnectionCount(rows.length))
      .catch(() => setConnectionCount(null));
  }, []);

  return (
    <div className="shell-v3 settings-v3">
      <aside className="shell-left settings-rail">
        <div className="crown">
          <a className="wordmark" href="/" title="Back to workspace">
            <span className="wm-mark" aria-hidden>
              <BrandMark />
            </span>
            <span className="wm-rest">telier</span>
          </a>
        </div>
        <div className="crumbs-v3">
          <a className="crumb" href="/">
            ← workspace
          </a>
          <span className="sep">/</span>
          <span className="now">settings</span>
        </div>
        <nav className="settings-nav">
          {SECTIONS.map((s) => (
            <a
              key={s.id}
              className={
                "settings-nav-item" + (section === s.id ? " active" : "")
              }
              href={s.href}
            >
              <span>{s.label}</span>
              {s.id === "connections" && connectionCount != null && (
                <span className="count">{connectionCount}</span>
              )}
            </a>
          ))}
        </nav>
        <div className="v3-footstrip">
          <span className="seg">
            <SlidersIcon size={11} /> ⌘, opens settings
          </span>
        </div>
      </aside>

      <main className="shell-right settings-right">
        <div className="settings-body">
          {section === "tools" && <SettingsTools />}
          {section === "connections" && <SettingsConnections />}
          {section === "appearance" && <SettingsAppearance />}
          {section === "about" && <SettingsAbout />}
        </div>
      </main>
    </div>
  );
}

function SettingsSectionHd({
  title,
  sub,
}: {
  title: string;
  sub: string;
}) {
  return (
    <div className="settings-section-hd">
      <h1 className="title">{title}</h1>
      <p className="sub">{sub}</p>
    </div>
  );
}

function SettingsCard({
  label,
  hint,
  children,
}: {
  label: string;
  hint?: string;
  children: React.ReactNode;
}) {
  return (
    <div className="settings-card">
      <div className="settings-card-hd">
        <span className="t">{label}</span>
        {hint && <span className="hint">{hint}</span>}
      </div>
      <div className="settings-card-body">{children}</div>
    </div>
  );
}

// ─── Default tools ──────────────────────────────────────────────

function SettingsTools() {
  const editor = useTweaksStore((s) => s.editor);
  const terminal = useTweaksStore((s) => s.terminal);
  const setEditor = useTweaksStore((s) => s.setEditor);
  const setTerminal = useTweaksStore((s) => s.setTerminal);
  return (
    <>
      <SettingsSectionHd
        title="Default tools"
        sub="Apps that fire when you click an agent's open-in-editor / open-in-terminal button. Per-browser preference, no backend round-trip."
      />
      <SettingsCard
        label="EDITOR"
        hint="Opens when an agent tile's “Open in editor” fires."
      >
        <div className="tool-grid">
          {EDITOR_OPTS.map((opt) => (
            <ToolCardEditor
              key={opt.value}
              opt={opt}
              active={editor === opt.value}
              onPick={() => setEditor(opt.value)}
            />
          ))}
        </div>
      </SettingsCard>
      <SettingsCard
        label="CONSOLE"
        hint="The terminal used by “Open in terminal” + detach actions."
      >
        <div className="tool-grid">
          {TERMINAL_OPTS.map((opt) => (
            <ToolCardTerminal
              key={opt.value}
              opt={opt}
              active={terminal === opt.value}
              onPick={() => setTerminal(opt.value)}
            />
          ))}
        </div>
      </SettingsCard>
    </>
  );
}

const EDITOR_CMD: Record<EditorChoice, string> = {
  vscode: "code .",
  cursor: "cursor .",
  pycharm: "charm .",
  idea: "idea .",
  webstorm: "wstorm .",
  vim: "mvim .",
};
function ToolCardEditor({
  opt,
  active,
  onPick,
}: {
  opt: { value: EditorChoice; label: string };
  active: boolean;
  onPick: () => void;
}) {
  return (
    <button
      type="button"
      className={"tool-card" + (active ? " active" : "")}
      onClick={onPick}
    >
      <div className="tool-card-hd">
        <span className="tool-name">{opt.label}</span>
        <span className="tool-cmd">${EDITOR_CMD[opt.value]}</span>
      </div>
    </button>
  );
}

const TERMINAL_CMD: Record<TerminalChoice, string> = {
  system: "open -a Terminal",
  iterm2: "open -a iTerm",
  terminator: "terminator",
  "gnome-terminal": "gnome-terminal",
  konsole: "konsole",
  tmux: "tmux new",
};
function ToolCardTerminal({
  opt,
  active,
  onPick,
}: {
  opt: { value: TerminalChoice; label: string };
  active: boolean;
  onPick: () => void;
}) {
  return (
    <button
      type="button"
      className={"tool-card" + (active ? " active" : "")}
      onClick={onPick}
    >
      <div className="tool-card-hd">
        <span className="tool-name">{opt.label}</span>
        <span className="tool-cmd">${TERMINAL_CMD[opt.value]}</span>
      </div>
    </button>
  );
}

// ─── Connections ────────────────────────────────────────────────

function SettingsConnections() {
  return (
    <>
      <SettingsSectionHd
        title="Connections"
        sub="Source creds, saved once. Reused whenever an agent needs to pull a ticket, error, or trace."
      />
      <Connections chromeless />
    </>
  );
}

// ─── Appearance ─────────────────────────────────────────────────

function SettingsAppearance() {
  const theme = useThemeStore((s) => s.theme);
  const setTheme = useThemeStore((s) => s.setTheme);
  const themes: { value: Theme; label: string; swatch: string }[] = [
    { value: "light", label: "Light", swatch: "swatch-light" },
    { value: "dark", label: "Dark", swatch: "swatch-dark" },
    { value: "ansi", label: "ANSI terminal", swatch: "swatch-ansi" },
  ];
  return (
    <>
      <SettingsSectionHd
        title="Appearance"
        sub="The shell theme cycles light → dark → ANSI. ANSI is the default — a softer dark with bright 16-colour terminal accents."
      />
      <SettingsCard label="THEME" hint="Click any card to switch.">
        <div className="tool-grid">
          {themes.map((t) => (
            <button
              key={t.value}
              type="button"
              className={
                "tool-card theme-card" + (theme === t.value ? " active" : "")
              }
              onClick={() => setTheme(t.value)}
            >
              <div className="tool-card-hd">
                <span className="tool-name">
                  <span className={`theme-swatch ${t.swatch}`} aria-hidden />{" "}
                  {t.label}
                </span>
                {theme === t.value && (
                  <span className="tool-cmd">
                    <CheckIcon size={10} /> active
                  </span>
                )}
              </div>
            </button>
          ))}
        </div>
      </SettingsCard>
    </>
  );
}

// ─── About ──────────────────────────────────────────────────────

function SettingsAbout() {
  const theme = useThemeStore((s) => s.theme);
  const rows = useMemo(
    () => [
      { label: "Atelier", value: "v3 — quiet shell" },
      { label: "Theme", value: theme },
      { label: "Frontend", value: window.location.host },
      { label: "Backend", value: "/api on the same origin" },
    ],
    [theme],
  );
  return (
    <>
      <SettingsSectionHd
        title="About"
        sub="Workspace + runtime info. Atelier is local-first: every agent's transcript, every artifact, every connection lives on this machine."
      />
      <SettingsCard label="RUNTIME">
        <div className="settings-card-body">
          {rows.map((row) => (
            <div className="settings-field" key={row.label}>
              <div className="settings-field-l">
                <div className="lbl">{row.label}</div>
              </div>
              <div className="settings-field-r">
                <span className="mono">{row.value}</span>
              </div>
            </div>
          ))}
        </div>
      </SettingsCard>
      <SettingsCard label="LICENCE & SOURCE" hint="Open-source — patches welcome.">
        <div className="settings-card-body">
          <div className="settings-field">
            <div className="settings-field-l">
              <div className="lbl">Repository</div>
              <div className="hint">github.com/sebastiandev/atelier</div>
            </div>
            <div className="settings-field-r">
              <a
                className="btn"
                href="https://github.com/sebastiandev/atelier"
                target="_blank"
                rel="noopener noreferrer"
              >
                Open on GitHub ↗
              </a>
            </div>
          </div>
        </div>
      </SettingsCard>
    </>
  );
}
