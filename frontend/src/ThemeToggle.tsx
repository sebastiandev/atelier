import { type Theme, useThemeStore } from "./state/theme";

// Cycle order matches state/theme.ts → light → dark → ansi → light.
// The toggle icon previews the *next* theme so the affordance reads
// like "press to go there".
const NEXT_LABEL: Record<Theme, Theme> = {
  light: "dark",
  dark: "ansi",
  ansi: "light",
};

export function ThemeToggle() {
  const theme = useThemeStore((s) => s.theme);
  const toggle = useThemeStore((s) => s.toggleTheme);
  const next = NEXT_LABEL[theme];
  return (
    <button
      type="button"
      className="theme-toggle"
      title={`Switch to ${next} theme`}
      aria-label={`Switch to ${next} theme`}
      onClick={toggle}
    >
      <PreviewIcon theme={next} />
    </button>
  );
}

function PreviewIcon({ theme }: { theme: Theme }) {
  switch (theme) {
    case "light":
      return <SunIcon />;
    case "dark":
      return <MoonIcon />;
    case "ansi":
      return <TerminalIcon />;
  }
}

function SunIcon() {
  return (
    <svg viewBox="0 0 16 16" width="14" height="14" aria-hidden>
      <circle cx="8" cy="8" r="3" fill="none" stroke="currentColor" strokeWidth="1.4" />
      <path
        d="M8 1.5v2M8 12.5v2M1.5 8h2M12.5 8h2M3.3 3.3l1.4 1.4M11.3 11.3l1.4 1.4M3.3 12.7l1.4-1.4M11.3 4.7l1.4-1.4"
        stroke="currentColor"
        strokeWidth="1.4"
        strokeLinecap="round"
      />
    </svg>
  );
}

function MoonIcon() {
  return (
    <svg viewBox="0 0 16 16" width="14" height="14" aria-hidden>
      <path
        d="M13 9.5A5.5 5.5 0 0 1 6.5 3a5.5 5.5 0 1 0 6.5 6.5z"
        fill="none"
        stroke="currentColor"
        strokeWidth="1.4"
        strokeLinejoin="round"
      />
    </svg>
  );
}

function TerminalIcon() {
  return (
    <svg viewBox="0 0 16 16" width="14" height="14" aria-hidden>
      <rect
        x="1.5"
        y="3"
        width="13"
        height="10"
        rx="1.5"
        fill="none"
        stroke="currentColor"
        strokeWidth="1.4"
      />
      <path
        d="M4 7l2 1.5L4 10M7.5 10.5h4"
        fill="none"
        stroke="currentColor"
        strokeWidth="1.4"
        strokeLinecap="round"
        strokeLinejoin="round"
      />
    </svg>
  );
}
