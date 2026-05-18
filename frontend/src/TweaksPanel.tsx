import { type PointerEvent as ReactPointerEvent, useEffect, useRef } from "react";

import { SETTINGS_DEFAULTS, useSettingsStore } from "./state/settings";

const PAD = 16;
const DEV_FLAG_KEY = "atelier:dev-tweaks";

// Dev-only colour playground. Off by default; enable with
// `localStorage.setItem("atelier:dev-tweaks", "true")` then reload.
// The editor / console / canvas-layout knobs that used to live here
// moved into the Settings screen (Default tools section); this panel
// is now purely for accent-hue iteration.
function isDevTweaksEnabled(): boolean {
  try {
    return window.localStorage.getItem(DEV_FLAG_KEY) === "true";
  } catch {
    return false;
  }
}

export function TweaksPanel() {
  const open = useSettingsStore((s) => s.panelOpen);
  const accentHue = useSettingsStore((s) => s.accentHue);
  const setAccentHue = useSettingsStore((s) => s.setAccentHue);
  const resetTweaks = useSettingsStore((s) => s.reset);
  const closePanel = useSettingsStore((s) => s.closePanel);
  const atDefault = accentHue === SETTINGS_DEFAULTS.accentHue;

  const panelRef = useRef<HTMLDivElement>(null);
  const offsetRef = useRef({ x: PAD, y: PAD });

  const clamp = () => {
    const el = panelRef.current;
    if (!el) return;
    const w = el.offsetWidth;
    const h = el.offsetHeight;
    const maxRight = Math.max(PAD, window.innerWidth - w - PAD);
    const maxBottom = Math.max(PAD, window.innerHeight - h - PAD);
    offsetRef.current = {
      x: Math.min(maxRight, Math.max(PAD, offsetRef.current.x)),
      y: Math.min(maxBottom, Math.max(PAD, offsetRef.current.y)),
    };
    el.style.right = `${offsetRef.current.x}px`;
    el.style.bottom = `${offsetRef.current.y}px`;
  };

  useEffect(() => {
    if (!open) return;
    clamp();
    const onResize = () => clamp();
    window.addEventListener("resize", onResize);
    return () => window.removeEventListener("resize", onResize);
  }, [open]);

  function onDragStart(e: ReactPointerEvent<HTMLDivElement>) {
    const el = panelRef.current;
    if (!el) return;
    e.preventDefault();
    const r = el.getBoundingClientRect();
    const sx = e.clientX;
    const sy = e.clientY;
    const startRight = window.innerWidth - r.right;
    const startBottom = window.innerHeight - r.bottom;
    function move(ev: PointerEvent) {
      offsetRef.current = {
        x: startRight - (ev.clientX - sx),
        y: startBottom - (ev.clientY - sy),
      };
      clamp();
    }
    function up() {
      window.removeEventListener("pointermove", move);
      window.removeEventListener("pointerup", up);
    }
    window.addEventListener("pointermove", move);
    window.addEventListener("pointerup", up);
  }

  if (!open) return null;
  if (!isDevTweaksEnabled()) return null;

  return (
    <div
      ref={panelRef}
      className="twk-panel"
      role="dialog"
      aria-label="Tweaks"
      style={{ right: offsetRef.current.x, bottom: offsetRef.current.y }}
    >
      <div className="twk-hd" onPointerDown={onDragStart}>
        <b>Tweaks</b>
        <button
          type="button"
          className="twk-x"
          aria-label="Close tweaks"
          onPointerDown={(e) => e.stopPropagation()}
          onClick={closePanel}
        >
          ×
        </button>
      </div>
      <div className="twk-body">
        <div className="twk-sect">Accent</div>
        <div className="twk-row">
          <div className="twk-lbl">
            <span>Hue</span>
            <span className="twk-val">{accentHue}°</span>
          </div>
          <input
            type="range"
            className="twk-slider"
            min={0}
            max={360}
            step={1}
            value={accentHue}
            onChange={(e) => setAccentHue(Number(e.target.value))}
          />
        </div>
        <div className="twk-hint">
          Drives the <code>--accent-h</code> custom property on{" "}
          <code>&lt;html&gt;</code>. The rest of the accent ramp
          derives via <code>oklch()</code>. Dev-only — enable by
          setting <code>localStorage.atelier:dev-tweaks = "true"</code>.
        </div>

        <div className="twk-foot">
          <button
            type="button"
            className="twk-reset"
            onClick={resetTweaks}
            disabled={atDefault}
            title={atDefault ? "Already at default" : "Reset accent to default"}
          >
            Reset to default
          </button>
        </div>
      </div>
    </div>
  );
}

// Trigger for the panel. Renders only when the dev flag is on so
// production builds don't carry the gear icon into the v3 shell.
export function TweaksToggle() {
  const togglePanel = useSettingsStore((s) => s.togglePanel);
  const open = useSettingsStore((s) => s.panelOpen);
  if (!isDevTweaksEnabled()) return null;
  return (
    <button
      type="button"
      className="theme-toggle"
      onClick={togglePanel}
      aria-label="Tweaks"
      aria-pressed={open}
      title="Tweaks (dev)"
    >
      <GearIcon />
    </button>
  );
}

function GearIcon() {
  return (
    <svg viewBox="0 0 16 16" width="14" height="14" aria-hidden>
      <path
        d="M8 5.5a2.5 2.5 0 100 5 2.5 2.5 0 000-5z M13.5 8c0-.4-.04-.8-.12-1.18l1.27-.99-1.5-2.6-1.5.6a4.4 4.4 0 00-2.04-1.18L9.4.5h-3l-.21 1.65a4.4 4.4 0 00-2.04 1.17l-1.5-.6-1.5 2.6 1.27 1c-.08.38-.12.78-.12 1.18 0 .4.04.8.12 1.18l-1.27.99 1.5 2.6 1.5-.6c.6.5 1.28.9 2.04 1.17L6.4 15.5h3l.21-1.65a4.4 4.4 0 002.04-1.18l1.5.6 1.5-2.6-1.27-.99c.08-.38.12-.78.12-1.18z"
        fill="none"
        stroke="currentColor"
        strokeWidth="1.1"
        strokeLinejoin="round"
      />
    </svg>
  );
}
