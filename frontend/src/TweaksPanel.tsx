import { type PointerEvent as ReactPointerEvent, useEffect, useRef } from "react";

import {
  type CanvasLayout,
  EDITOR_OPTS,
  type EditorChoice,
  TWEAKS_DEFAULTS,
  useTweaksStore,
} from "./state/tweaks";

const PAD = 16;

/**
 * Floating tweaks panel — accent hue + canvas layout.
 *
 * Always rendered (fixed position) but only visible when
 * `tweaksStore.panelOpen` is true. The header is draggable; the close
 * button (×) sets `panelOpen=false`.
 *
 * The trigger lives in the topbars (`TweaksToggle`) — Home / WorkView /
 * Connections all mount it next to the theme toggle. We keep the panel
 * itself at App scope so the gear icon can fire from anywhere.
 */
export function TweaksPanel() {
  const open = useTweaksStore((s) => s.panelOpen);
  const accentHue = useTweaksStore((s) => s.accentHue);
  const layout = useTweaksStore((s) => s.layout);
  const editor = useTweaksStore((s) => s.editor);
  const setAccentHue = useTweaksStore((s) => s.setAccentHue);
  const setLayout = useTweaksStore((s) => s.setLayout);
  const setEditor = useTweaksStore((s) => s.setEditor);
  const resetTweaks = useTweaksStore((s) => s.reset);
  const closePanel = useTweaksStore((s) => s.closePanel);
  const atDefault =
    accentHue === TWEAKS_DEFAULTS.accentHue &&
    layout === TWEAKS_DEFAULTS.layout &&
    editor === TWEAKS_DEFAULTS.editor;

  // Position is held in the panel only — not persisted. The default
  // (bottom-right with 16px padding) matches the prototype.
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

  // Re-clamp on open and on viewport resize so the panel never escapes.
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

        <div className="twk-sect">Agent layout</div>
        <LayoutRadio value={layout} onChange={setLayout} />
        <div className="twk-hint">
          Tiles snap into a responsive grid; columns flow horizontally.
          Drag a tile by its grip in the top-left to reorder.
        </div>

        <div className="twk-sect">Open in editor</div>
        <div className="twk-row">
          <select
            className="twk-select"
            value={editor}
            onChange={(e) => setEditor(e.target.value as EditorChoice)}
            aria-label="Editor for the Open-in-editor button"
          >
            {EDITOR_OPTS.map((o) => (
              <option key={o.value} value={o.value}>
                {o.label}
              </option>
            ))}
          </select>
        </div>
        <div className="twk-hint">
          The IDE the agent tile's editor button hands the worktree off
          to. Requires the chosen IDE (or JetBrains Toolbox) to have
          registered its URL handler with the OS.
        </div>

        <div className="twk-foot">
          <button
            type="button"
            className="twk-reset"
            onClick={resetTweaks}
            disabled={atDefault}
            title={atDefault ? "Already at defaults" : "Reset accent + layout to defaults"}
          >
            Reset to defaults
          </button>
        </div>
      </div>
    </div>
  );
}

const LAYOUT_OPTS: { value: CanvasLayout; label: string }[] = [
  { value: "tiles", label: "Tiles" },
  { value: "columns", label: "Columns" },
];

function LayoutRadio({
  value,
  onChange,
}: {
  value: CanvasLayout;
  onChange: (v: CanvasLayout) => void;
}) {
  const idx = Math.max(
    0,
    LAYOUT_OPTS.findIndex((o) => o.value === value),
  );
  const n = LAYOUT_OPTS.length;
  return (
    <div className="twk-seg" role="radiogroup">
      <div
        className="twk-seg-thumb"
        style={{
          left: `calc(2px + ${idx} * (100% - 4px) / ${n})`,
          width: `calc((100% - 4px) / ${n})`,
        }}
      />
      {LAYOUT_OPTS.map((o) => (
        <button
          key={o.value}
          type="button"
          role="radio"
          aria-checked={o.value === value}
          onClick={() => onChange(o.value)}
        >
          {o.label}
        </button>
      ))}
    </div>
  );
}

/**
 * Gear icon trigger — toggles the panel. Place in topbars next to ThemeToggle.
 */
export function TweaksToggle() {
  const togglePanel = useTweaksStore((s) => s.togglePanel);
  const open = useTweaksStore((s) => s.panelOpen);
  return (
    <button
      type="button"
      className="theme-toggle"
      onClick={togglePanel}
      aria-label="Tweaks"
      aria-pressed={open}
      title="Tweaks"
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
