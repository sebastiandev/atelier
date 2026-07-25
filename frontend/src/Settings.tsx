import { useEffect, useMemo, useState } from "react";

import { type Connection, listConnections, listLoopDefinitions } from "./api";
import { Connections } from "./Connections";
import { CheckIcon, SlidersIcon } from "./Icons";
import { LoopLibraryScreen } from "./LoopUI";
import { ShellTopbar } from "./ShellTopbar";
import {
  type ToolOption,
  type Theme,
  useSettingsStore,
} from "./state/settings";

export type SettingsSection =
  | "tools"
  | "loops"
  | "connections"
  | "appearance"
  | "about";

const SECTIONS = [
  {
    id: "tools",
    label: "default tools",
    title: "Default tools",
    detail: "Editor and terminal defaults",
    description: "Apps that fire when you click an agent's open-in-editor / open-in-terminal button. Options come from the backend settings descriptor.",
    href: "/settings",
  },
  {
    id: "loops",
    label: "loops",
    title: "Loops",
    detail: "Reusable multi-stage loops",
    href: "/settings/loops",
  },
  {
    id: "connections",
    label: "connections",
    title: "Connections",
    detail: "Saved source credentials",
    description: "Source creds, saved once. Reused whenever an agent needs to pull a ticket, error, or trace.",
    href: "/settings/connections",
  },
  {
    id: "appearance",
    label: "appearance",
    title: "Appearance",
    detail: "Theme preferences",
    description: "The shell theme cycles light → dark → ANSI. ANSI is the default — a softer dark with bright 16-colour terminal accents.",
    href: "/settings/appearance",
  },
  {
    id: "about",
    label: "about",
    title: "About",
    detail: "Workspace and runtime",
    description: "Workspace + runtime info. Atelier is local-first: every agent's transcript, every artifact, every connection lives on this machine.",
    href: "/settings/about",
  },
] satisfies Array<{
  id: SettingsSection;
  label: string;
  title: string;
  detail: string;
  description?: string;
  href: string;
}>;

export function Settings({ section }: { section: SettingsSection }) {
  // Connection count drives the nav-item chip — fetched lazily.
  const [connectionCount, setConnectionCount] = useState<number | null>(null);
  const [loopCount, setLoopCount] = useState<number | null>(null);
  useEffect(() => {
    listConnections()
      .then((rows: Connection[]) => setConnectionCount(rows.length))
      .catch(() => setConnectionCount(null));
  }, []);
  useEffect(() => {
    listLoopDefinitions(null)
      .then((rows) => setLoopCount(rows.length))
      .catch(() => setLoopCount(null));
  }, []);
  const currentSection = SECTIONS.find((item) => item.id === section)!;

  return (
    <div className="shell-v3 settings-v3 has-topbar">
      <ShellTopbar
        crumbs={section === "tools"
          ? [{ label: "settings" }]
          : [{ href: "/settings", label: "settings" }, { label: currentSection.label }]}
      />
      <aside className="shell-left settings-rail">
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
              {s.id === "loops" && loopCount != null && (
                <span className="count">{loopCount}</span>
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
        <div className={`settings-body${section === "loops" ? " wide" : ""}`}>
          {section !== "loops" && (
            <div className="settings-section-hd">
              <h1>{currentSection.title}</h1>
              <p>{currentSection.description ?? currentSection.detail}</p>
            </div>
          )}
          {section === "tools" && <SettingsTools />}
          {section === "loops" && <SettingsLoops />}
          {section === "connections" && <SettingsConnections />}
          {section === "appearance" && <SettingsAppearance />}
          {section === "about" && <SettingsAbout />}
        </div>
      </main>
    </div>
  );
}

function SettingsLoops() {
  return (
    <div className="settings-loops">
      <LoopLibraryScreen workSlug={null} embedded />
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
  const editor = useSettingsStore((s) => s.editor);
  const terminal = useSettingsStore((s) => s.terminal);
  const editorOptions = useSettingsStore((s) => s.editorOptions);
  const terminalOptions = useSettingsStore((s) => s.terminalOptions);
  const setEditor = useSettingsStore((s) => s.setEditor);
  const setTerminal = useSettingsStore((s) => s.setTerminal);
  return (
    <>
      <SettingsCard
        label="EDITOR"
        hint="Opens when an agent tile's “Open in editor” fires."
      >
        <div className="tool-grid">
          {editorOptions.map((opt) => (
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
          {terminalOptions.map((opt) => (
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

function ToolCardEditor({
  opt,
  active,
  onPick,
}: {
  opt: ToolOption;
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
        <span className="tool-cmd">${opt.command}</span>
      </div>
    </button>
  );
}

function ToolCardTerminal({
  opt,
  active,
  onPick,
}: {
  opt: ToolOption;
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
        <span className="tool-cmd">${opt.command}</span>
      </div>
    </button>
  );
}

// ─── Connections ────────────────────────────────────────────────

function SettingsConnections() {
  return <Connections chromeless />;
}

// ─── Appearance ─────────────────────────────────────────────────

function SettingsAppearance() {
  const theme = useSettingsStore((s) => s.theme);
  const setTheme = useSettingsStore((s) => s.setTheme);
  const themes: { value: Theme; label: string; swatch: string }[] = [
    { value: "light", label: "Light", swatch: "swatch-light" },
    { value: "dark", label: "Dark", swatch: "swatch-dark" },
    { value: "ansi", label: "ANSI terminal", swatch: "swatch-ansi" },
  ];
  return (
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
  );
}

// ─── About ──────────────────────────────────────────────────────

function SettingsAbout() {
  const theme = useSettingsStore((s) => s.theme);
  const rows = useMemo(
    () => [
      { label: "Atelier", value: "v4 — looping" },
      { label: "Theme", value: theme },
      { label: "Frontend", value: window.location.host },
      { label: "Backend", value: "/api on the same origin" },
    ],
    [theme],
  );
  return (
    <>
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
