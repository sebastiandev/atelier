import { useState } from "react";

import type { ContextEntry } from "./api";
import { FolderPickerDialog } from "./FolderPickerDialog";
import { FolderIcon } from "./Icons";

export type SimpleContextType = "text" | "url" | "file" | "folder";

export const SIMPLE_CONTEXT_PICKER_TYPES: ReadonlyArray<{
  id: SimpleContextType;
  label: string;
}> = [
  { id: "text", label: "Text" },
  { id: "url", label: "Link" },
  { id: "file", label: "File" },
  { id: "folder", label: "Folder" },
];

const SIMPLE_CONTEXT_TYPES: ReadonlySet<string> = new Set(
  SIMPLE_CONTEXT_PICKER_TYPES.map((type) => type.id),
);

export function isSimpleContextType(type: string): type is SimpleContextType {
  return SIMPLE_CONTEXT_TYPES.has(type);
}

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
    label: "Link",
    glyph: "LK",
    placeholder: "https://…",
  },
  file: {
    label: "File",
    glyph: "FL",
    placeholder: "/absolute/path/to/file",
  },
  folder: {
    label: "Folder",
    glyph: "FD",
    placeholder: "/absolute/path/to/folder",
  },
};

/**
 * Context row for unconnected text, link, file, and folder references. Stores the
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
        ) : type === "file" || type === "folder" ? (
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
              aria-label={`Browse for ${type}`}
              title="Browse"
            >
              {type === "folder" ? <FolderIcon size={14} /> : <FileIcon />}
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
          mode={type === "file" ? "file" : "folder"}
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
