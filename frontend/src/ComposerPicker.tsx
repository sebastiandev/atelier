/**
 * The composer picker — one popover recipe, two triggers.
 *
 * `/` lists the agent's advertised commands, `@` lists plan references.
 * Same container, header, row grid, footer and keyboard contract; only
 * the contents and the row's mono/prose order differ, per the type rule:
 * **what you insert is mono, what you read is UI type**. The `/` variant
 * therefore puts the token on the primary line and the `@` variant puts
 * the title there — handled in CSS off `[data-trigger]`, so this
 * component stays one shape.
 *
 * Design: `design/Composer Picker Handoff.md`, spec §06.
 */
import type { KeyboardEvent as ReactKeyboardEvent, ReactNode } from "react";
import { useEffect, useRef } from "react";

import { splitMatches } from "./pickerSearch";

/** `text` with every matched run wrapped in the accent mark (as ⌘K). */
export function markMatches(text: string, terms: string[]): ReactNode {
  const segments = splitMatches(text, terms);
  if (segments.length === 1 && !segments[0].marked) return text;
  return segments.map((segment, i) =>
    segment.marked ? (
      <mark key={i} className="picker-mark">
        {segment.text}
      </mark>
    ) : (
      segment.text
    ),
  );
}

export type PickerFilter = {
  id: string;
  label: string;
  count: number;
};

export type PickerItem = {
  /** React key + the value handed back to `onPick`. */
  id: string;
  /** Slot tag: PRJ / USR / OC for commands, the kind ramp for references. */
  tag: string;
  /** Drives the tag's tone ramp; omit for the neutral bg-3 tag. */
  tone?: "info" | "warn" | "danger";
  primary: ReactNode;
  secondary: ReactNode;
};

export function ComposerPicker({
  trigger,
  title,
  items,
  filters,
  activeFilter,
  onFilter,
  selectedIndex,
  onPick,
  footerVerb,
  total,
  emptyLabel,
  listLabel,
}: {
  trigger: "slash" | "at";
  title: string;
  items: readonly PickerItem[];
  filters?: readonly PickerFilter[];
  activeFilter?: string;
  onFilter?: (id: string) => void;
  selectedIndex: number;
  onPick: (id: string) => void;
  footerVerb: string;
  total: number;
  emptyLabel: string;
  listLabel: string;
}) {
  const optionRefs = useRef<Array<HTMLButtonElement | null>>([]);

  useEffect(() => {
    optionRefs.current.length = items.length;
  }, [items.length]);

  useEffect(() => {
    if (selectedIndex < 0) return;
    optionRefs.current[selectedIndex]?.scrollIntoView({ block: "nearest" });
  }, [items.length, selectedIndex]);

  return (
    <div className="composer-picker" data-trigger={trigger}>
      <div className="composer-picker-head">
        <span className="composer-picker-title">{title}</span>
        {filters && filters.length > 0 && (
          <div className="composer-picker-filters">
            {filters.map((filter) => (
              <button
                key={filter.id}
                type="button"
                className={filter.id === activeFilter ? "active" : undefined}
                aria-pressed={filter.id === activeFilter}
                onMouseDown={(event) => event.preventDefault()}
                onClick={() => onFilter?.(filter.id)}
              >
                <span>{filter.label}</span>
                <span>{filter.count}</span>
              </button>
            ))}
          </div>
        )}
      </div>
      <div className="composer-picker-list" role="listbox" aria-label={listLabel}>
        {items.length > 0 ? (
          items.map((item, index) => (
            <button
              key={item.id}
              ref={(node) => {
                optionRefs.current[index] = node;
              }}
              type="button"
              role="option"
              aria-selected={index === selectedIndex}
              className={index === selectedIndex ? "active" : undefined}
              // mousedown, not click: focus must never leave the textarea.
              onMouseDown={(event) => {
                event.preventDefault();
                onPick(item.id);
              }}
            >
              <span className="picker-tag" data-tone={item.tone}>
                {item.tag}
              </span>
              <span className="picker-row-body">
                <span className="picker-row-primary">{item.primary}</span>
                <span className="picker-row-secondary">{item.secondary}</span>
              </span>
              {index === selectedIndex && <span className="key">↵</span>}
            </button>
          ))
        ) : (
          <div className="composer-picker-empty">
            <b>{emptyLabel}</b>
            <span>esc keeps what you typed · space closes the picker</span>
          </div>
        )}
      </div>
      <div className="composer-picker-foot">
        <span className="seg">
          <span className="key">↑</span>
          <span className="key">↓</span> navigate
        </span>
        <span className="seg">
          <span className="key">↵</span> {footerVerb}
        </span>
        <span className="seg">
          <span className="key">esc</span> close
        </span>
        <span className="composer-picker-count">
          {items.length === total ? `${total}` : `${items.length} of ${total}`}
        </span>
      </div>
    </div>
  );
}

/**
 * One keystroke for an open picker; true when it was consumed.
 *
 * Shared by both triggers so the arrow/enter/esc contract cannot drift.
 * The caller bails on true, which is what keeps Esc reserved for
 * stop-agent whenever no picker is open.
 */
export function pickerKeyDown(
  e: ReactKeyboardEvent<HTMLTextAreaElement>,
  {
    count,
    selectedIndex,
    onMove,
    onPick,
    onDismiss,
  }: {
    count: number;
    selectedIndex: number;
    onMove: (nextIndex: number) => void;
    onPick: (index: number) => void;
    onDismiss: () => void;
  },
): boolean {
  if (e.key === "Escape") {
    e.preventDefault();
    onDismiss();
    return true;
  }
  if (count === 0) return false;
  if (e.key === "ArrowDown") {
    e.preventDefault();
    onMove((selectedIndex + 1) % count);
    return true;
  }
  if (e.key === "ArrowUp") {
    e.preventDefault();
    onMove((selectedIndex - 1 + count) % count);
    return true;
  }
  if (e.key === "Enter" || e.key === "Tab") {
    e.preventDefault();
    onPick(selectedIndex);
    return true;
  }
  return false;
}
