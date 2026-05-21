import { useState } from "react";

import type { ContextEntry } from "./api";
import { FolderPickerDialog } from "./FolderPickerDialog";

export type SimpleContextType = "text" | "url" | "file";

type Props = {
  context: ContextEntry;
  onChange: (next: ContextEntry) => void;
  onRemove: () => void;
};

const META: Record<SimpleContextType, { label: string; glyph: string; placeholder: string }> = {
  text: {
    label: "Text",
    glyph: "TX",
    placeholder: "Paste a snippet — notes, an error message, a stack trace…",
  },
  url: {
    label: "URL",
    glyph: "UR",
    placeholder: "https://…",
  },
  file: {
    label: "File",
    glyph: "FL",
    placeholder: "/absolute/path/to/file",
  },
};

/**
 * Context row for the unconnected types — text, url, file. Stores the
 * value verbatim on the ContextEntry; the backend renderer turns it into
 * a per-source markdown file under the agent's `context/` directory.
 */
export function SimpleContextRow({ context, onChange, onRemove }: Props) {
  const type = context.type as SimpleContextType;
  const meta = META[type];
  const [pickerOpen, setPickerOpen] = useState(false);

  return (
    <div className="context-card" data-source={type}>
      <div className="context-card-hd">
        <div className="ctx-type" data-source={type}>
          <span className="mono">{meta.glyph}</span>
          {meta.label}
        </div>
        <button
          type="button"
          className="rm"
          onClick={onRemove}
          aria-label={`Remove ${meta.label} context`}
          title="Remove"
        >
          ×
        </button>
      </div>
      <div className="context-card-bd">
        {type === "text" ? (
          <textarea
            className="textarea sm"
            rows={3}
            placeholder={meta.placeholder}
            value={context.value}
            onChange={(e) => onChange({ ...context, value: e.target.value })}
          />
        ) : type === "file" ? (
          <div className="folder-input-row">
            <input
              className="input sm"
              placeholder={meta.placeholder}
              value={context.value}
              onChange={(e) => onChange({ ...context, value: e.target.value })}
            />
            <button
              type="button"
              className="folder-input-pick"
              onClick={() => setPickerOpen(true)}
              aria-label="Browse for file"
              title="Browse"
            >
              <FileIcon />
            </button>
          </div>
        ) : (
          <input
            className="input sm"
            placeholder={meta.placeholder}
            value={context.value}
            onChange={(e) => onChange({ ...context, value: e.target.value })}
          />
        )}
      </div>
      {pickerOpen && (
        <FolderPickerDialog
          mode="file"
          initialPath={context.value || null}
          onCancel={() => setPickerOpen(false)}
          onPick={(path) => {
            onChange({ ...context, value: path });
            setPickerOpen(false);
          }}
        />
      )}
    </div>
  );
}

function FileIcon() {
  return (
    <svg
      width="14"
      height="14"
      viewBox="0 0 12 12"
      fill="none"
      stroke="currentColor"
      strokeWidth="1.2"
      strokeLinecap="round"
      strokeLinejoin="round"
      aria-hidden
    >
      <path d="M3 1.5h4l2.5 2.5v6a1 1 0 0 1-1 1H3a1 1 0 0 1-1-1v-7a1 1 0 0 1 1-1Z" />
      <path d="M7 1.5v2.5h2.5" />
    </svg>
  );
}
