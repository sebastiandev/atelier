import {
  type KeyboardEvent as ReactKeyboardEvent,
  useEffect,
  useMemo,
  useRef,
  useState,
} from "react";

import { CheckIcon, SearchIcon } from "./Icons";

type SessionConfigValue = string | boolean;
type SessionConfigChoice = {
  value: SessionConfigValue;
  name?: string;
  description?: string;
};
type SessionConfigOption = {
  id: string;
  name: string;
  currentValue: SessionConfigValue | null;
  choices: SessionConfigChoice[];
};

type SessionModelPickerProps = {
  disabled: boolean;
  option: SessionConfigOption | null;
  pickerId: string;
  refreshSeq: number;
  onBeforeChange?: () => boolean;
  onChange: (value: SessionConfigValue) => void;
  onRefresh: () => void;
};

export function SessionModelPicker({
  disabled,
  option,
  pickerId,
  refreshSeq,
  onBeforeChange,
  onChange,
  onRefresh,
}: SessionModelPickerProps) {
  const [open, setOpen] = useState(false);
  const [query, setQuery] = useState("");
  const [activeIndex, setActiveIndex] = useState(0);
  const [refreshing, setRefreshing] = useState(false);
  const refreshStartedSeqRef = useRef(0);
  const pickerRef = useRef<HTMLDivElement>(null);
  const searchRef = useRef<HTMLInputElement>(null);
  const resultsRef = useRef<HTMLDivElement>(null);

  const value = typeof option?.currentValue === "string" ? option.currentValue : null;
  const label = option && value ? labelForValue(option.choices, value) : null;
  const choices = useMemo(() => {
    if (option === null) return [];
    const normalized = normalizeModelQuery(query);
    if (!normalized) return option.choices;
    const terms = normalized.split(" ").filter(Boolean);
    return option.choices.filter((choice) => {
      const haystack = normalizeModelQuery(
        `${choice.name ?? ""} ${String(choice.value)} ${choice.description ?? ""}`,
      );
      return terms.every((term) => haystack.includes(term));
    });
  }, [option, query]);
  const shown = option !== null && value !== null && option.choices.length > 0;
  const title = label
    ? disabled
      ? `Wait for the current turn to finish before changing model (${value})`
      : `Model: ${label} (${value})`
    : undefined;

  useEffect(() => {
    if (!open) return;
    requestAnimationFrame(() => searchRef.current?.focus());
  }, [open]);
  useEffect(() => {
    if (!open) return;
    setActiveIndex(0);
  }, [choices, open]);
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
      if (target instanceof Node && pickerRef.current?.contains(target)) return;
      setOpen(false);
    };
    window.addEventListener("mousedown", close);
    window.addEventListener("scroll", close, true);
    return () => {
      window.removeEventListener("mousedown", close);
      window.removeEventListener("scroll", close, true);
    };
  }, [open]);
  useEffect(() => {
    if (!open || shown) return;
    setOpen(false);
  }, [open, shown]);
  useEffect(() => {
    if (!refreshing) return;
    if (refreshSeq > refreshStartedSeqRef.current) {
      setRefreshing(false);
      return;
    }
    const handle = window.setTimeout(() => setRefreshing(false), 1500);
    return () => window.clearTimeout(handle);
  }, [refreshing, refreshSeq]);

  if (!shown) return null;

  function openPicker() {
    if (disabled || onBeforeChange?.() === false) return;
    const nextOpen = !open;
    setOpen(nextOpen);
    setQuery("");
    setActiveIndex(0);
    if (nextOpen) {
      refreshStartedSeqRef.current = refreshSeq;
      setRefreshing(true);
      onRefresh();
    }
  }

  function choose(choice: SessionConfigChoice) {
    if (disabled || onBeforeChange?.() === false) return;
    onChange(choice.value);
    setOpen(false);
    setQuery("");
  }

  function handleSearchKeyDown(e: ReactKeyboardEvent<HTMLInputElement>) {
    if (e.key === "Escape") {
      e.preventDefault();
      setOpen(false);
      return;
    }
    const maxIndex = choices.length - 1;
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
      const choice = choices[Math.min(activeIndex, maxIndex)];
      if (choice) choose(choice);
    }
  }

  return (
    <div className="composer-model-picker" ref={pickerRef} title={title}>
      <button
        type="button"
        className="composer-model-trigger"
        disabled={disabled}
        onClick={openPicker}
        aria-haspopup="listbox"
        aria-expanded={open}
      >
        <span className="composer-model-prefix">Model:</span>
        <span className="composer-model-current">{label}</span>
        <span className="composer-model-caret" aria-hidden>
          ▾
        </span>
      </button>
      {open && (
        <div className="composer-model-menu">
          <label className="composer-model-search">
            <SearchIcon size={12} />
            <input
              ref={searchRef}
              value={query}
              onChange={(e) => setQuery(e.target.value)}
              onKeyDown={handleSearchKeyDown}
              placeholder="Search models"
              aria-controls={`${pickerId}-results`}
              aria-activedescendant={
                choices[activeIndex] ? `${pickerId}-option-${activeIndex}` : undefined
              }
            />
          </label>
          <div
            ref={resultsRef}
            id={`${pickerId}-results`}
            className="composer-model-results"
            role="listbox"
          >
            {choices.length === 0 ? (
              <div className="composer-model-empty">No models found</div>
            ) : (
              choices.map((choice, index) => {
                const selected = choice.value === value;
                const active = index === activeIndex;
                return (
                  <button
                    key={String(choice.value)}
                    id={`${pickerId}-option-${index}`}
                    type="button"
                    className="composer-model-option"
                    data-active={active ? "true" : undefined}
                    data-selected={selected ? "true" : undefined}
                    role="option"
                    aria-selected={selected}
                    onMouseEnter={() => setActiveIndex(index)}
                    onClick={() => choose(choice)}
                  >
                    <span className="composer-model-option-check">
                      {selected ? <CheckIcon size={11} /> : null}
                    </span>
                    <span className="composer-model-option-main">
                      <span className="composer-model-option-name">
                        {choice.name ?? String(choice.value)}
                      </span>
                      <span className="composer-model-option-value">
                        {String(choice.value)}
                      </span>
                    </span>
                  </button>
                );
              })
            )}
          </div>
          <div className="composer-model-foot">
            {refreshing ? "Refreshing models..." : "Type to filter"}
          </div>
        </div>
      )}
    </div>
  );
}

function labelForValue(choices: SessionConfigChoice[], value: SessionConfigValue) {
  const choice = choices.find((item) => item.value === value);
  return choice?.name ?? String(value);
}

function normalizeModelQuery(value: string): string {
  return value.toLowerCase().replace(/[^a-z0-9./:_-]+/g, " ").trim();
}
