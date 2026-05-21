import { useCallback, useEffect, useMemo, useRef, useState } from "react";

import { type FolderListing, listFolder } from "./api";

type PickerMode = "folder" | "file";

type Props = {
  /** Where to land when the modal opens. Falls back to the user's
   *  $HOME on the backend when empty / null. */
  initialPath?: string | null;
  /** ``"folder"`` (default) picks a directory; file rows are visible but
   *  not selectable. ``"file"`` flips it: clicking a file picks it
   *  immediately; the "Use this folder" footer disappears. */
  mode?: PickerMode;
  onCancel: () => void;
  /** Called with the absolute path of the picked folder or file. */
  onPick: (path: string) => void;
};

/**
 * Backend-driven folder browser.
 *
 * Renders a one-level listing of the current path's children + a
 * breadcrumb above. Clicking a directory drills in; clicking a crumb
 * navigates up. "Use this folder" returns the *current* path, not a
 * row — picking a directory you're inside is the common case (the user
 * already navigated *into* the folder they want to use).
 *
 * Files are listed but not selectable — agents need a directory for
 * their worktree. Hidden entries (dotfiles) hide by default and surface
 * via a small toggle.
 *
 * Keyboard:
 *   - Esc: cancel
 *   - ↑ / ↓: move row selection
 *   - Enter: drill into the highlighted directory (no-op on a file row)
 *   - The "Use this folder" button picks the current directory itself.
 */
export function FolderPickerDialog({
  initialPath,
  mode = "folder",
  onCancel,
  onPick,
}: Props) {
  const [listing, setListing] = useState<FolderListing | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [showHidden, setShowHidden] = useState(false);
  const [highlight, setHighlight] = useState(0);
  const [pendingPath, setPendingPath] = useState<string | null>(
    initialPath ?? null,
  );
  const listRef = useRef<HTMLUListElement>(null);

  const load = useCallback(
    async (target: string | null, hidden: boolean) => {
      setError(null);
      try {
        const data = await listFolder(target, hidden);
        setListing(data);
        setHighlight(0);
        setPendingPath(data.path);
      } catch (e) {
        // Common case: user typed (or we were handed) a path that
        // doesn't exist anymore. Fall back to $HOME so the picker stays
        // usable rather than landing in a stuck error state.
        if (target !== null) {
          try {
            const home = await listFolder(null, hidden);
            setListing(home);
            setHighlight(0);
            setPendingPath(home.path);
            setError(`${target} not available — showing home instead`);
            return;
          } catch (inner) {
            setError((inner as Error).message);
            return;
          }
        }
        setError((e as Error).message);
      }
    },
    [],
  );

  useEffect(() => {
    void load(initialPath ?? null, showHidden);
  }, [load, initialPath, showHidden]);

  // Esc + arrow keys. Modal traps focus through aria-modal + the scrim
  // click handler; this listener handles the ones React inputs swallow.
  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      if (e.key === "Escape") {
        e.stopPropagation();
        onCancel();
        return;
      }
      if (!listing) return;
      const max = listing.entries.length - 1;
      if (e.key === "ArrowDown") {
        e.preventDefault();
        setHighlight((h) => Math.min(max, h + 1));
      } else if (e.key === "ArrowUp") {
        e.preventDefault();
        setHighlight((h) => Math.max(0, h - 1));
      } else if (e.key === "Enter") {
        const target = listing.entries[highlight];
        if (target?.is_dir) {
          const next = joinPath(listing.path, target.name);
          void load(next, showHidden);
        } else if (target && mode === "file") {
          onPick(joinPath(listing.path, target.name));
        }
      }
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [listing, highlight, showHidden, load, mode, onCancel, onPick]);

  // Keep the highlighted row in view when arrow-keys push it past the
  // visible window of the scroll container.
  useEffect(() => {
    if (!listRef.current) return;
    const li = listRef.current.querySelector<HTMLLIElement>(
      `li[data-row="${highlight}"]`,
    );
    li?.scrollIntoView({ block: "nearest" });
  }, [highlight]);

  const crumbs = useMemo(
    () => (listing ? splitPath(listing.path) : []),
    [listing],
  );

  return (
    <div className="scrim" onClick={onCancel}>
      <div
        className="modal modal-lg folder-picker"
        role="dialog"
        aria-modal="true"
        onClick={(e) => e.stopPropagation()}
      >
        <div className="modal-hd">
          <div>
            <h3>{mode === "file" ? "Pick a file" : "Pick a folder"}</h3>
            <p className="sub">
              {listing ? listing.path : "Loading…"}
            </p>
          </div>
          <button
            className="btn-icon"
            onClick={onCancel}
            aria-label="Close"
            type="button"
          >
            ×
          </button>
        </div>
        <div className="modal-bd folder-picker-bd">
          <nav className="folder-picker-crumbs" aria-label="Path">
            {crumbs.map((crumb) => (
              <button
                key={crumb.path}
                type="button"
                className="crumb"
                onClick={() => void load(crumb.path, showHidden)}
              >
                {crumb.label}
              </button>
            ))}
          </nav>
          {error && <div className="form-error">{error}</div>}
          {listing && (
            <ul ref={listRef} className="folder-picker-list">
              {listing.entries.length === 0 && (
                <li className="folder-picker-empty">(empty)</li>
              )}
              {listing.entries.map((entry, idx) => {
                const isDir = entry.is_dir;
                const isHighlight = idx === highlight;
                const next = joinPath(listing.path, entry.name);
                return (
                  <li
                    key={entry.name}
                    data-row={idx}
                    className={[
                      "folder-picker-row",
                      isDir ? "is-dir" : "is-file",
                      isHighlight ? "is-highlight" : "",
                      entry.is_hidden ? "is-hidden" : "",
                    ]
                      .filter(Boolean)
                      .join(" ")}
                  >
                    <button
                      type="button"
                      className="folder-picker-row-btn"
                      disabled={!isDir && mode === "folder"}
                      onClick={() => {
                        if (isDir) {
                          void load(next, showHidden);
                        } else if (mode === "file") {
                          onPick(next);
                        }
                      }}
                      onMouseEnter={() => setHighlight(idx)}
                    >
                      <span className="folder-picker-glyph" aria-hidden>
                        {isDir ? "▸" : "·"}
                      </span>
                      <span className="folder-picker-name">{entry.name}</span>
                    </button>
                  </li>
                );
              })}
            </ul>
          )}
        </div>
        <div className="modal-ft folder-picker-ft">
          <label className="folder-picker-hidden-toggle">
            <input
              type="checkbox"
              checked={showHidden}
              onChange={(e) => setShowHidden(e.target.checked)}
            />
            Show hidden
          </label>
          <span className="spacer" />
          <button className="btn" type="button" onClick={onCancel}>
            Cancel
          </button>
          {mode === "folder" && (
            <button
              className="btn primary"
              type="button"
              disabled={!pendingPath}
              onClick={() => pendingPath && onPick(pendingPath)}
            >
              Use this folder
            </button>
          )}
        </div>
      </div>
    </div>
  );
}

function joinPath(base: string, name: string): string {
  if (base.endsWith("/")) return `${base}${name}`;
  return `${base}/${name}`;
}

type Crumb = { label: string; path: string };

function splitPath(path: string): Crumb[] {
  if (!path.startsWith("/")) return [{ label: path, path }];
  const parts = path.split("/").filter((p) => p.length > 0);
  const crumbs: Crumb[] = [{ label: "/", path: "/" }];
  let acc = "";
  for (const part of parts) {
    acc = `${acc}/${part}`;
    crumbs.push({ label: part, path: acc });
  }
  return crumbs;
}
