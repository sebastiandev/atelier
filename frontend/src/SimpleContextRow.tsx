import type { ContextEntry } from "./api";

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
        ) : (
          <input
            className="input sm"
            placeholder={meta.placeholder}
            value={context.value}
            onChange={(e) => onChange({ ...context, value: e.target.value })}
          />
        )}
      </div>
    </div>
  );
}
