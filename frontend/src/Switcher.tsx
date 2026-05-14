import { useEffect, useMemo, useRef, useState } from "react";

// Generic palette-style switcher. Callers shape their domain entities
// (projects, works) into `SwitcherItem[]` and pass them in; the component
// itself doesn't know about either. Arrow keys move the highlight, Enter
// opens the highlighted row, Esc closes (with stopPropagation so the
// global stop-agent listener never sees it).

export type SwitcherItem = {
  slug: string;
  name: string;
  // Optional second-line context (e.g. "Atelier core · loose" for a work
  // row that shows its parent project).
  subtitle?: string;
  // Glyph shown in the left chip (project monogram, work icon, etc.).
  glyph?: string;
  // OKLCH hue 0–360 — used to tint the row chip via --proj-h. Omit for
  // rows that should stay neutral (loose work, etc.).
  hue?: number;
  // Browser navigates here on Enter / click. Full-page nav by design —
  // matches the rest of the app's hand-rolled router.
  href: string;
};

type Props = {
  placeholder: string;
  items: SwitcherItem[];
  onClose: () => void;
  emptyMessage?: string;
};

export function Switcher({ placeholder, items, onClose, emptyMessage }: Props) {
  const [query, setQuery] = useState("");
  const [highlight, setHighlight] = useState(0);
  const inputRef = useRef<HTMLInputElement>(null);
  const listRef = useRef<HTMLDivElement>(null);

  const filtered = useMemo(() => {
    const q = query.trim().toLowerCase();
    if (!q) return items;
    return items.filter(
      (it) =>
        it.name.toLowerCase().includes(q) ||
        it.slug.toLowerCase().includes(q) ||
        (it.subtitle?.toLowerCase().includes(q) ?? false),
    );
  }, [items, query]);

  // Reset highlight whenever the filter narrows/widens — otherwise the
  // index points past the end and Enter becomes a no-op.
  useEffect(() => {
    setHighlight(0);
  }, [query]);

  useEffect(() => {
    inputRef.current?.focus();
  }, []);

  useEffect(() => {
    function onKey(e: KeyboardEvent) {
      if (e.key === "Escape") {
        // Stop the global stop-agent listener (and anything else) from
        // also reacting — matches FolderPickerDialog's convention.
        e.stopPropagation();
        onClose();
        return;
      }
      if (e.key === "ArrowDown") {
        e.preventDefault();
        setHighlight((h) => Math.min(filtered.length - 1, h + 1));
      } else if (e.key === "ArrowUp") {
        e.preventDefault();
        setHighlight((h) => Math.max(0, h - 1));
      } else if (e.key === "Enter") {
        const target = filtered[highlight];
        if (target) {
          e.preventDefault();
          window.location.assign(target.href);
        }
      }
    }
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [filtered, highlight, onClose]);

  useEffect(() => {
    const row = listRef.current?.querySelector<HTMLElement>(
      `[data-row="${highlight}"]`,
    );
    row?.scrollIntoView({ block: "nearest" });
  }, [highlight]);

  return (
    <div className="scrim switcher-scrim" onClick={onClose}>
      <div
        className="switcher"
        role="dialog"
        aria-modal="true"
        onClick={(e) => e.stopPropagation()}
      >
        <div className="switcher-hd">
          <SearchIcon />
          <input
            ref={inputRef}
            className="switcher-input"
            value={query}
            onChange={(e) => setQuery(e.target.value)}
            placeholder={placeholder}
            spellCheck={false}
            autoComplete="off"
          />
        </div>
        <div className="switcher-list" ref={listRef}>
          {filtered.length === 0 ? (
            <div className="switcher-empty hint">
              {emptyMessage ?? "No matches"}
            </div>
          ) : (
            filtered.map((it, i) => (
              <a
                key={it.slug}
                href={it.href}
                className={
                  "switcher-row" + (i === highlight ? " is-active" : "")
                }
                data-row={i}
                onMouseEnter={() => setHighlight(i)}
                style={
                  it.hue !== undefined
                    ? { ["--proj-h" as string]: String(it.hue) }
                    : undefined
                }
              >
                <span
                  className={"switcher-glyph" + (it.hue !== undefined ? " tinted" : "")}
                  aria-hidden="true"
                >
                  {it.glyph ?? "•"}
                </span>
                <div className="switcher-text">
                  <div className="switcher-name">{it.name}</div>
                  {it.subtitle && (
                    <div className="switcher-sub">{it.subtitle}</div>
                  )}
                </div>
                <span className="switcher-slug mono">{it.slug}</span>
              </a>
            ))
          )}
        </div>
        <div className="switcher-ft">
          <span><kbd>↑</kbd><kbd>↓</kbd> navigate</span>
          <span><kbd>↵</kbd> open</span>
          <span><kbd>esc</kbd> close</span>
        </div>
      </div>
    </div>
  );
}

function SearchIcon() {
  return (
    <svg
      width="14"
      height="14"
      viewBox="0 0 16 16"
      fill="none"
      aria-hidden="true"
    >
      <circle cx="7" cy="7" r="4.5" stroke="currentColor" strokeWidth="1.4" />
      <path
        d="M10.5 10.5 L14 14"
        stroke="currentColor"
        strokeWidth="1.4"
        strokeLinecap="round"
      />
    </svg>
  );
}

// Small affordance shown next to a breadcrumb crumb. Clicking it opens
// the switcher (callers wire the onClick + open state). Distinct from
// the crumb link itself so the link still navigates on plain click.
export function SwitcherChevron({
  onClick,
  title,
}: {
  onClick: () => void;
  title: string;
}) {
  return (
    <button
      type="button"
      className="crumb-switch"
      onClick={onClick}
      title={title}
      aria-label={title}
    >
      <svg
        width="10"
        height="10"
        viewBox="0 0 12 12"
        fill="none"
        aria-hidden="true"
      >
        <path
          d="M3 4.5 L6 7.5 L9 4.5"
          stroke="currentColor"
          strokeWidth="1.4"
          strokeLinecap="round"
          strokeLinejoin="round"
        />
      </svg>
    </button>
  );
}
