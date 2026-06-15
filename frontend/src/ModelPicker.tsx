import {
  type CSSProperties,
  type KeyboardEvent as ReactKeyboardEvent,
  useEffect,
  useMemo,
  useRef,
  useState,
} from "react";
import { createPortal } from "react-dom";

import { CheckIcon, SearchIcon } from "./Icons";

export type ModelPickerOption = {
  value: string;
  label: string;
};

type Props = {
  id: string;
  value: string;
  options: ModelPickerOption[];
  onChange: (value: string) => void;
  className?: string;
};

type ModelPickerMenuStyle = CSSProperties & {
  "--model-picker-results-h": string;
};

export function ModelPicker({ id, value, options, onChange, className }: Props) {
  const [open, setOpen] = useState(false);
  const [query, setQuery] = useState("");
  const [activeIndex, setActiveIndex] = useState(0);
  const [menuStyle, setMenuStyle] = useState<ModelPickerMenuStyle | null>(null);
  const rootRef = useRef<HTMLDivElement>(null);
  const menuRef = useRef<HTMLDivElement>(null);
  const inputRef = useRef<HTMLInputElement>(null);
  const resultsRef = useRef<HTMLDivElement>(null);
  const selected = options.find((option) => option.value === value);
  const filtered = useMemo(() => {
    const normalized = normalizeModelQuery(query);
    if (!normalized) return options;
    const terms = normalized.split(" ").filter(Boolean);
    return options.filter((option) => {
      const haystack = normalizeModelQuery(`${option.label} ${option.value}`);
      return terms.every((term) => haystack.includes(term));
    });
  }, [options, query]);

  useEffect(() => {
    if (!open) return;
    requestAnimationFrame(() => inputRef.current?.focus());
  }, [open]);

  useEffect(() => {
    if (!open) return;
    function updatePosition() {
      const rect = rootRef.current?.getBoundingClientRect();
      if (!rect) return;
      const gap = 6;
      const margin = 12;
      const width = Math.min(Math.max(rect.width, 360), window.innerWidth - margin * 2);
      const left = Math.min(
        Math.max(margin, rect.left),
        Math.max(margin, window.innerWidth - width - margin),
      );
      const roomBelow = window.innerHeight - rect.bottom - margin;
      const roomAbove = rect.top - margin;
      const openUp = roomBelow < 300 && roomAbove > roomBelow;
      const maxHeight = Math.max(
        180,
        Math.min(320, (openUp ? roomAbove : roomBelow) - gap - 54),
      );
      setMenuStyle({
        position: "fixed",
        zIndex: 1000,
        left,
        width,
        maxWidth: `calc(100vw - ${margin * 2}px)`,
        ...(openUp
          ? { bottom: window.innerHeight - rect.top + gap }
          : { top: rect.bottom + gap }),
        "--model-picker-results-h": `${maxHeight}px`,
      });
    }
    updatePosition();
    window.addEventListener("resize", updatePosition);
    window.addEventListener("scroll", updatePosition, true);
    return () => {
      window.removeEventListener("resize", updatePosition);
      window.removeEventListener("scroll", updatePosition, true);
    };
  }, [open, options.length]);

  useEffect(() => {
    if (!open) return;
    setActiveIndex(0);
  }, [filtered, open]);

  useEffect(() => {
    if (!open) return;
    const active = resultsRef.current?.querySelector<HTMLElement>(
      '[data-active="true"]',
    );
    active?.scrollIntoView({ block: "nearest" });
  }, [activeIndex, open]);

  useEffect(() => {
    if (!open) return;
    const close = (event: Event) => {
      const target = event.target;
      if (target instanceof Node && rootRef.current?.contains(target)) return;
      if (target instanceof Node && menuRef.current?.contains(target)) return;
      setOpen(false);
    };
    window.addEventListener("mousedown", close);
    window.addEventListener("scroll", close, true);
    return () => {
      window.removeEventListener("mousedown", close);
      window.removeEventListener("scroll", close, true);
    };
  }, [open]);

  function choose(option: ModelPickerOption) {
    onChange(option.value);
    setOpen(false);
    setQuery("");
  }

  function onSearchKeyDown(e: ReactKeyboardEvent<HTMLInputElement>) {
    if (e.key === "Escape") {
      e.preventDefault();
      setOpen(false);
      return;
    }
    const maxIndex = filtered.length - 1;
    if (maxIndex < 0) return;
    if (e.key === "ArrowDown") {
      e.preventDefault();
      setActiveIndex((index) => Math.min(index + 1, maxIndex));
      return;
    }
    if (e.key === "ArrowUp") {
      e.preventDefault();
      setActiveIndex((index) => Math.max(index - 1, 0));
      return;
    }
    if (e.key === "Home") {
      e.preventDefault();
      setActiveIndex(0);
      return;
    }
    if (e.key === "End") {
      e.preventDefault();
      setActiveIndex(maxIndex);
      return;
    }
    if (e.key === "Enter") {
      e.preventDefault();
      const option = filtered[Math.min(activeIndex, maxIndex)];
      if (option) choose(option);
    }
  }

  return (
    <div className={`model-picker ${className ?? ""}`} ref={rootRef}>
      <button
        type="button"
        className="model-picker-trigger"
        onClick={() => {
          setOpen((current) => !current);
          setQuery("");
          setActiveIndex(0);
        }}
        aria-haspopup="listbox"
        aria-expanded={open}
      >
        <span className="model-picker-label">{selected?.label ?? value}</span>
        <span className="model-picker-value">{selected?.value ?? value}</span>
        <span className="model-picker-caret" aria-hidden>
          ▾
        </span>
      </button>
      {open &&
        menuStyle &&
        createPortal(
          <div className="model-picker-menu" ref={menuRef} style={menuStyle}>
            <label className="model-picker-search">
              <SearchIcon size={12} />
              <input
                ref={inputRef}
                value={query}
                onChange={(e) => setQuery(e.target.value)}
                onKeyDown={onSearchKeyDown}
                placeholder="Search models"
                aria-controls={`${id}-results`}
                aria-activedescendant={
                  filtered[activeIndex] ? `${id}-option-${activeIndex}` : undefined
                }
              />
            </label>
            <div
              ref={resultsRef}
              id={`${id}-results`}
              className="model-picker-results"
              role="listbox"
            >
              {filtered.length === 0 ? (
                <div className="model-picker-empty">No models found</div>
              ) : (
                filtered.map((option, index) => {
                  const selectedOption = option.value === value;
                  const active = index === activeIndex;
                  return (
                    <button
                      key={option.value}
                      id={`${id}-option-${index}`}
                      type="button"
                      className="model-picker-option"
                      data-active={active ? "true" : undefined}
                      data-selected={selectedOption ? "true" : undefined}
                      role="option"
                      aria-selected={selectedOption}
                      onMouseEnter={() => setActiveIndex(index)}
                      onClick={() => choose(option)}
                    >
                      <span className="model-picker-option-check">
                        {selectedOption ? <CheckIcon size={11} /> : null}
                      </span>
                      <span className="model-picker-option-main">
                        <span className="model-picker-option-label">
                          {option.label}
                        </span>
                        <span className="model-picker-option-value">
                          {option.value}
                        </span>
                      </span>
                    </button>
                  );
                })
              )}
            </div>
          </div>,
          document.body,
        )}
    </div>
  );
}

function normalizeModelQuery(value: string): string {
  return value.toLowerCase().replace(/[^a-z0-9./:_-]+/g, " ").trim();
}
